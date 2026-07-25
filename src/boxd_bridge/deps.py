"""Shared FastAPI dependencies and per-request object construction."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request, status

from boxd_bridge.auth.plex_oauth import PlexOAuthClient
from boxd_bridge.auth.ratelimit import RateLimiter
from boxd_bridge.auth.session import SessionCodec, SessionInvalid
from boxd_bridge.config import AuthMode, Settings, SourceKind
from boxd_bridge.sources.base import WatchSource
from boxd_bridge.sources.plex import PlexSource
from boxd_bridge.sources.tautulli import TautulliSource
from boxd_bridge.transform.ratings import RatingPolicy

SESSION_COOKIE = "bb_session"
PIN_COOKIE = "bb_pin"

# Plex numbers the server owner as account 1; shared users get their plex.tv id.
PLEX_OWNER_ACCOUNT_ID = "1"


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


def get_session_codec(request: Request) -> SessionCodec:
    codec = request.app.state.session_codec
    if codec is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Sessions are only available in plex-oauth mode.",
        )
    return codec


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def get_oauth_client(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    request: Request,
) -> PlexOAuthClient:
    return PlexOAuthClient(
        client,
        product=settings.app_product_name,
        client_identifier=request.app.state.client_identifier,
    )


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def current_session(
    request: Request,
    codec: Annotated[SessionCodec, Depends(get_session_codec)],
) -> dict:
    try:
        return codec.decode(request.cookies.get(SESSION_COOKIE))
    except SessionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in with Plex to continue.",
        ) from exc


def build_source(
    settings: Settings,
    client: httpx.AsyncClient,
    session: dict | None = None,
) -> WatchSource:
    """Construct the source for this request.

    In hosted mode the server URL comes only from the session, which was
    populated from the plex.tv ``/resources`` response. A browser-supplied URL
    is never accepted anywhere in this function.
    """
    if settings.auth_mode is AuthMode.PLEX_OAUTH:
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sign in with Plex to continue.",
            )
        uri = session.get("server_uri")
        token = session.get("server_token") or session.get("plex_token")
        if not uri or not token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No Plex server selected for this session.",
            )
        return PlexSource(
            uri,
            token,
            client,
            account_id=session.get("account_id"),
            # The visitor authenticated as themselves, so the token that reads
            # ratings belongs to the person being exported. owner_user_id=None
            # means "trust every row".
            rating_policy=RatingPolicy(settings.export_ratings, None),
        )

    if settings.source_kind is SourceKind.TAUTULLI:
        return TautulliSource(
            settings.tautulli_url or "",
            settings.tautulli_apikey or "",
            client,
            completion_threshold=settings.completion_threshold,
            # owner_user_id is discovered from Tautulli's admin user, so ratings
            # attach only to the admin's own plays.
            rating_policy=RatingPolicy(settings.export_ratings, None),
        )
    return PlexSource(
        settings.plex_url or "",
        settings.plex_token or "",
        client,
        # Plex reports the server owner as accountID 1 (confirmed against
        # /accounts on a live server). A shared user's rows therefore never
        # inherit the owner's ratings.
        rating_policy=RatingPolicy(settings.export_ratings, PLEX_OWNER_ACCOUNT_ID),
    )
