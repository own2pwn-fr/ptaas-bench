"""The service comes up, is provisioned, and serves its ordinary surface."""

from __future__ import annotations


def test_home_page_is_the_compiled_shell(stack):
    from pressroom.settings import settings

    response = stack.client.get("/")
    assert response.status_code == 200
    # The masthead, the counting site and the signing key are deployment data put into
    # the shell here, not compiled into a bundle that is deployed everywhere.
    assert settings().site_name in response.text
    assert settings().preview_signing_key in response.text
    for placeholder in ("__PRESSROOM_TITLE__", "__PRESSROOM_BOOTSTRAP__",
                        "__PRESSROOM_ANALYTICS__"):
        assert placeholder not in response.text


def test_the_ordinary_surface_answers(stack):
    for path in ("/api/status", "/api/config", "/api/articles", "/api/topics",
                 "/api/newsletter/issues", "/robots.txt", "/sitemap.xml",
                 "/feed.xml", "/feed.json", "/.well-known/security.txt",
                 "/openapi.json", "/humans.txt", "/opensearch.xml"):
        response = stack.client.get(path)
        assert response.status_code == 200, (path, response.status_code, response.text[:200])


def test_an_article_and_its_thread(stack):
    listed = stack.client.get("/api/articles").json()
    slug = listed["items"][0]["slug"]
    assert stack.client.get(f"/api/articles/{slug}").status_code == 200
    assert stack.client.get(f"/api/articles/{slug}/related").status_code == 200
    assert stack.client.get(f"/api/articles/{slug}/comments").status_code == 200


def test_signing_in_with_a_provisioned_account(stack):
    account = stack.account("author")
    stack.sign_in(account)
    who = stack.client.get("/api/auth/session").json()
    assert who["state"] == "authenticated"
    assert who["account"]["email"] == account["email"]


def test_nothing_is_counted_by_ordinary_traffic(stack):
    stack.client.get("/api/articles")
    stack.client.get("/api/topics")
    listed = stack.client.get("/api/articles").json()
    slug = listed["items"][0]["slug"]
    stack.client.get(f"/api/articles/{slug}")
    stack.client.get("/api/search", params={"q": "harbour ferry timetable"})
    stack.client.get("/api/authors", params={"status": "active"})
    stack.client.get("/api/comments", params={"article": slug})
    stack.client.post(f"/api/articles/{slug}/comments",
                      json={"body": "Good piece, the ferry point stands.", "name": "Ruth O."})
    assert stack.signals() == []
