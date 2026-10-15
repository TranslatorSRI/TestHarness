"""Authentication.

Two ways in, and nothing is reachable without one of them (bar the health
check and the login page itself):

* the UI: one shared username/password for the consortium, which sets a
  signed session cookie;
* the API: a bearer token, for the harness to upload with and for scripts.

The JSON read API accepts either, so the UI's own pages could use it too.
"""

import hmac
import time
from collections import deque
from typing import Optional

from fastapi import HTTPException, Request, status


def _equal(a: Optional[str], b: str) -> bool:
    return a is not None and hmac.compare_digest(a.encode(), b.encode())


def has_valid_token(request: Request) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return scheme.lower() == "bearer" and _equal(
        token.strip(), request.app.state.settings.api_token
    )


def is_logged_in(request: Request) -> bool:
    return request.session.get("user") == request.app.state.settings.username


def require_token(request: Request) -> None:
    """Dependency for the ingest API: bearer token only."""
    if not has_valid_token(request):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "A valid bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_token_or_login(request: Request) -> None:
    """Dependency for the read API."""
    if not (has_valid_token(request) or is_logged_in(request)):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Log in, or send a bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def check_credentials(request: Request, username: str, password: str) -> bool:
    settings = request.app.state.settings
    # evaluate both, so a wrong username takes as long as a wrong password
    user_ok = _equal(username, settings.username)
    password_ok = _equal(password, settings.password)
    return user_ok and password_ok


def client_address(request: Request) -> str:
    """Who is logging in, for the throttle.

    Behind the ingress the peer is always the ingress, so the client comes
    from X-Forwarded-For. Only its last entry is trusted: that's the one the
    ingress itself adds, while anything before it is whatever the client sent.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


class LoginThrottle:
    """Slow down password guessing against the one shared password.

    Counts failed logins per client address in a sliding window, in memory.
    That is enough for the single replica this runs as; it resets on restart.
    """

    def __init__(
        self,
        max_failures: int = 10,
        window_s: float = 15 * 60,
        max_clients: int = 10_000,
    ):
        self.max_failures = max_failures
        self.window_s = window_s
        self.max_clients = max_clients
        self._failures: dict[str, deque] = {}

    def _recent(self, key: str, now: float) -> deque:
        failures = self._failures.get(key, deque())
        while failures and now - failures[0] > self.window_s:
            failures.popleft()
        return failures

    def blocked(self, key: str) -> bool:
        return len(self._recent(key, time.monotonic())) >= self.max_failures

    def failed(self, key: str) -> None:
        now = time.monotonic()
        failures = self._recent(key, now)
        failures.append(now)
        self._failures[key] = failures
        if len(self._failures) > self.max_clients:
            # keep memory bounded: forget whoever has nothing recent
            for other in list(self._failures):
                if not self._recent(other, now):
                    del self._failures[other]

    def succeeded(self, key: str) -> None:
        self._failures.pop(key, None)
