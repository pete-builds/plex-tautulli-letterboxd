"""Export endpoints: a JSON preview and the CSV download itself."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from boxd_bridge.config import AuthMode, Settings, SourceKind
from boxd_bridge.deps import (
    SESSION_COOKIE,
    build_source,
    get_http_client,
    get_settings_dep,
)
from boxd_bridge.export_service import build_export
from boxd_bridge.sources.base import SourceError
from boxd_bridge.sources.tautulli import TautulliSource
from boxd_bridge.transform.csv_export import part_filename
from boxd_bridge.transform.filters import InvalidSinceDate, parse_since

router = APIRouter(prefix="/api", tags=["export"])


def _session_for(request: Request, settings: Settings) -> dict[str, Any] | None:
    if settings.auth_mode is not AuthMode.PLEX_OAUTH:
        return None
    codec = request.app.state.session_codec
    from boxd_bridge.auth.session import SessionInvalid

    try:
        return codec.decode(request.cookies.get(SESSION_COOKIE))
    except SessionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in with Plex to continue.",
        ) from exc


def _parse_since_or_400(since: str | None) -> date | None:
    try:
        return parse_since(since)
    except InvalidSinceDate as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


async def _run_export(
    request: Request,
    settings: Settings,
    client: httpx.AsyncClient,
    user_id: str | None,
    since: date | None = None,
):
    session = _session_for(request, settings)
    # In hosted mode a visitor may only export their own history.
    effective_user = session.get("account_id") if session else user_id
    source = build_source(settings, client, session)
    try:
        return await build_export(
            source,
            tz=settings.tzinfo,
            user_id=effective_user,
            max_bytes=settings.csv_chunk_bytes,
            since=since,
        )
    except SourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc


def _today(settings: Settings) -> str:
    """Today in the display timezone: the sensible cutoff for the next run."""
    return datetime.now(settings.tzinfo).date().isoformat()


@router.get("/users")
async def list_users(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> dict[str, Any]:
    """Selectable users. Only meaningful for the Tautulli source in env mode."""
    if settings.auth_mode is not AuthMode.ENV or settings.source_kind is not SourceKind.TAUTULLI:
        return {"users": []}
    source = build_source(settings, client)
    assert isinstance(source, TautulliSource)
    try:
        return {"users": await source.list_users()}
    except SourceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc


@router.get("/preview")
async def preview(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    user_id: Annotated[str | None, Query(max_length=64)] = None,
    since: Annotated[str | None, Query(max_length=10)] = None,
) -> dict[str, Any]:
    since_date = _parse_since_or_400(since)
    result = await _run_export(request, settings, client, user_id, since_date)
    return {
        "rows": result.row_count,
        "total_rows": result.total_rows,
        "filtered_out": result.filtered_out,
        "since": since_date.isoformat() if since_date else None,
        "next_since": _today(settings),
        "parts": result.part_count,
        "rewatches": result.rewatch_count,
        "exact_id_matches": result.matched_count,
        "timezone": settings.display_timezone,
        "sample": [
            {
                "WatchedDate": row.watched_date,
                "Title": row.title,
                "Year": row.year,
                "tmdbID": row.tmdb_id,
                "imdbID": row.imdb_id,
                "Rewatch": row.rewatch,
            }
            for row in result.rows[:10]
        ],
    }


@router.get("/export.csv", response_class=PlainTextResponse)
async def export_csv(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
    user_id: Annotated[str | None, Query(max_length=64)] = None,
    since: Annotated[str | None, Query(max_length=10)] = None,
    part: Annotated[int, Query(ge=1, le=999)] = 1,
) -> PlainTextResponse:
    since_date = _parse_since_or_400(since)
    result = await _run_export(request, settings, client, user_id, since_date)
    if part > result.part_count:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"This export has {result.part_count} part(s).",
        )
    body = result.parts[part - 1]
    filename = part_filename("letterboxd", part - 1, result.part_count)
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Boxd-Parts": str(result.part_count),
        "X-Boxd-Rows": str(result.row_count),
        "X-Boxd-Total-Rows": str(result.total_rows),
        # The cutoff to use next time. We persist nothing, so this is how the
        # client learns where to resume.
        "X-Boxd-Next-Since": _today(settings),
    }
    if since_date:
        headers["X-Boxd-Since"] = since_date.isoformat()
    return PlainTextResponse(
        body, media_type="text/csv; charset=utf-8", headers=headers
    )
