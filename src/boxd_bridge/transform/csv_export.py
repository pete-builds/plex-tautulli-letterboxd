"""Letterboxd CSV rendering.

Format constraints, all from <https://letterboxd.com/about/importing-data/>:

* UTF-8, comma delimited, **no space after the comma** (a space breaks their
  quoted-string parsing).
* Quotes inside quoted text are escaped with a **backslash**, not by doubling.
  Python's ``csv`` doubles by default, so ``doublequote=False`` plus an explicit
  ``escapechar`` is required.
* **1MB file size limit.** Larger exports are split into parts, with the header
  row repeated in every part.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

from boxd_bridge.models import DiaryRow

BASE_COLUMNS: Sequence[str] = (
    "tmdbID",
    "imdbID",
    "Title",
    "Year",
    "WatchedDate",
    "Rewatch",
)
RATING_COLUMN = "Rating10"

# Back-compat alias: the columns emitted when ratings are off, which is default.
CSV_COLUMNS: Sequence[str] = BASE_COLUMNS


def columns_for(include_ratings: bool = False) -> tuple[str, ...]:
    """Rating10 is *absent*, not blank, when ratings are disabled.

    An always-present empty column would suggest the data exists and happens to
    be missing. Omitting it says plainly that this export does not carry ratings.
    """
    return (*BASE_COLUMNS, RATING_COLUMN) if include_ratings else tuple(BASE_COLUMNS)

_LINE_TERMINATOR = "\n"


def _writer(buffer: io.StringIO) -> "csv._writer":
    return csv.writer(
        buffer,
        delimiter=",",
        quotechar='"',
        doublequote=False,
        escapechar="\\",
        quoting=csv.QUOTE_MINIMAL,
        lineterminator=_LINE_TERMINATOR,
    )


def _render_line(values: Sequence[object]) -> str:
    buffer = io.StringIO()
    _writer(buffer).writerow(values)
    return buffer.getvalue()


def render_header(include_ratings: bool = False) -> str:
    return _render_line(columns_for(include_ratings))


def row_values(row: DiaryRow, include_ratings: bool = False) -> list[str]:
    values = [
        row.tmdb_id or "",
        row.imdb_id or "",
        row.title,
        str(row.year) if row.year is not None else "",
        row.watched_date,
        "true" if row.rewatch else "false",
    ]
    if include_ratings:
        # An unrated film gets an empty cell. A literal 0 would import as a
        # real rating of zero.
        values.append(str(row.rating10) if row.rating10 is not None else "")
    return values


def render_row(row: DiaryRow, include_ratings: bool = False) -> str:
    return _render_line(row_values(row, include_ratings))


def render_csv_parts(
    rows: Iterable[DiaryRow],
    max_bytes: int = 900_000,
    include_ratings: bool = False,
) -> list[str]:
    """Render rows into one or more complete CSV documents.

    Each returned part is independently importable: it starts with the header
    and stays at or under ``max_bytes`` when UTF-8 encoded. A single row that is
    larger than the budget still gets its own part rather than being dropped or
    looping forever.
    """
    header = render_header(include_ratings)
    header_bytes = len(header.encode("utf-8"))
    if max_bytes <= header_bytes:
        raise ValueError(
            f"max_bytes={max_bytes} leaves no room for the {header_bytes}-byte header"
        )

    parts: list[str] = []
    current: list[str] = [header]
    current_bytes = header_bytes
    has_row = False

    for row in rows:
        line = render_row(row, include_ratings)
        line_bytes = len(line.encode("utf-8"))
        if has_row and current_bytes + line_bytes > max_bytes:
            parts.append("".join(current))
            current = [header]
            current_bytes = header_bytes
            has_row = False
        current.append(line)
        current_bytes += line_bytes
        has_row = True

    if has_row:
        parts.append("".join(current))
    if not parts:
        parts.append(header)
    return parts


def part_filename(stem: str, index: int, total: int) -> str:
    if total == 1:
        return f"{stem}.csv"
    return f"{stem}-part-{index + 1}.csv"
