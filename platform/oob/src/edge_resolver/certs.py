"""TLS material, minted at startup: a local issuing CA and per-name leaf certificates.

A single self-signed certificate would be a tell. Real internal edge nodes present a
certificate for the name the client asked for, issued by the organisation's own CA, and
that is what this does: an issuing CA is generated at startup, and a leaf is issued on
demand from the SNI name and cached. A client that asked for ``a.example.net`` gets a
certificate that says ``a.example.net``, chained to an issuer with an ordinary
infrastructure name. Validation still fails at the root, exactly as it would against any
private PKI the client does not have in its trust store.

Generating at startup rather than shipping keys in the image means no private key lives
in the repository and nothing expires between deployments.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import ssl
import tempfile
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

# Conservative: anything that is not a plain DNS name gets the default certificate
# rather than being embedded in a subject.
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62})(\.[a-z0-9]([a-z0-9-]{0,62}))*$")
MAX_CACHED_CONTEXTS = 256


def _write(path: str, data: bytes, mode: int = 0o600) -> None:
    with open(path, "wb") as handle:
        handle.write(data)
    os.chmod(path, mode)


class CertificateFactory:
    """Issues and caches leaf certificates, one per requested server name."""

    def __init__(self, ca_name: str, default_name: str, directory: str | None = None) -> None:
        self.default_name = default_name.lower()
        self.directory = directory or tempfile.mkdtemp(prefix="tls-")
        os.makedirs(self.directory, exist_ok=True)
        self._lock = threading.Lock()
        self._contexts: dict[str, ssl.SSLContext] = {}
        # Reverse map, so a listener can recover the name a client asked for: on the
        # server side of a handshake the SNI value is not exposed anywhere else, and one
        # context is built per name anyway.
        self._names: dict[int, str] = {}

        self._ca_key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ca_name)])
        now = dt.datetime.now(dt.timezone.utc)
        self._ca_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(self._ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=30))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(self._ca_key, hashes.SHA256())
        )
        self._ca_pem = self._ca_cert.public_bytes(serialization.Encoding.PEM)

    # -- issuing ---------------------------------------------------------------

    def issue(self, name: str) -> tuple[str, str]:
        """Write a leaf chain and key for ``name``; return their paths."""
        safe = name.lower()
        key = ec.generate_private_key(ec.SECP256R1())
        now = dt.datetime.now(dt.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, safe)]))
            .issuer_name(self._ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(hours=1))
            .not_valid_after(now + dt.timedelta(days=397))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(safe), x509.DNSName(f"*.{safe}")]),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .sign(self._ca_key, hashes.SHA256())
        )
        stem = re.sub(r"[^a-z0-9.-]", "_", safe)[:96] or "default"
        chain_path = os.path.join(self.directory, f"{stem}.pem")
        key_path = os.path.join(self.directory, f"{stem}.key")
        _write(
            chain_path,
            certificate.public_bytes(serialization.Encoding.PEM) + self._ca_pem,
            mode=0o644,
        )
        _write(
            key_path,
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ),
        )
        return chain_path, key_path

    # -- contexts --------------------------------------------------------------

    def context_for(self, name: str | None) -> ssl.SSLContext:
        wanted = (name or self.default_name).lower().strip(".")
        if not NAME_RE.match(wanted) or len(wanted) > 253:
            wanted = self.default_name
        with self._lock:
            cached = self._contexts.get(wanted)
            if cached is not None:
                return cached
        context = self._build_context(wanted)
        with self._lock:
            if len(self._contexts) >= MAX_CACHED_CONTEXTS:
                # Bounded: a client sweeping thousands of names must not grow us without
                # limit. Dropping the cache costs one key generation per name afterwards.
                self._contexts.clear()
                self._names.clear()
            self._contexts[wanted] = context
            self._names[id(context)] = wanted
        return context

    def name_for(self, context: ssl.SSLContext | None) -> str | None:
        """The server name a live connection negotiated, or None."""
        if context is None:
            return None
        with self._lock:
            return self._names.get(id(context))

    def _build_context(self, name: str) -> ssl.SSLContext:
        chain_path, key_path = self.issue(name)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(chain_path, key_path)
        # Old clients and old JREs still speak TLS 1.0/1.1; refusing them would turn a
        # connection we want to record into a handshake failure we never see.
        context.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
        try:
            context.set_ciphers("DEFAULT:@SECLEVEL=0")
        except ssl.SSLError:  # pragma: no cover - depends on the OpenSSL build
            pass
        return context

    def server_context(self) -> ssl.SSLContext:
        """Default context, with an SNI hook that swaps in a matching certificate."""
        context = self.context_for(self.default_name)

        def sni_callback(sslobject, server_name, _context):
            try:
                sslobject.context = self.context_for(server_name)
            except Exception:  # pragma: no cover - never fail a handshake over this
                pass

        context.sni_callback = sni_callback
        return context
