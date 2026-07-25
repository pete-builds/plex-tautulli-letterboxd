"""Turn raw watch events into Letterboxd diary rows.

Two rules do the real work here.

**Timezone before truncation.** Letterboxd's ``WatchedDate`` is a calendar date,
not a timestamp, and the importer asks that timezone conversion happen before
truncation. A film finished at 11pm ET is 03:00 UTC the *next* day, so
truncating the UTC value lands the diary entry on the wrong day.

**Rewatch.** Group by film, sort ascending, first watch is not a rewatch and
every later one is. Because Letterboxd merges same-film/same-date lines into a
single entry, we collapse duplicates on a date *first*: otherwise two watches on
one day would produce one row saying ``Rewatch=false`` and another saying
``Rewatch=true`` for an entry that will be merged, and which one wins is
undefined.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from zoneinfo import ZoneInfo

from boxd_bridge.models import DiaryRow, WatchEvent

FilmKey = tuple[str, str]


def _normalize_title(title: str) -> str:
    folded = unicodedata.normalize("NFKD", title).casefold().strip()
    return " ".join(folded.split())


def film_key(event: WatchEvent) -> FilmKey:
    """Identity used to group watches of the same film.

    Exact ids win. Title+year is the last resort and only applies to items with
    no external ids at all, which in practice means unmatched local media.
    """
    if event.tmdb_id:
        return ("tmdb", event.tmdb_id)
    if event.imdb_id:
        return ("imdb", event.imdb_id)
    return ("title", f"{_normalize_title(event.title)}|{event.year or ''}")


def build_diary_rows(
    events: Iterable[WatchEvent], tz: ZoneInfo
) -> list[DiaryRow]:
    """Collapse events onto local calendar dates and flag rewatches."""
    # 1. Project every event onto a local calendar date, keeping the earliest
    #    event for a given (film, date) so the merged entry is deterministic.
    by_film_date: dict[tuple[FilmKey, str], WatchEvent] = {}
    for event in events:
        key = film_key(event)
        local_date = event.watched_at_utc.astimezone(tz).date().isoformat()
        slot = (key, local_date)
        existing = by_film_date.get(slot)
        if existing is None or event.watched_at_utc < existing.watched_at_utc:
            by_film_date[slot] = event

    # 2. Per film, the earliest date is the first watch; everything later is a
    #    rewatch.
    per_film: dict[FilmKey, list[tuple[str, WatchEvent]]] = defaultdict(list)
    for (key, local_date), event in by_film_date.items():
        per_film[key].append((local_date, event))

    rows: list[DiaryRow] = []
    for entries in per_film.values():
        entries.sort(key=lambda pair: (pair[0], pair[1].watched_at_utc))
        for index, (local_date, event) in enumerate(entries):
            rows.append(
                DiaryRow(
                    watched_date=local_date,
                    title=event.title,
                    year=event.year,
                    tmdb_id=event.tmdb_id,
                    imdb_id=event.imdb_id,
                    rewatch=index > 0,
                )
            )

    # Stable, human-readable ordering. Not required by the importer, but it makes
    # the file diffable and the tests deterministic.
    rows.sort(key=lambda r: (r.watched_date, _normalize_title(r.title)))
    return rows
