"""Server-rendered UI. One page, two states: signed out and ready to export."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from boxd_bridge.auth.session import SessionInvalid
from boxd_bridge.config import AuthMode, Settings, SourceKind
from boxd_bridge.deps import SESSION_COOKIE, get_settings_dep

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> HTMLResponse:
    session = None
    if settings.auth_mode is AuthMode.PLEX_OAUTH:
        codec = request.app.state.session_codec
        try:
            session = codec.decode(request.cookies.get(SESSION_COOKIE))
        except SessionInvalid:
            session = None

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "hosted": settings.auth_mode is AuthMode.PLEX_OAUTH,
            "signed_in": session is not None,
            "username": (session or {}).get("username"),
            "server_name": (session or {}).get("server_name"),
            "source": settings.source_kind.value,
            "timezone": settings.display_timezone,
            "threshold": settings.completion_threshold,
            "show_user_picker": (
                settings.auth_mode is AuthMode.ENV
                and settings.source_kind is SourceKind.TAUTULLI
            ),
        },
    )
