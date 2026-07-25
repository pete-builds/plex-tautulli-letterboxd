"""Tautulli history adapter.

Field names below were verified live against a real Tautulli instance on
2026-07-25 rather than recalled. What the probe established:

``GET /api/v2?apikey=...&cmd=get_history&media_type=movie``

``response.data`` carries ``recordsFiltered`` / ``recordsTotal`` and a ``data``
list. Each row has, among others::

    date, started, stopped   -- Unix epoch seconds, true UTC
    user_id, user, friendly_name
    media_type               -- "movie"
    rating_key
    title, full_title, year
    guid                     -- "plex://movie/5d77683..."  (opaque, NOT tmdb/imdb)
    percent_complete         -- int 0-100
    watched_status           -- 0, 0.25, 0.5, 0.75, 1.0
    group_count, group_ids

Two things that matter and are easy to get wrong:

1. ``guid`` is the opaque ``plex://`` form. The real external ids live behind
   ``cmd=get_metadata&rating_key=<rk>`` as ``guids``:
   ``["imdb://tt1234567", "tmdb://12345", "tvdb://67890"]``. We look those up
   per distinct rating_key and cache them.
2. Errors come back as HTTP 200 with ``response.result == "error"``, so the
   status code alone tells you nothing.

Pagination is ``start`` / ``length`` against ``recordsFiltered``.
``grouping=1`` (the default here) merges resumed sessions of the same film into
one row, which is exactly the notion of "a watch" we want.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from boxd_bridge.guids import ExternalIds, parse_guid_strings
from boxd_bridge.models import WatchEvent
from boxd_bridge.sources.base import SourceError
from boxd_bridge.transform.ratings import DISABLED, RatingPolicy, normalize_rating10

_PAGE_SIZE = 500
_MAX_PAGES = 200  # 100k plays; a guard against a misreported total looping forever
_METADATA_CONCURRENCY = 5


class TautulliSource:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        client: httpx.AsyncClient,
        *,
        completion_threshold: int = 85,
        rating_policy: RatingPolicy = DISABLED,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client
        self._completion_threshold = completion_threshold
        self._rating_policy = rating_policy
        # rating_key -> (external ids, the token owner's rating)
        self._metadata_cache: dict[str, tuple[ExternalIds, int | None]] = {}

    async def _call(self, cmd: str, **params: Any) -> Any:
        query = {"apikey": self._api_key, "cmd": cmd, **params}
        try:
            response = await self._client.get(
                f"{self._base_url}/api/v2", params=query
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise SourceError(f"Tautulli request failed: {exc}") from exc
        except ValueError as exc:
            raise SourceError("Tautulli returned a non-JSON response") from exc

        envelope = payload.get("response") or {}
        if envelope.get("result") != "success":
            # Never echo the message verbatim into user-facing errors upstream;
            # it is fine here because this string stays server-side.
            raise SourceError(
                f"Tautulli {cmd} failed: {envelope.get('message') or 'unknown error'}"
            )
        return envelope.get("data")

    async def _metadata(self, rating_key: str) -> tuple[ExternalIds, int | None]:
        if rating_key in self._metadata_cache:
            return self._metadata_cache[rating_key]
        try:
            data = await self._call("get_metadata", rating_key=rating_key)
        except SourceError:
            result = (ExternalIds(None, None), None)
        else:
            data = data if isinstance(data, dict) else {}
            guids = data.get("guids")
            ids = parse_guid_strings(guids if isinstance(guids, list) else None)
            # `user_rating` is the personal star rating. `rating` and
            # `audience_rating` are critic/aggregate scores and must never be
            # exported as somebody's own rating.
            result = (ids, normalize_rating10(data.get("user_rating")))
        self._metadata_cache[rating_key] = result
        return result

    async def _rating_owner_id(self) -> str | None:
        """The Tautulli admin, which is the account whose token reads ratings."""
        try:
            data = await self._call("get_users")
        except SourceError:
            return None
        for user in data if isinstance(data, list) else []:
            if isinstance(user, dict) and user.get("is_admin"):
                return str(user.get("user_id"))
        return None

    def _is_complete(self, row: dict[str, Any]) -> bool:
        percent = row.get("percent_complete")
        if isinstance(percent, (int, float)):
            return percent >= self._completion_threshold
        # Fall back to watched_status (0 .. 1.0) when percent_complete is absent.
        status = row.get("watched_status")
        if isinstance(status, (int, float)):
            return status * 100 >= self._completion_threshold
        return False

    @staticmethod
    def _watched_at(row: dict[str, Any]) -> datetime | None:
        """Prefer when the film *finished*; that is the date a viewer means."""
        for field in ("stopped", "date", "started"):
            value = row.get(field)
            if isinstance(value, (int, float)) and value > 0:
                return datetime.fromtimestamp(int(value), tz=UTC)
        return None

    async def _fetch_rows(self, user_id: str | None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start = 0
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {
                "media_type": "movie",
                "grouping": 1,
                "start": start,
                "length": _PAGE_SIZE,
                "order_column": "date",
                "order_dir": "asc",
            }
            if user_id:
                params["user_id"] = user_id
            data = await self._call("get_history", **params)
            if not isinstance(data, dict):
                raise SourceError("Tautulli get_history returned an unexpected shape")
            page = data.get("data") or []
            rows.extend(r for r in page if isinstance(r, dict))
            total = data.get("recordsFiltered")
            start += len(page)
            if not page or not isinstance(total, int) or start >= total:
                break
        return rows

    async def fetch_movie_history(
        self, *, user_id: str | None = None
    ) -> list[WatchEvent]:
        rows = await self._fetch_rows(user_id)

        # media_type is filtered server-side, but a defensive check costs nothing
        # and protects against the filter being ignored the way Plex's is.
        candidates = [
            row
            for row in rows
            if row.get("media_type") == "movie" and self._is_complete(row)
        ]

        rating_keys = {
            str(row["rating_key"]) for row in candidates if row.get("rating_key")
        }
        semaphore = asyncio.Semaphore(_METADATA_CONCURRENCY)

        async def warm(rating_key: str) -> None:
            async with semaphore:
                await self._metadata(rating_key)

        await asyncio.gather(*(warm(rk) for rk in rating_keys))

        # Ratings read through this API key always belong to the admin, so they
        # may only be attached to the admin's own plays.
        policy = self._rating_policy
        if policy.enabled and policy.owner_user_id is None:
            policy = RatingPolicy(True, await self._rating_owner_id())

        events: list[WatchEvent] = []
        for row in candidates:
            watched_at = self._watched_at(row)
            title = row.get("title") or row.get("full_title")
            if watched_at is None or not title:
                continue
            ids, rating10 = self._metadata_cache.get(
                str(row.get("rating_key")), (ExternalIds(None, None), None)
            )
            user_id = str(row["user_id"]) if row.get("user_id") else None
            year = row.get("year")
            events.append(
                WatchEvent(
                    watched_at_utc=watched_at,
                    title=str(title),
                    year=int(year) if str(year or "").isdigit() else None,
                    tmdb_id=ids.tmdb_id,
                    imdb_id=ids.imdb_id,
                    user_id=user_id,
                    user_name=row.get("friendly_name") or row.get("user"),
                    rating10=rating10 if policy.applies_to(user_id) else None,
                )
            )
        return events

    async def list_users(self) -> list[dict[str, Any]]:
        data = await self._call("get_users")
        if not isinstance(data, list):
            return []
        return [
            {
                "user_id": str(u.get("user_id")),
                "username": u.get("username"),
                "friendly_name": u.get("friendly_name") or u.get("username"),
            }
            for u in data
            if isinstance(u, dict) and u.get("is_active") and str(u.get("user_id")) != "0"
        ]
