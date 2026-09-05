"""SMTP listener, driven with the standard library's own client."""

from __future__ import annotations

import smtplib
import socket

from conftest import ZONE


def _send(port: int, sender: str, recipient: str, message: str = "Subject: hi\r\n\r\nbody\r\n") -> None:
    with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
        client.ehlo("scanner.invalid")
        client.sendmail(sender, [recipient], message)


def test_sender_domain_never_steals_the_attribution(service):
    """Regression: ``app@target.invalid`` used to outrank ``shop0031@<zone>`` because a
    sender domain was mined with the DNS-label rule."""
    _send(service.ports["smtp"], "app@target.invalid", f"shop0031@{ZONE}")
    (callback,) = service.store.wait_for(1, timeout=5)
    assert callback.token == "shop0031"


def test_envelope_localpart_carries_the_token(service):
    _send(service.ports["smtp"], "app@target.invalid", f"shop0031@{ZONE}")
    (callback,) = service.store.wait_for(1, timeout=5)
    assert (callback.channel, callback.token, callback.source) == (
        "smtp",
        "shop0031",
        "smtp_localpart",
    )
    assert callback.detail["rcpt_to"] == [f"shop0031@{ZONE}"]
    assert callback.detail["mail_from"] == "app@target.invalid"
    assert callback.in_zone is True


def test_address_domain_outranks_the_localpart(service):
    """``anything@shop0031.oob.bench.local`` hides the token where a DNS label would be,
    so rule 1 applies and rule 5 does not."""
    _send(service.ports["smtp"], "app@target.invalid", f"noreply@shop0031.{ZONE}")
    (callback,) = service.store.wait_for(1, timeout=5)
    assert (callback.token, callback.source) == ("shop0031", "dns_label")


def test_recipient_outranks_sender(service):
    """The payload controls RCPT TO; MAIL FROM is usually the target app's own domain,
    so it must never win the attribution."""
    _send(service.ports["smtp"], f"aaaa0001@{ZONE}", f"shop0031@{ZONE}")
    (callback,) = service.store.wait_for(1, timeout=5)
    assert callback.token == "shop0031"


def test_headers_are_logged_and_the_body_is_discarded(service):
    _send(
        service.ports["smtp"],
        "app@target.invalid",
        f"shop0031@{ZONE}",
        "Subject: catalogue import\r\nX-Mailer: target-app\r\n\r\nplease ignore\r\n",
    )
    (callback,) = service.store.wait_for(1, timeout=5)
    headers = callback.detail["headers"]
    assert "Subject: catalogue import" in headers
    assert "X-Mailer: target-app" in headers
    assert callback.detail["body_bytes"] > 0
    assert "please ignore" not in callback.raw  # the body is counted, never kept


def test_dynamic_form_over_smtp(service):
    _send(service.ports["smtp"], "app@target.invalid", f"shop0031-9f2c@{ZONE}")
    (callback,) = service.store.wait_for(1, timeout=5)
    assert (callback.token, callback.nonce) == ("shop0031", "9f2c")


def test_abandoned_transaction_is_still_evidence(service):
    """MAIL FROM + RCPT TO then a dropped connection: the payload already fired."""
    with socket.create_connection(("127.0.0.1", service.ports["smtp"]), timeout=5) as sock:
        assert sock.recv(1024).startswith(b"220 ")
        sock.sendall(b"HELO scanner.invalid\r\n")
        sock.recv(1024)
        sock.sendall(b"MAIL FROM:<app@target.invalid>\r\n")
        sock.recv(1024)
        sock.sendall(f"RCPT TO:<shop0031@{ZONE}>\r\n".encode())
        sock.recv(1024)
    (callback,) = service.store.wait_for(1, timeout=5)
    assert callback.token == "shop0031" and callback.detail["aborted"] is True


def test_starttls_and_auth_are_refused_without_killing_the_session(service):
    with socket.create_connection(("127.0.0.1", service.ports["smtp"]), timeout=5) as sock:
        sock.recv(1024)
        sock.sendall(b"STARTTLS\r\n")
        assert sock.recv(1024).startswith(b"502 ")
        sock.sendall(b"AUTH LOGIN\r\n")
        assert sock.recv(1024).startswith(b"502 ")
        sock.sendall(f"MAIL FROM:<a@b.invalid>\r\nRCPT TO:<shop0031@{ZONE}>\r\nQUIT\r\n".encode())
        sock.recv(4096)
    (callback,) = service.store.wait_for(1, timeout=5)
    assert callback.token == "shop0031"
