"""The ``since`` window: parsing, boundaries, and its interaction with timezones."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from boxd_bridge.models import DiaryRow, WatchEvent
from boxd_bridge.transform.csv_export import render_csv_parts, render_header
from boxd_bridge.transform.filters import InvalidSinceDate, filter_since, parse_since
from boxd_bridge.transform.rewatch import build_diary_rows

ET = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


def diary_row(watched_date: str, title: str = "Heat") -> DiaryRow:
    return DiaryRow(watched_date=watched_date, title=title, year=1995, tmdb_id="949")


# --- parsing -------------------------------------------------------------


def test_parse_since_accepts_iso_date():
    assert parse_since("2026-07-25") == date(2026, 7, 25)


def test_parse_since_treats_absent_and_blank_as_all_time():
    assert parse_since(None) is None
    assert parse_since("") is None
    assert parse_since("   ") is None


def test_parse_since_strips_surrounding_whitespace():
    assert parse_since(" 2026-07-25 ") == date(2026, 7, 25)


@pytest.mark.parametrize(
    "value",
    ["25-07-2026", "2026/07/25", "20260725", "2026-W30-5", "yesterday", "2026-07"],
)
def test_parse_since_rejects_other_formats(value):
    """One documented format, one accepted format."""
    with pytest.raises(InvalidSinceDate):
        parse_since(value)


@pytest.mark.parametrize("value", ["2026-13-01", "2026-02-30", "0000-00-00"])
def test_parse_since_rejects_impossible_dates(value):
    with pytest.raises(InvalidSinceDate):
        parse_since(value)


# --- boundaries ----------------------------------------------------------


def test_lower_bound_is_inclusive():
    """A watch exactly ON the since date must be included."""
    rows = [diary_row("2026-07-25")]
    assert len(filter_since(rows, date(2026, 7, 25))) == 1


def test_the_day_before_is_excluded():
    rows = [diary_row("2026-07-24")]
    assert filter_since(rows, date(2026, 7, 25)) == []


def test_the_day_after_is_included():
    rows = [diary_row("2026-07-26")]
    assert len(filter_since(rows, date(2026, 7, 25))) == 1


def test_none_means_all_time():
    rows = [diary_row("2001-01-01"), diary_row("2026-07-25")]
    assert len(filter_since(rows, None)) == 2


def test_filter_returns_a_new_list():
    rows = [diary_row("2026-07-25")]
    assert filter_since(rows, None) is not rows


def test_year_and_month_boundaries_compare_chronologically():
    rows = [diary_row(d) for d in ("2025-12-31", "2026-01-01", "2026-09-30", "2026-10-01")]
    kept = [r.watched_date for r in filter_since(rows, date(2026, 1, 1))]
    assert kept == ["2026-01-01", "2026-09-30", "2026-10-01"]


def test_future_since_is_not_an_error_and_matches_nothing():
    rows = [diary_row("2026-07-25")]
    assert filter_since(rows, date(2099, 1, 1)) == []


# --- timezone interaction ------------------------------------------------


def test_since_filters_on_local_date_not_utc_date():
    """23:30 ET Jul 24 is 03:30 UTC Jul 25.

    With since=2026-07-25 the row must be EXCLUDED, because its local diary date
    is the 24th. Filtering on the raw UTC timestamp would wrongly keep it.
    """
    event = WatchEvent(
        watched_at_utc=datetime(2026, 7, 25, 3, 30, tzinfo=UTC),
        title="Heat",
        year=1995,
        tmdb_id="949",
    )
    rows = build_diary_rows([event], ET)
    assert rows[0].watched_date == "2026-07-24"
    assert filter_since(rows, date(2026, 7, 25)) == []
    assert len(filter_since(rows, date(2026, 7, 24))) == 1


def test_same_instant_can_fall_inside_or_outside_the_window_by_timezone():
    event = WatchEvent(
        watched_at_utc=datetime(2026, 7, 25, 3, 30, tzinfo=UTC),
        title="Heat",
        year=1995,
        tmdb_id="949",
    )
    cutoff = date(2026, 7, 25)
    assert len(filter_since(build_diary_rows([event], UTC_TZ), cutoff)) == 1
    assert filter_since(build_diary_rows([event], ET), cutoff) == []


# --- interaction with rewatch flags --------------------------------------


def test_rewatch_flag_survives_the_window():
    """A film first seen before the window is still a rewatch inside it.

    Filtering the events before grouping would export this as a first watch,
    writing a wrong diary entry rather than merely an incomplete one.
    """
    events = [
        WatchEvent(
            watched_at_utc=datetime(2024, 3, 1, 12, tzinfo=UTC),
            title="Heat", year=1995, tmdb_id="949",
        ),
        WatchEvent(
            watched_at_utc=datetime(2026, 7, 20, 12, tzinfo=UTC),
            title="Heat", year=1995, tmdb_id="949",
        ),
    ]
    rows = filter_since(build_diary_rows(events, UTC_TZ), date(2026, 1, 1))
    assert len(rows) == 1
    assert rows[0].rewatch is True


# --- empty results -------------------------------------------------------


def test_empty_result_renders_a_valid_header_only_csv():
    """Not a crash, and not an empty body."""
    rows = filter_since([diary_row("2020-01-01")], date(2026, 7, 25))
    parts = render_csv_parts(rows, max_bytes=900_000)
    assert parts == [render_header()]
    assert parts[0].strip() == "tmdbID,imdbID,Title,Year,WatchedDate,Rewatch"
