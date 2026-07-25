"""Application factory. Wiring only: no business logic lives in this file."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from boxd_bridge import __version__
from boxd_bridge.auth.ratelimit import RateLimiter
from boxd_bridge.auth.session import SessionCodec
from boxd_bridge.config import AuthMode, Settings, get_settings
from boxd_bridge.routers import auth, export, health, pages

_PACKAGE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    async with httpx.AsyncClient(
        timeout=settings.request_timeout_seconds,
        follow_redirects=True,
        headers={"User-Agent": f"boxd-bridge/{__version__}"},
    ) as client:
        app.state.http_client = client
        yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="boxd-bridge",
        version=__version__,
        description="Export Plex / Tautulli watch history to a Letterboxd CSV.",
        lifespan=_lifespan,
    )

    app.state.settings = settings
    app.state.templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    app.state.rate_limiter = RateLimiter(
        settings.rate_limit_requests, settings.rate_limit_window_seconds
    )
    app.state.client_identifier = str(uuid.uuid4())
    app.state.session_codec = (
        SessionCodec(settings.session_secret or "", settings.session_ttl_seconds)
        if settings.auth_mode is AuthMode.PLEX_OAUTH
        else None
    )

    app.mount(
        "/static",
        StaticFiles(directory=str(_PACKAGE_DIR / "static")),
        name="static",
    )

    app.include_router(health.router)
    app.include_router(pages.router)
    app.include_router(export.router)
    if settings.auth_mode is AuthMode.PLEX_OAUTH:
        app.include_router(auth.router)

    return app
