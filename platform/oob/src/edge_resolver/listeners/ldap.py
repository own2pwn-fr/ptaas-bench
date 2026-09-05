"""LDAP listener: enough of LDAPv3 to record the DN a directory lookup asks for.

A Java naming lookup (``${jndi:ldap://host/x}`` and relatives) produces two observable
events: the DNS resolution, and an LDAP connection whose BindRequest or SearchRequest
carries the path from the URL. This listener answers both operations successfully so the
client gets past its first round-trip and actually sends the search, instead of aborting
on a protocol error before we learn anything.

Implemented: LDAPMessage framing, BindRequest (any auth choice -- simple, SASL, empty --
answered success without ever looking at the credential), SearchRequest (only the
baseObject is read), UnbindRequest, ExtendedRequest (answered success). Responses are
BindResponse, SearchResultEntry and SearchResultDone.

NOT implemented, on purpose:

* the search filter, scope, attribute list and size/time limits are parsed past, not
  honoured. Every search returns the same single entry, whatever was asked for.
* there is no directory behind this. The entry we return is the baseObject itself with
  one inert attribute.
* we never return a naming reference: no ``javaClassName``, no ``javaCodebase``, no
  ``javaSerializedData``, no ``javaFactory``. A Java client therefore gets a clean search
  result and then fails to materialise an object, which is the outcome we want -- this
  service records requests, and helping a payload reach code execution in the client
  would be indefensible.
* no StartTLS, no LDAPS, no SASL exchange, no controls, no referrals, no paged results,
  no abandon, no modify/add/delete.
"""

from __future__ import annotations

import asyncio
import logging

from .. import ber
from ..config import Config
from ..recorder import Recorder
from ..tokens import Candidate, dn_candidates

log = logging.getLogger("edge_resolver.ldap")

MAX_MESSAGE = 65536


class LdapHandler:
    def __init__(self, config: Config, recorder: Recorder) -> None:
        self.config = config
        self.recorder = recorder

    async def __call__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        peer_ip = peer[0]
        saw_bytes = False
        recorded = False
        try:
            while True:
                message = await _read_message(reader)
                if message is None:
                    break
                saw_bytes = True
                try:
                    response, did_record = self._dispatch(message, peer_ip)
                except ber.BerError as exc:
                    self.recorder.record(
                        channel="ldap",
                        source_ip=peer_ip,
                        candidates=[],
                        raw=f"ldap unparseable message ({exc}): {message[:256].hex()}",
                        detail={"malformed": True},
                        owned_zone=None,
                    )
                    recorded = True
                    break
                recorded = recorded or did_record
                if response is None:
                    break
                writer.write(response)
                await writer.drain()
            if saw_bytes and not recorded:
                self.recorder.record(
                    channel="ldap",
                    source_ip=peer_ip,
                    candidates=[],
                    raw="ldap connection with no DN-bearing operation",
                    detail={"empty": True},
                    owned_zone=None,
                )
        except (ConnectionError, asyncio.CancelledError):
            raise
        except Exception:  # pragma: no cover
            log.exception("ldap handling failed")
        finally:
            try:
                writer.close()
            except Exception:  # pragma: no cover
                pass

    def _dispatch(self, message: bytes, peer_ip: str) -> tuple[bytes | None, bool]:
        envelope = ber.read_tlv(message)
        if envelope.tag != ber.TAG_SEQUENCE:
            raise ber.BerError(f"LDAPMessage is not a SEQUENCE (tag 0x{envelope.tag:02x})")
        parts = list(ber.iter_tlv(envelope.content))
        if len(parts) < 2:
            raise ber.BerError("LDAPMessage without a protocol op")
        message_id = parts[0].int
        op = parts[1]

        if op.tag == ber.APP_UNBIND_REQUEST:
            return None, False  # no response is defined for unbind; just close

        if op.tag == ber.APP_BIND_REQUEST:
            fields = list(ber.iter_tlv(op.content))
            version = fields[0].int if fields else 0
            dn = fields[1].text if len(fields) > 1 else ""
            self._record(peer_ip, "bind", dn, {"version": version})
            body = ber.tlv(
                ber.APP_BIND_RESPONSE, ber.enc_enum(0) + ber.enc_str("") + ber.enc_str("")
            )
            return ber.enc_seq(ber.enc_int(message_id), body), True

        if op.tag == ber.APP_SEARCH_REQUEST:
            fields = list(ber.iter_tlv(op.content))
            base = fields[0].text if fields else ""
            scope = fields[1].int if len(fields) > 1 else -1
            self._record(peer_ip, "search", base, {"scope": scope})
            entry = ber.tlv(
                ber.APP_SEARCH_ENTRY,
                ber.enc_str(base) + ber.enc_seq(_attribute("objectClass", "top")),
            )
            done = ber.tlv(
                ber.APP_SEARCH_DONE, ber.enc_enum(0) + ber.enc_str("") + ber.enc_str("")
            )
            return (
                ber.enc_seq(ber.enc_int(message_id), entry)
                + ber.enc_seq(ber.enc_int(message_id), done),
                True,
            )

        if op.tag == ber.APP_EXTENDED_REQUEST:
            self._record(peer_ip, "extended", "", {})
            body = ber.tlv(
                ber.APP_EXTENDED_RESPONSE, ber.enc_enum(0) + ber.enc_str("") + ber.enc_str("")
            )
            return ber.enc_seq(ber.enc_int(message_id), body), True

        self._record(peer_ip, f"op-0x{op.tag:02x}", "", {})
        return None, True

    def _record(self, peer_ip: str, operation: str, dn: str, detail: dict) -> None:
        candidates: list[Candidate] = list(dn_candidates(dn))
        self.recorder.record(
            channel="ldap",
            source_ip=peer_ip,
            candidates=candidates,
            raw=f"ldap {operation} dn={dn!r} " + " ".join(f"{k}={v}" for k, v in detail.items()),
            detail={"operation": operation, "dn": dn, **detail},
            # Plain LDAP carries no server name, so attribution here rests on the source
            # address and on any hint registered for the host that was resolved first.
            owned_zone=None,
        )


def _attribute(name: str, value: str) -> bytes:
    return ber.enc_seq(ber.enc_str(name), ber.enc_set(ber.enc_str(value)))


async def _read_message(reader: asyncio.StreamReader, timeout: float = 30.0) -> bytes | None:
    """Read exactly one LDAPMessage: a SEQUENCE header, then its definite length."""
    try:
        head = await asyncio.wait_for(reader.readexactly(2), timeout)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, TimeoutError):
        return None
    first = head[1]
    if first < 0x80:
        length, prefix = first, head
    elif first == 0x80:
        return None  # indefinite length: not legal in LDAP, refuse to guess
    else:
        count = first & 0x7F
        if count > 4:
            return None
        try:
            extra = await asyncio.wait_for(reader.readexactly(count), timeout)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, TimeoutError):
            return None
        length, prefix = int.from_bytes(extra, "big"), head + extra
    if length > MAX_MESSAGE:
        return None
    try:
        body = await asyncio.wait_for(reader.readexactly(length), timeout)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, TimeoutError):
        return None
    return prefix + body
