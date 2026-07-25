"""Unifying a film's external ids across plays that resolved them inconsistently.

The real defect: a film deleted from the library stops resolving metadata, so an
older play carries no ids while a newer play of the *same film* does. Keyed
directly, they became two films and both were written as first watches. Found in
a real export, where a February play and a June play of one film were both
flagged ``Rewatch=false``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from boxd_bridge.models import WatchEvent
from boxd_bridge.transform.rewatch import build_diary_rows, reconcile_ids

UTC_TZ = ZoneInfo("UTC")


def event(
    day: str,
    title: str = "Example Comedy",
    year: int | None = 2008,
    tmdb_id: str | None = None,
    imdb_id: str | None = None,
) -> WatchEvent:
    return WatchEvent(
        watched_at_utc=datetime.fromisoformat(f"{day}T18:00:00").replace(tzinfo=UTC),
        title=title,
        year=year,
        tmdb_id=tmdb_id,
        imdb_id=imdb_id,
    )


# --- the reported bug ----------------------------------------------------


def test_later_play_is_a_rewatch_when_only_it_resolved_ids():
    """The reported shape: the February play has no ids, the June play does."""
    events = [
        event("2026-02-01"),
        event("2026-06-28", tmdb_id="500101", imdb_id="tt5550101"),
    ]
    rows = build_diary_rows(events, UTC_TZ)
    assert len(rows) == 2
    assert [r.rewatch for r in rows] == [False, True]


def test_the_earlier_play_also_gains_the_ids():
    """Adopted ids reach the output, so the row imports by exact match."""
    events = [
        event("2026-02-01"),
        event("2026-06-28", tmdb_id="500101", imdb_id="tt5550101"),
    ]
    rows = build_diary_rows(events, UTC_TZ)
    assert all(r.tmdb_id == "500101" for r in rows)
    assert all(r.imdb_id == "tt5550101" for r in rows)


def test_order_does_not_matter():
    """The id-bearing play may come first in the history."""
    events = [
        event("2026-02-01", tmdb_id="500101"),
        event("2026-06-28"),
    ]
    rows = build_diary_rows(events, UTC_TZ)
    assert [r.rewatch for r in rows] == [False, True]
    assert all(r.tmdb_id == "500101" for r in rows)


def test_three_plays_with_only_one_resolved():
    events = [event("2026-01-05"), event("2026-03-05", tmdb_id="500101"), event("2026-09-05")]
    rows = build_diary_rows(events, UTC_TZ)
    assert [r.rewatch for r in rows] == [False, True, True]


def test_partial_resolution_merges_tmdb_and_imdb_from_different_plays():
    """One play resolved only TMDB, another only IMDb. Same film."""
    events = [
        event("2026-02-01", tmdb_id="500101"),
        event("2026-06-28", imdb_id="tt5550101"),
        event("2026-07-04"),
    ]
    enriched = reconcile_ids(events)
    assert enriched[2].tmdb_id == "500101"
    assert enriched[2].imdb_id == "tt5550101"
    assert [r.rewatch for r in build_diary_rows(events, UTC_TZ)] == [False, True, True]


# --- the inverse: must NOT merge -----------------------------------------


def test_distinct_films_sharing_title_and_year_do_not_merge():
    """Two different films, both with ids. They must stay separate."""
    events = [
        event("2026-01-05", title="Nosferatu", year=2024, tmdb_id="426063"),
        event("2026-02-05", title="Nosferatu", year=2024, tmdb_id="999999"),
    ]
    rows = build_diary_rows(events, UTC_TZ)
    assert len(rows) == 2
    assert all(r.rewatch is False for r in rows)


def test_ambiguous_title_year_does_not_adopt_ids(caplog):
    """An id-less play must not pick a side when two id sets exist."""
    events = [
        event("2026-01-05", title="Crash", year=2004, tmdb_id="111"),
        event("2026-02-05", title="Crash", year=2004, tmdb_id="222"),
        event("2026-03-05", title="Crash", year=2004),
    ]
    with caplog.at_level(logging.WARNING):
        enriched = reconcile_ids(events)
    assert enriched[2].tmdb_id is None
    assert "Ambiguous ids" in caplog.text


def test_ambiguous_case_keeps_the_idless_play_grouped_by_title():
    events = [
        event("2026-01-05", title="Crash", year=2004, tmdb_id="111"),
        event("2026-02-05", title="Crash", year=2004, tmdb_id="222"),
        event("2026-03-05", title="Crash", year=2004),
        event("2026-04-05", title="Crash", year=2004),
    ]
    rows = build_diary_rows(events, UTC_TZ)
    # Two id-less plays group together: first watch, then a rewatch.
    idless = [r for r in rows if r.tmdb_id is None]
    assert [r.rewatch for r in idless] == [False, True]


def test_conflicting_imdb_ids_are_also_treated_as_ambiguous():
    events = [
        event("2026-01-05", imdb_id="tt111"),
        event("2026-02-05", imdb_id="tt222"),
        event("2026-03-05"),
    ]
    assert reconcile_ids(events)[2].imdb_id is None


def test_same_title_different_year_stays_separate():
    events = [
        event("2026-01-05", title="Nosferatu", year=1922, tmdb_id="653"),
        event("2026-02-05", title="Nosferatu", year=2024),
    ]
    enriched = reconcile_ids(events)
    assert enriched[1].tmdb_id is None


# --- invariants ----------------------------------------------------------


def test_reconciliation_never_changes_the_event_count():
    events = [
        event("2026-02-01"),
        event("2026-06-28", tmdb_id="500101"),
        event("2026-07-01", title="Other", year=1999),
    ]
    assert len(reconcile_ids(events)) == len(events)


def test_reconciliation_never_changes_the_row_count():
    events = [event("2026-02-01"), event("2026-06-28", tmdb_id="500101")]
    assert len(build_diary_rows(events, UTC_TZ)) == 2


def test_events_that_already_have_ids_are_untouched():
    original = event("2026-06-28", tmdb_id="500101", imdb_id="tt5550101")
    assert reconcile_ids([original])[0] is original


def test_titles_with_no_ids_anywhere_are_untouched():
    events = [event("2026-02-01", title="Home Movie", year=2001)]
    enriched = reconcile_ids(events)
    assert enriched[0].tmdb_id is None
    assert enriched[0].imdb_id is None


def test_title_matching_is_normalized():
    """Casing and spacing differences must not defeat reconciliation."""
    events = [
        event("2026-02-01", title="  example   COMEDY "),
        event("2026-06-28", title="Example Comedy", tmdb_id="500101"),
    ]
    assert reconcile_ids(events)[0].tmdb_id == "500101"
    assert [r.rewatch for r in build_diary_rows(events, UTC_TZ)] == [False, True]


def test_empty_history():
    assert reconcile_ids([]) == []
