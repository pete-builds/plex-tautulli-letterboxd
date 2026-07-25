"""Server-rendered UI. One page, two states: signed out and ready to export."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

# Preset windows, in days. Resolved server-side so the dates are computed in the
# export's own timezone rather than the browser's, and so they are testable.
PRESET_DAYS: tuple[tuple[str, int], ...] = (
    ("Last 30 days", 30),
    ("Last 90 days", 90),
    ("Last year", 365),
)

CUSTOM_OPTION = "custom"


def build_since_options(today) -> list[dict[str, str]]:
    """Options whose *value is the resolved since date*.

    The browser does no date arithmetic. Previously it did, which meant the
    control could display one window while a different one went on the wire.
    "Last year" is 365 days back, not the start of the calendar year.
    """
    options = [{"label": "All time", "value": ""}]
    options += [
        {"label": label, "value": (today - timedelta(days=days)).isoformat()}
        for label, days in PRESET_DAYS
    ]
    options.append({"label": "Custom date…", "value": CUSTOM_OPTION})
    return options

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
            "since_options": build_since_options(
                datetime.now(settings.tzinfo).date()
            ),
        },
    )
