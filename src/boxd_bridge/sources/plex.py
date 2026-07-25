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
from boxd_bridge.transform.ratings import DISABLED, RatingPolicy, normalize_rating10

def _year_from_release_date(value: object) -> int | None:
    """Plex history rows carry no `year`, but they do carry a release date.

    Verified live: 0 of 54 movie history rows had a `year` field, while every
    one had `originallyAvailableAt`. Rows for media deleted from the library
    also have `ratingKey=None`, so the metadata lookup cannot supply a year
    either. Without this fallback those rows export with an empty Year and
    Letterboxd matches on title alone, which silently imports the wrong film for
    an ambiguous title like "The Thing" (1951, 1982, 2011).
    """
    if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


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
        account_username: str | None = None,
        require_account_scope: bool = False,
        rating_policy: RatingPolicy = DISABLED,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = client
        self._account_id = account_id
        self._account_username = account_username
        self._require_account_scope = require_account_scope
        self._rating_policy = rating_policy
        # ratingKey -> (external ids, year, the token owner's rating)
        self._metadata_cache: dict[str, tuple[ExternalIds, int | None, int | None]] = {}

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

    async def _metadata(
        self, rating_key: str
    ) -> tuple[ExternalIds, int | None, int | None]:
        if rating_key in self._metadata_cache:
            return self._metadata_cache[rating_key]
        try:
            container = await self._get(f"/library/metadata/{rating_key}")
            items = container.get("Metadata") or []
            item = items[0] if items else {}
            ids = parse_plex_guid_objects(item.get("Guid"))
            year = item.get("year")
            # `userRating` is the personal 0-10 star rating. `rating` and
            # `audienceRating` are critic/aggregate scores; never export those.
            result = (
                ids,
                int(year) if isinstance(year, int) else None,
                normalize_rating10(item.get("userRating")),
            )
        except (SourceError, IndexError, TypeError, ValueError):
            result = (ExternalIds(None, None), None, None)
        self._metadata_cache[rating_key] = result
        return result

    async def _resolve_account_id(self) -> str | None:
        """Map a plex.tv identity onto this server's LOCAL account id.

        These are not the same number, which is the trap. Verified live:

        * A **shared** user appears in ``/accounts`` under their plex.tv id, so
          that id works directly as a history filter.
        * The **server owner** is local account ``1``, while their plex.tv id is
          something else entirely. Filtering history by the plex.tv id returns
          zero rows and no error, which is exactly what a server owner would
          hit on every single export.

        So try an exact id match first, then fall back to matching the account
        name against the plex.tv username.
        """
        wanted_id = str(self._account_id).strip() if self._account_id else ""
        wanted_name = (self._account_username or "").strip().casefold()
        if not wanted_id and not wanted_name:
            return None

        container = await self._get("/accounts")
        accounts = [a for a in (container.get("Account") or []) if isinstance(a, dict)]

        # 1. Shared user: plex.tv id is already this server's local id.
        if wanted_id:
            for account in accounts:
                if str(account.get("id")) == wanted_id:
                    return wanted_id

        # 2. Server owner: different id, same name.
        if wanted_name:
            for account in accounts:
                if str(account.get("name") or "").strip().casefold() == wanted_name:
                    local_id = account.get("id")
                    if local_id is not None:
                        return str(local_id)

        return None

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
        scope_id: str | None = None
        if self._require_account_scope:
            scope_id = await self._resolve_account_id()
            if scope_id is None:
                # Fail closed. Running unfiltered here would hand every user on
                # the server their history, into someone's public diary.
                raise SourceError(
                    "Could not identify your account on this Plex server, so no "
                    "history was exported."
                )
            rows = await self._fetch_rows(scope_id)
        else:
            rows = await self._fetch_rows(user_id)
            if user_id:
                scope_id = str(user_id)

        # Client-side filter: the server-side type filters are not trustworthy.
        candidates = [row for row in rows if row.get("type") == "movie"]

        # Defence in depth. accountID *is* honoured today, but `type` is not, and
        # trusting a server-side filter for an isolation boundary is how one
        # user's history ends up in another user's export.
        if scope_id is not None:
            candidates = [
                row
                for row in candidates
                if str(row.get("accountID")) == str(scope_id)
            ]

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
            ids, meta_year, rating10 = self._metadata_cache.get(
                str(row.get("ratingKey")), (ExternalIds(None, None), None, None)
            )
            user_id = str(row["accountID"]) if row.get("accountID") else None
            year = row.get("year")
            if not isinstance(year, int):
                year = meta_year or _year_from_release_date(
                    row.get("originallyAvailableAt")
                )
            events.append(
                WatchEvent(
                    watched_at_utc=datetime.fromtimestamp(int(viewed_at), tz=UTC),
                    title=str(title),
                    year=year,
                    tmdb_id=ids.tmdb_id,
                    imdb_id=ids.imdb_id,
                    user_id=user_id,
                    rating10=(
                        rating10 if self._rating_policy.applies_to(user_id) else None
                    ),
                )
            )
        return events
