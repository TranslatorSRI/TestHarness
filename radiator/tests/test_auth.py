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
        ("/\\evil.example", "/"),
        ("javascript:alert(1)", "/"),
    ],
)
def test_login_only_redirects_on_site(next_url, expected):
    assert _safe_next(next_url) == expected


def test_throttle_trusts_only_the_ingress_entry(client):
    """Forging X-Forwarded-For can't dodge the throttle: only the last entry,
    the one the ingress adds, counts."""
    for i in range(10):
        client.post(
            "/login",
            data={"username": USERNAME, "password": "nope"},
            headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"},
        )
    res = client.post(
        "/login",
        data={"username": USERNAME, "password": PASSWORD},
        headers={"X-Forwarded-For": "198.51.100.1, 203.0.113.7"},
    )
    assert res.status_code == 401 and "Too many" in res.text
    # someone else, behind the same ingress, is unaffected
    other = client.post(
        "/login",
        data={"username": USERNAME, "password": PASSWORD},
        headers={"X-Forwarded-For": "203.0.113.8"},
        follow_redirects=False,
    )
    assert other.status_code == 303


def test_throttle_memory_is_bounded():
    from radiator.auth import LoginThrottle

    throttle = LoginThrottle(max_clients=100, window_s=0)
    for i in range(1000):
        throttle.failed(f"client-{i}")
    assert len(throttle._failures) <= 101


def test_login_returns_to_the_full_url(client):
    res = client.get("/runs/abc?status=FAILED&agent=arax", follow_redirects=False)
    target = res.headers["location"]
    page = client.get(target)
    assert 'value="/runs/abc?status=FAILED&amp;agent=arax"' in page.text
