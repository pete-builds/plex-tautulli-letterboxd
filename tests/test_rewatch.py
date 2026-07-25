"""Rewatch grouping and timezone-boundary behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from boxd_bridge.models import WatchEvent
from boxd_bridge.transform.rewatch import build_diary_rows, film_key
from tests.conftest import utc

ET = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


def test_first_watch_is_not_a_rewatch(make_event):
    rows = build_diary_rows([make_event(at=utc(2026, 1, 5))], UTC_TZ)
    assert len(rows) == 1
    assert rows[0].rewatch is False


def test_later_watches_are_rewatches(make_event):
    events = [
        make_event(at=utc(2026, 3, 9)),
        make_event(at=utc(2026, 1, 5)),
        make_event(at=utc(2026, 2, 7)),
    ]
    rows = build_diary_rows(events, UTC_TZ)
    assert [(r.watched_date, r.rewatch) for r in rows] == [
        ("2026-01-05", False),
        ("2026-02-07", True),
        ("2026-03-09", True),
    ]


def test_two_watches_on_the_same_local_day_collapse_to_one_row(make_event):
    """Letterboxd merges same-film/same-date lines, so we must not emit both.

    Emitting both would produce one Rewatch=false and one Rewatch=true line for
    an entry that gets merged, and which one survives is undefined.
    """
    events = [
        make_event(at=utc(2026, 1, 5, 2)),
        make_event(at=utc(2026, 1, 5, 21)),
    ]
    rows = build_diary_rows(events, UTC_TZ)
    assert len(rows) == 1
    assert rows[0].rewatch is False


def test_timezone_conversion_happens_before_truncation(make_event):
    """23:30 ET on Jul 24 is 03:30 UTC on Jul 25. The diary date is the 24th."""
    event = make_event(at=datetime(2026, 7, 25, 3, 30, tzinfo=UTC))
    assert build_diary_rows([event], UTC_TZ)[0].watched_date == "2026-07-25"
    assert build_diary_rows([event], ET)[0].watched_date == "2026-07-24"


def test_timezone_shift_can_merge_two_utc_days_into_one_local_day(make_event):
    """Both land on Jul 24 ET, so they collapse into a single diary entry."""
    events = [
        make_event(at=datetime(2026, 7, 24, 20, 0, tzinfo=UTC)),  # 16:00 ET Jul 24
        make_event(at=datetime(2026, 7, 25, 3, 30, tzinfo=UTC)),  # 23:30 ET Jul 24
    ]
    assert len(build_diary_rows(events, ET)) == 1
    assert len(build_diary_rows(events, UTC_TZ)) == 2


def test_dst_boundary_is_handled_by_zoneinfo(make_event):
    """01:30 UTC on 2026-11-01 is 21:30 ET on 2026-10-31, before the DST change."""
    event = make_event(at=datetime(2026, 11, 1, 1, 30, tzinfo=UTC))
    assert build_diary_rows([event], ET)[0].watched_date == "2026-10-31"


def test_different_films_do_not_share_rewatch_state(make_event):
    events = [
        make_event(at=utc(2026, 1, 5), title="Heat", tmdb_id="949"),
        make_event(at=utc(2026, 1, 6), title="Collateral", tmdb_id="2502"),
    ]
    rows = build_diary_rows(events, UTC_TZ)
    assert all(r.rewatch is False for r in rows)


def test_same_film_matched_across_id_and_title(make_event):
    """A tmdb id groups with itself even when the titles differ in case."""
    events = [
        make_event(at=utc(2026, 1, 5), title="Heat", tmdb_id="949"),
        make_event(at=utc(2026, 6, 5), title="HEAT", tmdb_id="949"),
    ]
    rows = build_diary_rows(events, UTC_TZ)
    assert [r.rewatch for r in rows] == [False, True]


def test_title_year_fallback_when_no_ids():
    events = [
        WatchEvent(
            watched_at_utc=utc(2026, 1, 5), title="Home Movie", year=2001,
        ),
        WatchEvent(
            watched_at_utc=utc(2026, 4, 5), title="  home   MOVIE ", year=2001,
        ),
    ]
    rows = build_diary_rows(events, UTC_TZ)
    assert [r.rewatch for r in rows] == [False, True]


def test_title_fallback_distinguishes_by_year():
    events = [
        WatchEvent(watched_at_utc=utc(2026, 1, 5), title="Nosferatu", year=1922),
        WatchEvent(watched_at_utc=utc(2026, 1, 6), title="Nosferatu", year=2024),
    ]
    rows = build_diary_rows(events, UTC_TZ)
    assert all(r.rewatch is False for r in rows)


def test_film_key_prefers_tmdb_then_imdb_then_title():
    both = WatchEvent(
        watched_at_utc=utc(2026, 1, 1), title="X", tmdb_id="1", imdb_id="tt1"
    )
    imdb_only = WatchEvent(watched_at_utc=utc(2026, 1, 1), title="X", imdb_id="tt1")
    neither = WatchEvent(watched_at_utc=utc(2026, 1, 1), title="X", year=2020)
    assert film_key(both) == ("tmdb", "1")
    assert film_key(imdb_only) == ("imdb", "tt1")
    assert film_key(neither)[0] == "title"


def test_output_is_sorted_and_deterministic(make_event):
    events = [
        make_event(at=utc(2026, 3, 9), title="Zodiac", tmdb_id="1949"),
        make_event(at=utc(2026, 3, 9), title="Alien", tmdb_id="348"),
    ]
    rows = build_diary_rows(events, UTC_TZ)
    assert [r.title for r in rows] == ["Alien", "Zodiac"]


def test_empty_input_yields_no_rows():
    assert build_diary_rows([], UTC_TZ) == []
