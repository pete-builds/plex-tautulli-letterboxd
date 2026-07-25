"""Plex sign-in routes. Only mounted when ``AUTH_MODE=plex-oauth``."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from boxd_bridge.auth.plex_oauth import PlexAuthError, PlexOAuthClient, PlexPin
from boxd_bridge.auth.ratelimit import RateLimiter
from boxd_bridge.auth.session import SessionCodec, SessionInvalid
from boxd_bridge.config import Settings
from boxd_bridge.deps import (
    PIN_COOKIE,
    SESSION_COOKIE,
    client_ip,
    get_http_client,
    get_oauth_client,
    get_rate_limiter,
    get_session_codec,
    get_settings_dep,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_PIN_TTL_SECONDS = 900


def _set_cookie(
    response: Response,
    name: str,
    value: str,
    settings: Settings,
    max_age: int,
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/plex/start")
async def start_plex_login(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    oauth: Annotated[PlexOAuthClient, Depends(get_oauth_client)],
    codec: Annotated[SessionCodec, Depends(get_session_codec)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> RedirectResponse:
    if not limiter.allow(client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-in attempts. Try again in a few minutes.",
        )
    try:
        pin = await oauth.create_pin()
    except PlexAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    forward_url = f"{settings.public_base_url.rstrip('/')}/auth/plex/callback"
    response = RedirectResponse(
        oauth.build_auth_url(pin, forward_url), status_code=status.HTTP_303_SEE_OTHER
    )
    _set_cookie(
        response,
        PIN_COOKIE,
        codec.encode({"pin_id": pin.pin_id, "code": pin.code}),
        settings,
        _PIN_TTL_SECONDS,
    )
    return response


@router.get("/plex/callback")
async def plex_callback(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    oauth: Annotated[PlexOAuthClient, Depends(get_oauth_client)],
    codec: Annotated[SessionCodec, Depends(get_session_codec)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> RedirectResponse:
    try:
        pin_state = codec.decode(
            request.cookies.get(PIN_COOKIE), ttl_seconds=_PIN_TTL_SECONDS
        )
    except SessionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sign-in expired. Start again.",
        ) from exc

    pin = PlexPin(str(pin_state.get("pin_id")), str(pin_state.get("code")))
    try:
        token = await oauth.exchange_pin(pin)
        account = await oauth.validate_token(token)
        servers = await oauth.list_servers(
            token, prefer_local=settings.prefer_local_connections
        )
    except PlexAuthError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    if not servers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "No remotely reachable Plex server is available on this account. "
                "A server that is only reachable on its own LAN cannot be read "
                "from a hosted instance."
            ),
        )

    server = servers[0]
    payload = {
        "plex_token": token,
        "server_uri": server.uri,
        "server_token": server.access_token,
        "server_name": server.name,
        "machine_identifier": server.machine_identifier,
        "account_id": str(account.get("id") or "") or None,
        "username": account.get("username") or account.get("title"),
    }

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    _set_cookie(
        response, SESSION_COOKIE, codec.encode(payload), settings, codec.ttl_seconds
    )
    response.delete_cookie(PIN_COOKIE, path="/")
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(PIN_COOKIE, path="/")
    return response
