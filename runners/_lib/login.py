"""Obtain a session for the tools that cannot log themselves in.

ZAP, wapiti and arachni can perform a login. nuclei and nikto cannot: the best they
offer is "send this cookie / this header on every request". Half the corpus is behind
a login, so without this module those two would be benchmarked anonymously against
authenticated targets and the comparison table would be measuring the harness, not
the tools.

So the harness logs in itself, from inside the network, and hands the resulting
cookie or bearer token to the driver in whatever dialect that tool speaks.

Two honesty requirements, both enforced here:

* the session is **verified** before the scan starts (fetch a known logged-in page
  and match the app's logged-in marker). An expired or rejected cookie would turn
  the run into an anonymous scan that still gets labelled "authenticated".
* the fact that the session was injected rather than negotiated is recorded in the
  run record, because it is a real methodological difference between the tools.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from .config import Credentials
from .internal_http import Http

log = logging.getLogger("bench.runners.login")


class LoginError(RuntimeError):
    """The harness could not obtain a verified session. Scanning anonymously while
    claiming to be authenticated is the one outcome we refuse."""


@dataclass
class Session:
    app: str
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    token: str | None = None
    verified: bool = False
    detail: str = ""

    @property
    def cookie_header(self) -> str | None:
        if not self.cookies:
            return None
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def as_headers(self) -> dict[str, str]:
        """Everything a dumb scanner needs to appear logged in."""
        out = dict(self.headers)
        cookie = self.cookie_header
        if cookie:
            out["Cookie"] = cookie
        return out

    def to_dict(self) -> dict[str, Any]:
        # Credentials never reach the run record; only the shape of the session does.
        return {
            "app": self.app,
            "verified": self.verified,
            "cookie_names": sorted(self.cookies),
            "header_names": sorted(self.headers),
            "detail": self.detail,
        }


def _parse_set_cookie(lines: list[str]) -> dict[str, str]:
    """Extract name=value from Set-Cookie lines, ignoring the attributes.

    A cookie jar would be more correct, but the harness only ever replays cookies to
    the host that set them, and pulling in a jar implementation for that is not worth
    the dependency.
    """
    jar: dict[str, str] = {}
    for line in lines:
        first = line.split(";", 1)[0].strip()
        if "=" not in first:
            continue
        name, _, value = first.partition("=")
        name, value = name.strip(), value.strip()
        # An expiring/blanking cookie must delete, not set an empty session.
        if value in ("", '""') and name in jar:
            jar.pop(name)
            continue
        if name:
            jar[name] = value
    return jar


def _dig(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def establish(
    http: Http, creds: Credentials, *, strict: bool = True, connect_to: str | None = None
) -> Session:
    """Log in and verify. Raises ``LoginError`` in strict mode when unverifiable.

    ``connect_to`` pins every request to one address (the target's internal one)
    while keeping the Host header, so that this traffic is classified as the
    platform's own rather than as the tool's. See _lib/internal_http.py.
    """
    session = Session(app=creds.app)
    login_response = None

    if creds.kind == "basic":
        raw = f"{creds.username}:{creds.password}".encode()
        session.headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        session.detail = "HTTP basic, no login request needed"
    else:
        if not creds.login_url:
            raise LoginError(f"{creds.app}: credentials have no login_url")
        # Some apps mint a CSRF cookie on the login page and reject a POST without it.
        pre_cookies: dict[str, str] = {}
        if creds.login_page_url:
            pre = http.request("GET", creds.login_page_url, timeout=30, connect_to=connect_to)
            pre_cookies = _parse_set_cookie(pre.cookies)

        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in pre_cookies.items())} if pre_cookies else {}
        if creds.kind == "json" or creds.kind == "bearer":
            res = http.request("POST", creds.login_url, json_body=creds.login_body(), headers=headers, timeout=60, connect_to=connect_to)
        else:
            res = http.request(
                "POST", creds.login_url, data=urlencode(creds.login_body()), headers=headers,
                timeout=60, connect_to=connect_to,
            )
        # A 302 to the dashboard is a successful form login; urllib follows it, so a
        # non-2xx here really is a failure.
        if not res.ok:
            raise LoginError(f"{creds.app}: login returned HTTP {res.status} {res.error or res.body[:200]}")

        login_response = res
        session.cookies = {**pre_cookies, **_parse_set_cookie(res.cookies)}
        if creds.token_json_path:
            try:
                token = _dig(json.loads(res.body or "{}"), creds.token_json_path)
            except json.JSONDecodeError:
                token = None
            if token is None:
                raise LoginError(
                    f"{creds.app}: no token at {creds.token_json_path!r} in the login response"
                )
            session.token = str(token)
        if creds.session in ("bearer", "header"):
            if not session.token:
                raise LoginError(f"{creds.app}: session={creds.session} requires token_json_path")
            session.headers[creds.header_name] = creds.header_template.format(token=session.token)
        session.detail = f"{creds.kind} login at {creds.login_url}"

    # The target contract carries indicators but no verification URL, because for a
    # normal login the proof is in the login response itself. So: verify against a
    # dedicated URL when one is configured, otherwise against the body the login
    # returned. Only when neither is available is the session unverifiable.
    if creds.verify_url:
        check = http.request("GET", creds.verify_url, headers=session.as_headers(), timeout=30, connect_to=connect_to)
        where = creds.verify_url
    elif creds.logged_in_regex or creds.logged_out_regex:
        check = login_response
        where = "the login response"
    else:
        session.verified = False
        session.detail += "; NOT VERIFIED (no verify_url and no logged-in indicator)"
        if strict:
            raise LoginError(
                f"{creds.app}: nothing to verify the session against. An unverified "
                "session lets a silently anonymous scan be published as authenticated; "
                "give the credentials file a logged_in_indicator or a verify_url."
            )
        return session

    if check is None:  # basic auth with no verify_url and no indicators
        session.verified = False
        return session

    marker_ok = check.ok
    if marker_ok and creds.logged_in_regex:
        marker_ok = re.search(creds.logged_in_regex, check.body) is not None
    if marker_ok and creds.logged_out_regex:
        marker_ok = re.search(creds.logged_out_regex, check.body) is None

    session.verified = marker_ok
    session.detail += f"; verified against {where} -> HTTP {check.status}"
    if not marker_ok and strict:
        raise LoginError(
            f"{creds.app}: session did not verify against {where} (HTTP {check.status}); "
            "refusing to run an 'authenticated' scan that is not one"
        )
    return session
