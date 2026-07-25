"""Source adapter tests.

Fixtures mirror the response shapes captured live on 2026-07-25 from a real
Tautulli instance and a real Plex Media Server 1.43.3, including the two traps
those probes turned up:

* Tautulli signals failure with HTTP 200 + ``response.result == "error"``
* Plex's history ``type`` filter does not reliably restrict the response
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from boxd_bridge.sources.base import SourceError
from boxd_bridge.sources.plex import PlexSource
from boxd_bridge.sources.tautulli import TautulliSource


def tautulli_history_row(**overrides):
    row = {
        "reference_id": 738,
        "row_id": 738,
        "id": 738,
        "date": 1770000000,
        "started": 1770000000,
        "stopped": 1770003600,
        "duration": 1932,
        "user_id": 424242,
        "user": "moviefan",
        "friendly_name": "moviefan",
        "media_type": "movie",
        "rating_key": 1001,
        "full_title": "Example Film",
        "title": "Example Film",
        "year": 2026,
        "guid": "plex://movie/000000000000000000000001",
        "percent_complete": 95,
        "watched_status": 1,
        "group_count": 1,
        "group_ids": "738",
    }
    row.update(overrides)
    return row


def tautulli_app(history_rows, *, metadata_guids=None, fail=False):
    if metadata_guids is None:
        metadata_guids = ["imdb://tt1234567", "tmdb://12345"]

    def handler(request: httpx.Request) -> httpx.Response:
        cmd = request.url.params.get("cmd")
        if fail:
            # Tautulli reports errors with a 200, which is the trap.
            return httpx.Response(
                200,
                json={"response": {"result": "error", "message": "Invalid apikey", "data": {}}},
            )
        if cmd == "get_history":
            start = int(request.url.params.get("start", 0))
            length = int(request.url.params.get("length", 500))
            page = history_rows[start : start + length]
            return httpx.Response(
                200,
                json={
                    "response": {
                        "result": "success",
                        "data": {
                            "recordsFiltered": len(history_rows),
                            "recordsTotal": len(history_rows),
                            "data": page,
                        },
                    }
                },
            )
        if cmd == "get_metadata":
            return httpx.Response(
                200,
                json={
                    "response": {
                        "result": "success",
                        "data": {"guids": metadata_guids, "title": "Example Film", "year": "2026"},
                    }
                },
            )
        if cmd == "get_users":
            return httpx.Response(
                200,
                json={
                    "response": {
                        "result": "success",
                        "data": [
                            {"user_id": 0, "username": "Local", "is_active": 1},
                            {"user_id": 424242, "username": "moviefan", "friendly_name": "moviefan", "is_active": 1},
                            {"user_id": 5550726, "username": "Guests", "is_active": 0},
                        ],
                    }
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def tautulli_source(rows, **kw):
    client = httpx.AsyncClient(transport=tautulli_app(rows, **kw))
    return TautulliSource("http://tautulli.test:8181", "key", client), client


async def test_tautulli_maps_verified_fields():
    source, client = tautulli_source([tautulli_history_row()])
    async with client:
        events = await source.fetch_movie_history()
    assert len(events) == 1
    event = events[0]
    assert event.title == "Example Film"
    assert event.year == 2026
    assert event.tmdb_id == "12345"
    assert event.imdb_id == "tt1234567"
    assert event.user_id == "424242"
    assert event.user_name == "moviefan"


async def test_tautulli_uses_stopped_not_started_for_the_date():
    """The date a viewer means is when the film finished."""
    source, client = tautulli_source([tautulli_history_row()])
    async with client:
        events = await source.fetch_movie_history()
    assert events[0].watched_at_utc == datetime.fromtimestamp(1770003600, tz=UTC)


async def test_tautulli_falls_back_to_date_when_stopped_is_zero():
    source, client = tautulli_source([tautulli_history_row(stopped=0)])
    async with client:
        events = await source.fetch_movie_history()
    assert events[0].watched_at_utc == datetime.fromtimestamp(1770000000, tz=UTC)


async def test_tautulli_drops_incomplete_watches():
    """percent_complete 29 was a real row: started and abandoned."""
    rows = [
        tautulli_history_row(percent_complete=29, watched_status=0.25, row_id=1),
        tautulli_history_row(percent_complete=95, row_id=2),
    ]
    source, client = tautulli_source(rows)
    async with client:
        events = await source.fetch_movie_history()
    assert len(events) == 1


async def test_tautulli_threshold_is_configurable():
    client = httpx.AsyncClient(transport=tautulli_app([tautulli_history_row(percent_complete=50)]))
    source = TautulliSource("http://t.test:8181", "k", client, completion_threshold=40)
    async with client:
        assert len(await source.fetch_movie_history()) == 1


async def test_tautulli_falls_back_to_watched_status_when_percent_missing():
    row = tautulli_history_row(watched_status=1)
    row.pop("percent_complete")
    source, client = tautulli_source([row])
    async with client:
        assert len(await source.fetch_movie_history()) == 1


async def test_tautulli_paginates():
    rows = [tautulli_history_row(row_id=i, rating_key=1001) for i in range(1200)]
    source, client = tautulli_source(rows)
    async with client:
        events = await source.fetch_movie_history()
    assert len(events) == 1200


async def test_tautulli_error_result_raises_despite_http_200():
    source, client = tautulli_source([], fail=True)
    async with client:
        with pytest.raises(SourceError, match="Invalid apikey"):
            await source.fetch_movie_history()


async def test_tautulli_missing_guids_leaves_ids_empty():
    source, client = tautulli_source([tautulli_history_row()], metadata_guids=[])
    async with client:
        events = await source.fetch_movie_history()
    assert events[0].tmdb_id is None
    assert events[0].imdb_id is None
    assert events[0].title == "Example Film"  # still exportable by fuzzy title match


async def test_tautulli_filters_non_movie_rows_defensively():
    source, client = tautulli_source(
        [tautulli_history_row(media_type="episode"), tautulli_history_row()]
    )
    async with client:
        assert len(await source.fetch_movie_history()) == 1


async def test_tautulli_list_users_excludes_inactive_and_local():
    source, client = tautulli_source([])
    async with client:
        users = await source.list_users()
    assert [u["user_id"] for u in users] == ["424242"]


# --------------------------------------------------------------------------
# Plex
# --------------------------------------------------------------------------


def plex_history_row(**overrides):
    row = {
        "accountID": 1,
        "ratingKey": "1001",
        "title": "Example Film",
        "type": "movie",
        "viewedAt": 1770003600,
        "year": 2026,
    }
    row.update(overrides)
    return row


def plex_transport(rows, *, guid=None, html_error=False):
    guid = guid if guid is not None else [{"id": "imdb://tt1234567"}, {"id": "tmdb://12345"}]

    def handler(request: httpx.Request) -> httpx.Response:
        if html_error:
            return httpx.Response(200, text="")
        if request.url.path == "/status/sessions/history/all":
            start = int(request.url.params.get("X-Plex-Container-Start", 0))
            size = int(request.url.params.get("X-Plex-Container-Size", 500))
            page = rows[start : start + size]
            return httpx.Response(
                200,
                json={
                    "MediaContainer": {
                        "size": len(page),
                        "totalSize": len(rows),
                        "offset": start,
                        "Metadata": page,
                    }
                },
            )
        if request.url.path.startswith("/library/metadata/"):
            return httpx.Response(
                200,
                json={"MediaContainer": {"Metadata": [{"Guid": guid, "year": 2026}]}},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_plex_filters_non_movies_client_side():
    """The server-side type filter is unreliable, so the adapter must filter."""
    rows = [
        plex_history_row(type="track", title="Some Song"),
        plex_history_row(type="episode", title="Some Episode"),
        plex_history_row(),
    ]
    client = httpx.AsyncClient(transport=plex_transport(rows))
    source = PlexSource("http://localhost:32400", "token", client)
    async with client:
        events = await source.fetch_movie_history()
    assert [e.title for e in events] == ["Example Film"]


async def test_plex_extracts_ids_from_the_guid_array():
    client = httpx.AsyncClient(transport=plex_transport([plex_history_row()]))
    source = PlexSource("http://localhost:32400", "token", client)
    async with client:
        events = await source.fetch_movie_history()
    assert events[0].tmdb_id == "12345"
    assert events[0].imdb_id == "tt1234567"
    assert events[0].watched_at_utc == datetime.fromtimestamp(1770003600, tz=UTC)


async def test_plex_empty_reply_gives_the_host_networking_hint():
    """Plex's host-header allowlist returns an empty body off-host."""
    client = httpx.AsyncClient(transport=plex_transport([], html_error=True))
    source = PlexSource("http://plex.example.com:32400", "token", client)
    async with client:
        with pytest.raises(SourceError, match="network_mode: host"):
            await source.fetch_movie_history()


async def test_plex_paginates():
    rows = [plex_history_row(ratingKey=str(i)) for i in range(1100)]
    client = httpx.AsyncClient(transport=plex_transport(rows))
    source = PlexSource("http://localhost:32400", "token", client)
    async with client:
        assert len(await source.fetch_movie_history()) == 1100
