"""The Information Radiator web app.

Run with ``uvicorn radiator.app:create_app --factory``. Behind the cluster
ingress, the login throttle reads each client's address from the
X-Forwarded-For entry the ingress adds (see ``auth.client_address``).
"""

import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from radiator import api, web
from radiator.auth import (
    LoginThrottle,
    check_credentials,
    client_address,
    is_logged_in,
)
from radiator.config import Settings
from radiator.db import make_sessionmaker

HERE = Path(__file__).parent
logger = logging.getLogger("radiator")


def configure_logging() -> None:
    """Send the radiator's own log lines (uploads, rejected uploads) to
    stderr next to uvicorn's, at RADIATOR_LOG_LEVEL (default INFO)."""
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(os.getenv("RADIATOR_LOG_LEVEL", "INFO").upper())
    logger.propagate = False


SESSION_MAX_AGE_S = 30 * 24 * 3600


def _safe_next(next_url: Optional[str]) -> str:
    """Only redirect back to a path on this site after login."""
    if not next_url:
        return "/"
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc or not next_url.startswith("/"):
        return "/"
    # browsers read "/\\host" like "//host", another site
    if next_url[1:2] in ("/", "\\"):
        return "/"
    return next_url


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    configure_logging()
    settings = settings or Settings.from_env()
    app = FastAPI(title="Information Radiator", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.sessionmaker = make_sessionmaker(settings.database_url)
    app.state.login_throttle = LoginThrottle()
    templates = Jinja2Templates(directory=HERE / "templates")
    web.register_template_helpers(templates)
    app.state.templates = templates

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="radiator_session",
        max_age=SESSION_MAX_AGE_S,
        same_site="lax",
        https_only=settings.secure_cookies,
    )
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.include_router(api.ingest_router)
    app.include_router(api.read_router)
    app.include_router(web.router)

    @app.exception_handler(RequestValidationError)
    async def rejected(request: Request, exc: RequestValidationError):
        """An upload the schema rejects is lost to the dashboard: say exactly
        what was wrong, here as well as in the harness's log."""
        if request.url.path.startswith(api.schema.INGEST_PREFIX):
            errors = exc.errors()
            details = "; ".join(
                f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg')}"
                for e in errors[:10]
            )
            more = f" (and {len(errors) - 10} more)" if len(errors) > 10 else ""
            logger.warning(
                f"Rejected upload {request.method} {request.url.path}: "
                f"{details}{more}"
            )
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(web.LoginRequired)
    async def login_required(request: Request, exc: web.LoginRequired):
        target = request.url.path
        if request.url.query:
            target += f"?{request.url.query}"
        return RedirectResponse(
            f"/login?next={quote(target, safe='/')}", status_code=303
        )

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        """Liveness/readiness: the app is up and can reach its database."""
        try:
            with app.state.sessionmaker() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page(request: Request, next: Optional[str] = None):
        if is_logged_in(request):
            return RedirectResponse(_safe_next(next), status_code=303)
        return templates.TemplateResponse(
            request, "login.html", {"next": _safe_next(next), "error": None}
        )

    @app.post("/login", include_in_schema=False)
    def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        next: Optional[str] = Form(None),
    ):
        throttle: LoginThrottle = app.state.login_throttle
        client = client_address(request)
        error = None
        if throttle.blocked(client):
            error = "Too many failed attempts. Try again in a few minutes."
        elif check_credentials(request, username, password):
            throttle.succeeded(client)
            request.session.clear()
            request.session["user"] = settings.username
            return RedirectResponse(_safe_next(next), status_code=303)
        else:
            throttle.failed(client)
            error = "That username and password didn't match."
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": _safe_next(next), "error": error},
            status_code=401,
        )

    @app.post("/logout", include_in_schema=False)
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    return app
