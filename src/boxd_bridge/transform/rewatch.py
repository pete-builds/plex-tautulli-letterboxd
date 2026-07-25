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

import logging
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import replace
from zoneinfo import ZoneInfo

from boxd_bridge.models import DiaryRow, WatchEvent

logger = logging.getLogger(__name__)

FilmKey = tuple[str, str]
TitleKey = tuple[str, str]


def _normalize_title(title: str) -> str:
    folded = unicodedata.normalize("NFKD", title).casefold().strip()
    return " ".join(folded.split())


def film_key(event: WatchEvent) -> FilmKey:
    """Identity used to group watches of the same film.

    Exact ids win. Title+year is the last resort and only applies to items with
    no external ids at all, which in practice means unmatched local media.

    Run :func:`reconcile_ids` over the full history first. Applied to raw events
    this fragments a film whose plays resolved ids inconsistently.
    """
    if event.tmdb_id:
        return ("tmdb", event.tmdb_id)
    if event.imdb_id:
        return ("imdb", event.imdb_id)
    return ("title", f"{_normalize_title(event.title)}|{event.year or ''}")


def _title_key(event: WatchEvent) -> TitleKey:
    return (_normalize_title(event.title), str(event.year or ""))


def reconcile_ids(events: Sequence[WatchEvent]) -> list[WatchEvent]:
    """Give every play of a film the same external ids before grouping.

    A film deleted from the library stops resolving metadata, so an old play of
    it carries no ids while a recent play of the *same film* does. Keyed
    directly, those become two films and every one of them is reported as a
    first watch. In real history this wrote ``Rewatch=false`` onto rows that
    were genuinely rewatches, and a Letterboxd import cannot be undone.

    So id-less events adopt the ids observed for their (title, year), but only
    when those observations are unanimous. Two different TMDB ids under one
    (title, year) means two genuinely distinct films, and merging them would be
    a worse error than the one being fixed, so those are left alone.
    """
    tmdb_by_title: dict[TitleKey, set[str]] = defaultdict(set)
    imdb_by_title: dict[TitleKey, set[str]] = defaultdict(set)

    for event in events:
        if not (event.tmdb_id or event.imdb_id):
            continue
        key = _title_key(event)
        if event.tmdb_id:
            tmdb_by_title[key].add(event.tmdb_id)
        if event.imdb_id:
            imdb_by_title[key].add(event.imdb_id)

    enriched: list[WatchEvent] = []
    warned: set[TitleKey] = set()

    for event in events:
        key = _title_key(event)
        tmdb_ids = tmdb_by_title.get(key, set())
        imdb_ids = imdb_by_title.get(key, set())

        if not tmdb_ids and not imdb_ids:
            enriched.append(event)  # nothing ever resolved for this title
            continue

        # Conflicting ids under one (title, year): distinct films that happen to
        # share a name and year. Refuse to merge and say so, once.
        if len(tmdb_ids) > 1 or len(imdb_ids) > 1:
            if key not in warned:
                warned.add(key)
                logger.warning(
                    "Ambiguous ids for %r (%s): tmdb=%s imdb=%s. Not merging; "
                    "these plays stay grouped by title.",
                    event.title,
                    event.year,
                    sorted(tmdb_ids),
                    sorted(imdb_ids),
                )
            enriched.append(event)
            continue

        # Fill in whatever this play is missing, without ever overwriting an id
        # it already has. Partially resolved plays matter as much as id-less
        # ones: a play with only TMDB and a play with only IMDb key differently
        # and would otherwise split the same film in two.
        tmdb_id = event.tmdb_id or next(iter(tmdb_ids), None)
        imdb_id = event.imdb_id or next(iter(imdb_ids), None)
        if tmdb_id == event.tmdb_id and imdb_id == event.imdb_id:
            enriched.append(event)
        else:
            enriched.append(replace(event, tmdb_id=tmdb_id, imdb_id=imdb_id))

    return enriched


def build_diary_rows(
    events: Iterable[WatchEvent], tz: ZoneInfo
) -> list[DiaryRow]:
    """Collapse events onto local calendar dates and flag rewatches."""
    # 0. Unify each film's ids across its plays. Without this, a film whose
    #    older plays lost their metadata splits in two and every play of it is
    #    reported as a first watch.
    events = reconcile_ids(list(events))

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
                    rating10=event.rating10,
                )
            )

    # Stable, human-readable ordering. Not required by the importer, but it makes
    # the file diffable and the tests deterministic.
    rows.sort(key=lambda r: (r.watched_date, _normalize_title(r.title)))
    return rows
