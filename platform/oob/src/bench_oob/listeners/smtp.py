"""SMTP listener: accept the transaction, log the envelope and headers, discard the mail.

What it implements: the ESMTP greeting, EHLO/HELO, MAIL FROM, RCPT TO, DATA (read to
the ``<CRLF>.<CRLF>`` terminator), RSET, NOOP, VRFY, QUIT. That is everything a payload
of the form "make the app mail <token>@oob.bench.local" needs to reach a 250.

What it does NOT implement, and never will: relaying or delivery of any kind, AUTH
(answered 502, senders proceed unauthenticated), STARTTLS (also 502 -- offering it and
then failing the handshake would lose the callback), SIZE/8BITMIME semantics beyond
advertising them, BDAT/CHUNKING, address or header validation, and MIME parsing. The
message body is counted and thrown away; only the headers are kept.

One callback per mail transaction, flushed when DATA completes -- or when the
connection ends with an envelope pending, since a payload that sends MAIL/RCPT and then
disconnects has still proven the vulnerability.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import Config
from ..recorder import Recorder
from ..tokens import Candidate, address_parts, host_label

log = logging.getLogger("bench_oob.smtp")

MAX_LINE = 4096
MAX_MESSAGE = 262144  # 256 KiB kept in memory; the rest is drained and dropped.


class SmtpHandler:
    def __init__(self, config: Config, recorder: Recorder) -> None:
        self.config = config
        self.recorder = recorder

    async def __call__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        peer_ip = peer[0]
        domain = self.config.domain
        mail_from: str | None = None
        rcpts: list[str] = []
        helo: str | None = None

        def reset_envelope() -> None:
            nonlocal mail_from, rcpts
            mail_from, rcpts = None, []

        async def send(line: str) -> None:
            writer.write(line.encode("utf-8", "replace") + b"\r\n")
            await writer.drain()

        try:
            await send(f"220 {domain} ESMTP bench-oob")
            while True:
                line = await _read_line(reader)
                if line is None:
                    break
                command, _, argument = line.partition(" ")
                verb = command.strip().upper()
                argument = argument.strip()

                if verb in ("EHLO", "HELO"):
                    helo = argument
                    if verb == "EHLO":
                        await send(f"250-{domain} greets {argument or 'unknown'}")
                        await send("250-8BITMIME")
                        await send(f"250 SIZE {MAX_MESSAGE}")
                    else:
                        await send(f"250 {domain}")
                elif verb == "MAIL":
                    mail_from = _extract_address(argument, "FROM:")
                    await send("250 2.1.0 sender ok")
                elif verb == "RCPT":
                    recipient = _extract_address(argument, "TO:")
                    if recipient:
                        rcpts.append(recipient)
                    await send("250 2.1.5 recipient ok")
                elif verb == "DATA":
                    await send("354 end data with <CR><LF>.<CR><LF>")
                    headers, size = await _read_message(reader)
                    self._record(peer_ip, mail_from, rcpts, helo, headers, size)
                    reset_envelope()
                    await send("250 2.0.0 accepted and discarded")
                elif verb == "RSET":
                    reset_envelope()
                    await send("250 2.0.0 ok")
                elif verb == "NOOP":
                    await send("250 2.0.0 ok")
                elif verb == "VRFY":
                    await send("252 2.5.2 cannot verify, will attempt delivery")
                elif verb in ("STARTTLS", "AUTH"):
                    # Refused rather than faked: a half-done TLS upgrade or SASL
                    # exchange would abort the client before it ever sends RCPT TO.
                    await send(f"502 5.5.1 {verb.lower()} not implemented")
                elif verb == "QUIT":
                    await send(f"221 2.0.0 {domain} closing connection")
                    break
                else:
                    # Deliberately permissive: an unknown verb from an odd client is
                    # not worth losing a callback over.
                    await send("250 2.0.0 ok")

        except (ConnectionError, asyncio.CancelledError):
            raise
        except Exception:  # pragma: no cover
            log.exception("smtp handler failed")
        finally:
            # In the finally block on purpose: a client that sends QUIT and slams the
            # connection shut makes our final write raise, and the callback it already
            # proved must survive that.
            if mail_from or rcpts:
                self._record(peer_ip, mail_from, rcpts, helo, [], 0, aborted=True)
            try:
                writer.close()
            except Exception:  # pragma: no cover
                pass

    def _record(
        self,
        peer_ip: str,
        mail_from: str | None,
        rcpts: list[str],
        helo: str | None,
        headers: list[str],
        size: int,
        aborted: bool = False,
    ) -> None:
        zone = self.config.zone
        candidates: list[Candidate] = []
        in_zone: bool | None = None
        for address in rcpts:
            local, domain = address_parts(address)
            if domain:
                inside = domain == zone or domain.endswith("." + zone)
                in_zone = True if inside else (False if in_zone is None else in_zone)
                # A recipient domain inside our zone is a DNS name carrying the token in
                # the same place a DNS query would, so it is mined with rule 1. A foreign
                # one is kept, but ranked below every real rule.
                candidates.append(
                    Candidate("dns_label" if inside else "smtp_domain", host_label(domain, zone) or "")
                )
            if local:
                candidates.append(Candidate("smtp_localpart", local))
        if mail_from:
            local, domain = address_parts(mail_from)
            candidates.append(Candidate("smtp_sender", local or ""))
            candidates.append(Candidate("smtp_sender", host_label(domain, zone) or ""))
        # Order within a rank is preserved by extract(), which keeps the first recipient
        # ahead of the later ones.
        raw_lines = [
            f"smtp helo={helo or '-'} from=<{mail_from or ''}> "
            f"rcpt={','.join(f'<{r}>' for r in rcpts) or '-'} "
            f"body_bytes={size}{' aborted' if aborted else ''}"
        ]
        raw_lines.extend(headers)
        self.recorder.record(
            channel="smtp",
            source_ip=peer_ip,
            candidates=candidates,
            raw="\n".join(raw_lines),
            detail={
                "helo": helo,
                "mail_from": mail_from,
                "rcpt_to": rcpts,
                "headers": headers[:32],
                "body_bytes": size,
                "aborted": aborted,
            },
            in_zone=in_zone,
        )


def _extract_address(argument: str, prefix: str) -> str | None:
    """Pull the address out of ``FROM:<a@b> SIZE=123`` and friends."""
    text = argument.strip()
    if text.upper().startswith(prefix):
        text = text[len(prefix) :].strip()
    if text.startswith("<"):
        end = text.find(">")
        if end != -1:
            return text[1:end].strip() or None
        return text[1:].strip() or None
    return text.split(" ", 1)[0].strip() or None


async def _read_line(reader: asyncio.StreamReader, timeout: float = 30.0) -> str | None:
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout)
    except (asyncio.TimeoutError, TimeoutError, ValueError, asyncio.LimitOverrunError):
        return None
    if not raw:
        return None
    return raw[:MAX_LINE].decode("utf-8", "replace").rstrip("\r\n")


async def _read_message(reader: asyncio.StreamReader) -> tuple[list[str], int]:
    """Read to the dot terminator; return the headers and the total byte count."""
    headers: list[str] = []
    in_headers = True
    size = 0
    while True:
        try:
            raw = await asyncio.wait_for(reader.readline(), 30.0)
        except (asyncio.TimeoutError, TimeoutError, ValueError, asyncio.LimitOverrunError):
            break
        if not raw:
            break
        size += len(raw)
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line == ".":
            break
        if in_headers:
            if line == "":
                in_headers = False
            elif len(headers) < 64:
                headers.append(line[:512])
        if size > MAX_MESSAGE:
            # Keep draining to the terminator so the client sees a clean 250, but stop
            # accumulating: a mail bomb must not grow the canary's memory.
            in_headers = False
    return headers, size
