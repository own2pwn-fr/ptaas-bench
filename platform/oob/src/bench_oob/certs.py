"""Self-signed certificate, minted at startup.

Nobody validates this certificate: the point of the HTTPS listener is that a payload
pointing at ``https://<token>.oob.bench.local/`` still produces a callback, and every
client that would reach it either ignores verification errors or fails after the TLS
ClientHello -- by which time we have already logged the connection. Generating it at
startup rather than shipping one in the image means no private key lives in git and no
certificate ever expires mid-benchmark.
"""

from __future__ import annotations

import datetime as dt
import os
import ssl
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def generate_self_signed(domain: str, directory: str | None = None) -> tuple[str, str]:
    """Write a cert/key pair for ``domain`` and ``*.domain``; return their paths."""
    target = directory or tempfile.mkdtemp(prefix="bench-oob-tls-")
    os.makedirs(target, exist_ok=True)
    cert_path = os.path.join(target, "oob.crt")
    key_path = os.path.join(target, "oob.key")

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    now = dt.datetime.now(dt.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(domain), x509.DNSName(f"*.{domain}")]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    with open(key_path, "wb") as handle:
        handle.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    os.chmod(key_path, 0o600)
    with open(cert_path, "wb") as handle:
        handle.write(certificate.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def server_context(domain: str, directory: str | None = None) -> ssl.SSLContext:
    cert_path, key_path = generate_self_signed(domain, directory)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    # Old scanners and old JREs still speak TLS 1.0/1.1; refusing them would turn a
    # callback we want to record into a handshake failure we never see. MINIMUM_SUPPORTED
    # defers to whatever this OpenSSL build still allows, instead of naming a version
    # that a future Python will have dropped.
    context.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
    try:
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
    except ssl.SSLError:  # pragma: no cover - depends on the OpenSSL build
        pass
    return context
