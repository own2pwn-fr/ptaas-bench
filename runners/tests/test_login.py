"""Session establishment for the scanners that cannot log in by themselves.

The failure this file guards against is the quiet one: a scan that runs
anonymously and is published as authenticated. Half the corpus sits behind a login,
so that mistake would not look like a bug, it would look like every tool being blind
to half the vulnerabilities.
"""

from __future__ import annotations

import json

import pytest

from runners._lib.config import Credentials
from runners._lib.internal_http import Response
from runners._lib.login import LoginError, establish

from fakes import FakeHttp

LOGIN = "http://app/login"
VERIFY = "http://app/me"


def form_creds(**kw):
    base = dict(
        app="app", kind="form", login_url=LOGIN, login_page_url=LOGIN,
        username="jdoe", password="s3cr3t", verify_url=VERIFY, logged_in_regex="Sign out",
    )
    base.update(kw)
    return Credentials(**base)


def test_form_login_collects_cookies_and_verifies():
    http = FakeHttp({
        LOGIN: Response(200, "<form>", cookies=["sid=abc; Path=/; HttpOnly", "csrf=xyz"]),
        VERIFY: Response(200, "<a>Sign out</a>"),
    })
    session = establish(http, form_creds())
    assert session.verified
    assert session.cookies == {"sid": "abc", "csrf": "xyz"}
    assert session.as_headers()["Cookie"] == "sid=abc; csrf=xyz"


def test_multiple_set_cookie_headers_are_all_kept():
    """A dict of headers collapses them into one; a login usually sets two."""
    http = FakeHttp({
        LOGIN: Response(200, "", cookies=["sid=abc", "tenant=42", "sid=abc"]),
        VERIFY: Response(200, "Sign out"),
    })
    assert establish(http, form_creds()).cookies == {"sid": "abc", "tenant": "42"}


def test_bearer_login_extracts_the_token_and_builds_the_header():
    http = FakeHttp({
        LOGIN: Response(200, json.dumps({"data": {"access_token": "eyJ0"}})),
        VERIFY: Response(200, '{"customer_id": 7}'),
    })
    creds = form_creds(
        kind="json", session="bearer", token_json_path="data.access_token",
        logged_in_regex='"customer_id"',
    )
    session = establish(http, creds)
    assert session.token == "eyJ0"
    assert session.headers["Authorization"] == "Bearer eyJ0"


def test_basic_auth_needs_no_login_request():
    http = FakeHttp({VERIFY: Response(200, "Sign out")})
    session = establish(http, form_creds(kind="basic"))
    assert session.headers["Authorization"].startswith("Basic ")
    assert not any(url == LOGIN for _, url in http.requests)


def test_a_rejected_login_is_fatal():
    http = FakeHttp({LOGIN: Response(401, "bad credentials")})
    with pytest.raises(LoginError, match="HTTP 401"):
        establish(http, form_creds())


def test_a_session_that_does_not_verify_is_fatal():
    """The whole point: an unverified session must never be labelled authenticated."""
    http = FakeHttp({
        LOGIN: Response(200, "", cookies=["sid=abc"]),
        VERIFY: Response(200, "Please log in"),  # logged_in_regex absent
    })
    with pytest.raises(LoginError, match="did not verify"):
        establish(http, form_creds())


def test_logged_out_marker_also_fails_verification():
    http = FakeHttp({
        LOGIN: Response(200, "", cookies=["sid=abc"]),
        VERIFY: Response(200, "Sign out ... session expired"),
    })
    creds = form_creds(logged_out_regex="session expired")
    with pytest.raises(LoginError, match="did not verify"):
        establish(http, creds)


def test_the_login_response_itself_verifies_when_there_is_no_verify_url():
    """The target contract carries indicators but no verification URL: for a normal
    login the proof is in the response the login returned."""
    http = FakeHttp({LOGIN: Response(200, "<p>Welcome back, jdoe</p>", cookies=["sid=abc"])})
    session = establish(http, form_creds(verify_url=None, logged_in_regex="Welcome back"))
    assert session.verified
    assert "the login response" in session.detail
    # And it did not invent a URL to fetch.
    assert [url for _, url in http.requests].count(LOGIN) == 2  # page GET + login POST


def test_a_login_response_missing_the_indicator_is_still_a_failure():
    http = FakeHttp({LOGIN: Response(200, "Those details did not match", cookies=["sid=abc"])})
    with pytest.raises(LoginError, match="did not verify"):
        establish(http, form_creds(verify_url=None, logged_in_regex="Welcome back"))


def test_credentials_with_nothing_to_verify_against_are_rejected():
    http = FakeHttp({LOGIN: Response(200, "", cookies=["sid=abc"])})
    with pytest.raises(LoginError, match="nothing to verify"):
        establish(http, form_creds(verify_url=None, logged_in_regex=None))


def test_non_strict_mode_records_the_doubt_instead_of_hiding_it():
    http = FakeHttp({
        LOGIN: Response(200, "", cookies=["sid=abc"]),
        VERIFY: Response(200, "Please log in"),
    })
    session = establish(http, form_creds(), strict=False)
    assert session.verified is False
    assert "verified against" in session.detail


def test_the_session_summary_never_carries_the_password():
    """run.json is published; the shape of the session is useful, the secret is not."""
    http = FakeHttp({
        LOGIN: Response(200, "", cookies=["sid=abc"]),
        VERIFY: Response(200, "Sign out"),
    })
    summary = establish(http, form_creds()).to_dict()
    assert "s3cr3t" not in json.dumps(summary)
    assert summary["cookie_names"] == ["sid"]


def test_requests_can_be_pinned_to_one_address():
    """A dual-homed target answers to the same name on both networks; the interface
    decides whether our own login is recorded as the platform's traffic or the tool's."""
    http = FakeHttp({
        LOGIN: Response(200, "", cookies=["sid=abc"]),
        VERIFY: Response(200, "Sign out"),
    })
    establish(http, form_creds(), connect_to="10.77.0.9")
    assert set(http.connected_to) == {"10.77.0.9"}
