"""Plex Media Server history adapter.

Verified live against a real PMS (1.43.3) on 2026-07-25.

``GET /status/sessions/history/all`` with ``Accept: application/json`` returns
``MediaContainer`` with ``totalSize``/``size``/``offset`` and a ``Metadata`` list
whose rows carry ``accountID``, ``ratingKey``, ``title``, ``type``, ``viewedAt``
(epoch seconds) and, for episodes/tracks, ``grandparentTitle`` etc.

**The trap:** neither ``metadataItemType=1`` nor ``type=1`` reliably restricts
the response to movies. ``metadataItemType=1`` was ignored outright (the probe
got tracks and episodes back), and ``type=1`` returned a mixed page *and* broke
``totalSize``. So this adapter paginates the unfiltered history and filters on
``type == "movie"`` client-side.

External ids are not in the history rows. They come from
``GET /library/metadata/<ratingKey>`` as ``Guid: [{"id": "imdb://tt7654321"}, ...]``.

Plex history has no completion percentage: an entry exists because the item was
marked watched. ``COMPLETION_THRESHOLD`` therefore only applies to the Tautulli
source, and the README says so.

**Host networking:** Plex's host-header allowlist means a request from off-host
over plain LAN gets an empty reply. A container talking to a Plex server on the
same machine must use ``network_mode: host`` and ``http://localhost:32400``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from boxd_bridge.guids import ExternalIds, parse_plex_guid_objects
from boxd_bridge.models import WatchEvent
from boxd_bridge.sources.base import SourceError

_PAGE_SIZE = 500
_MAX_PAGES = 200
_METADATA_CONCURRENCY = 5


class PlexSource:
    def __init__(
        self,
        base_url: str,
        token: str,
        client: httpx.AsyncClient,
        *,
        account_id: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = client
        self._account_id = account_id
        self._metadata_cache: dict[str, tuple[ExternalIds, int | None]] = {}

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "X-Plex-Token": self._token}

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        try:
            response = await self._client.get(
                f"{self._base_url}{path}", params=params, headers=self._headers()
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise SourceError(f"Plex request failed: {exc}") from exc
        except ValueError as exc:
            raise SourceError(
                "Plex returned a non-JSON response. If this server is on the same "
                "host, use http://localhost:32400 and network_mode: host."
            ) from exc
        container = payload.get("MediaContainer")
        if not isinstance(container, dict):
            raise SourceError("Plex response had no MediaContainer")
        return container

    async def _metadata(self, rating_key: str) -> tuple[ExternalIds, int | None]:
        if rating_key in self._metadata_cache:
            return self._metadata_cache[rating_key]
        try:
            container = await self._get(f"/library/metadata/{rating_key}")
            items = container.get("Metadata") or []
            item = items[0] if items else {}
            ids = parse_plex_guid_objects(item.get("Guid"))
            year = item.get("year")
            result = (ids, int(year) if isinstance(year, int) else None)
        except (SourceError, IndexError, TypeError, ValueError):
            result = (ExternalIds(None, None), None)
        self._metadata_cache[rating_key] = result
        return result

    async def _fetch_rows(self, user_id: str | None) -> list[dict[str, Any]]:
        account_id = user_id or self._account_id
        rows: list[dict[str, Any]] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {
                "sort": "viewedAt:asc",
                "X-Plex-Container-Start": offset,
                "X-Plex-Container-Size": _PAGE_SIZE,
            }
            if account_id:
                params["accountID"] = account_id
            container = await self._get("/status/sessions/history/all", **params)
            page = [m for m in (container.get("Metadata") or []) if isinstance(m, dict)]
            rows.extend(page)
            total = container.get("totalSize")
            offset += len(page)
            if not page or not isinstance(total, int) or offset >= total:
                break
        return rows

    async def fetch_movie_history(
        self, *, user_id: str | None = None
    ) -> list[WatchEvent]:
        rows = await self._fetch_rows(user_id)
        # Client-side filter: the server-side type filters are not trustworthy.
        candidates = [row for row in rows if row.get("type") == "movie"]

        rating_keys = {
            str(row["ratingKey"]) for row in candidates if row.get("ratingKey")
        }
        semaphore = asyncio.Semaphore(_METADATA_CONCURRENCY)

        async def warm(rating_key: str) -> None:
            async with semaphore:
                await self._metadata(rating_key)

        await asyncio.gather(*(warm(rk) for rk in rating_keys))

        events: list[WatchEvent] = []
        for row in candidates:
            viewed_at = row.get("viewedAt")
            title = row.get("title")
            if not isinstance(viewed_at, (int, float)) or viewed_at <= 0 or not title:
                continue
            ids, meta_year = self._metadata_cache.get(
                str(row.get("ratingKey")), (ExternalIds(None, None), None)
            )
            year = row.get("year")
            events.append(
                WatchEvent(
                    watched_at_utc=datetime.fromtimestamp(int(viewed_at), tz=UTC),
                    title=str(title),
                    year=year if isinstance(year, int) else meta_year,
                    tmdb_id=ids.tmdb_id,
                    imdb_id=ids.imdb_id,
                    user_id=str(row["accountID"]) if row.get("accountID") else None,
                )
            )
        return events
