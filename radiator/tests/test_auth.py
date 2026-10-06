"""Nothing is reachable without logging in or a token."""

import pytest

from radiator.app import _safe_next

from .conftest import PASSWORD, USERNAME


def test_health_check_needs_no_auth(client):
    assert client.get("/healthz").json() == {"ok": True}


@pytest.mark.parametrize("path", ["/", "/trends", "/performance"])
def test_pages_redirect_to_login(client, path):
    res = client.get(path, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == f"/login?next={path}"


def test_read_api_needs_token_or_login(client, api, browser):
    assert client.get("/api/runs").status_code == 401
    assert api.get("/api/runs").status_code == 200
    assert browser.get("/api/runs").status_code == 200


def test_ingest_needs_the_token(client, browser):
    body = {
        "run_id": "6e3c1d8e-5f0e-4bb8-9d7e-1f1e0b6f0a11",
        "suite": "s",
        "started_at": "2026-10-06T00:00:00Z",
    }
    assert client.post("/api/ingest/runs", json=body).status_code == 401
    bad = client.post(
        "/api/ingest/runs", json=body, headers={"Authorization": "Bearer nope"}
    )
    assert bad.status_code == 401
    # a logged-in browser still can't write: ingest is token-only
    assert browser.post("/api/ingest/runs", json=body).status_code == 401


def test_login_and_logout(client):
    res = client.post(
        "/login",
        data={"username": USERNAME, "password": PASSWORD, "next": "/trends"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == "/trends"
    assert client.get("/", follow_redirects=False).status_code == 200

    client.post("/logout", follow_redirects=False)
    assert client.get("/", follow_redirects=False).status_code == 303


def test_wrong_password(client):
    res = client.post("/login", data={"username": USERNAME, "password": "nope"})
    assert res.status_code == 401
    assert "didn" in res.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_is_throttled(client):
    for _ in range(10):
        client.post("/login", data={"username": USERNAME, "password": "nope"})
    # even the right password is refused once the address is throttled
    res = client.post("/login", data={"username": USERNAME, "password": PASSWORD})
    assert res.status_code == 401
    assert "Too many" in res.text


@pytest.mark.parametrize(
    "next_url, expected",
    [
        (None, "/"),
        ("/runs/abc?x=1", "/runs/abc?x=1"),
        ("https://evil.example", "/"),
        ("//evil.example", "/"),
        ("javascript:alert(1)", "/"),
    ],
)
def test_login_only_redirects_on_site(next_url, expected):
    assert _safe_next(next_url) == expected
