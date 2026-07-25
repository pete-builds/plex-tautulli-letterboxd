"""Letterboxd CSV formatting: quoting, backslash escaping, and chunking."""

from __future__ import annotations

import pytest

from boxd_bridge.models import DiaryRow
from boxd_bridge.transform.csv_export import (
    CSV_COLUMNS,
    part_filename,
    render_csv_parts,
    render_header,
    render_row,
)


def row(title: str = "Heat", **kw) -> DiaryRow:
    base = dict(
        watched_date="2026-01-05", title=title, year=1995, tmdb_id="949", rewatch=False
    )
    base.update(kw)
    return DiaryRow(**base)


def test_header_matches_documented_columns():
    assert render_header() == "tmdbID,imdbID,Title,Year,WatchedDate,Rewatch\n"


def test_no_space_after_comma():
    """A space after the delimiter breaks Letterboxd's quoted-string parsing."""
    line = render_row(row())
    assert ", " not in line
    assert line == "949,,Heat,1995,2026-01-05,false\n"


def test_rewatch_renders_as_lowercase_boolean():
    assert render_row(row(rewatch=True)).endswith(",true\n")


def test_field_containing_a_comma_is_quoted():
    line = render_row(row(title="Dogville, Revisited"))
    assert '"Dogville, Revisited"' in line


def test_quotes_are_escaped_with_a_backslash_not_doubled():
    """Letterboxd escapes quotes with a backslash; csv's default doubling is wrong."""
    line = render_row(row(title='The "Burbs'))
    assert '\\"' in line
    assert '""' not in line


def test_literal_backslash_is_escaped():
    line = render_row(row(title=r"Back\Slash"))
    assert r"Back\\Slash" in line


def test_unicode_is_preserved():
    line = render_row(row(title="Amélie"))
    assert "Amélie" in line
    assert line.encode("utf-8").decode("utf-8") == line


def test_missing_ids_render_as_empty_fields():
    line = render_row(row(tmdb_id=None, imdb_id=None, year=None))
    assert line == ",,Heat,,2026-01-05,false\n"


def test_imdb_only_row():
    line = render_row(row(tmdb_id=None, imdb_id="tt0113277"))
    assert line.startswith(",tt0113277,Heat,")


def test_single_part_when_under_the_limit():
    parts = render_csv_parts([row() for _ in range(10)], max_bytes=900_000)
    assert len(parts) == 1
    assert parts[0].count("\n") == 11  # header + 10 rows


def test_splits_into_parts_at_the_byte_limit():
    rows = [row(watched_date=f"2026-01-{d:02d}") for d in range(1, 29)]
    header_bytes = len(render_header().encode())
    row_bytes = len(render_row(rows[0]).encode())
    budget = header_bytes + row_bytes * 5

    parts = render_csv_parts(rows, max_bytes=budget)

    assert len(parts) == 6  # 28 rows at 5 per part
    for part in parts:
        assert len(part.encode("utf-8")) <= budget


def test_every_part_repeats_the_header():
    rows = [row(watched_date=f"2026-01-{d:02d}") for d in range(1, 11)]
    budget = len(render_header().encode()) + len(render_row(rows[0]).encode()) * 3
    parts = render_csv_parts(rows, max_bytes=budget)
    assert len(parts) > 1
    for part in parts:
        assert part.startswith(render_header())


def test_no_rows_are_lost_across_the_split():
    rows = [row(watched_date=f"2026-02-{d:02d}") for d in range(1, 21)]
    budget = len(render_header().encode()) + len(render_row(rows[0]).encode()) * 4
    parts = render_csv_parts(rows, max_bytes=budget)
    emitted = sum(len(p.strip().split("\n")) - 1 for p in parts)
    assert emitted == len(rows)


def test_oversized_single_row_still_gets_a_part():
    """A row larger than the budget must not loop forever or vanish."""
    big = row(title="X" * 5_000)
    parts = render_csv_parts([big, row()], max_bytes=2_000)
    assert len(parts) == 2
    assert "X" * 5_000 in parts[0]


def test_empty_export_is_a_header_only_file():
    parts = render_csv_parts([], max_bytes=900_000)
    assert parts == [render_header()]


def test_budget_smaller_than_the_header_is_rejected():
    with pytest.raises(ValueError):
        render_csv_parts([row()], max_bytes=len(render_header().encode()) - 1)


def test_column_count_is_consistent():
    assert len(CSV_COLUMNS) == len(render_row(row()).rstrip("\n").split(","))


def test_part_filenames():
    assert part_filename("letterboxd", 0, 1) == "letterboxd.csv"
    assert part_filename("letterboxd", 0, 3) == "letterboxd-part-1.csv"
    assert part_filename("letterboxd", 2, 3) == "letterboxd-part-3.csv"


def test_parts_round_trip_through_a_matching_csv_reader():
    import csv
    import io

    rows = [row(title='He said "go", loudly'), row(title=r"a\b")]
    part = render_csv_parts(rows, max_bytes=900_000)[0]
    parsed = list(
        csv.reader(
            io.StringIO(part), doublequote=False, escapechar="\\", quotechar='"'
        )
    )
    assert parsed[0] == list(CSV_COLUMNS)
    assert parsed[1][2] == 'He said "go", loudly'
    assert parsed[2][2] == r"a\b"
