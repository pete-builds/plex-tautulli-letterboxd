"""The ``since`` window.

Idempotent import makes re-importing safe, but not *cheap*: Letterboxd shows a
review screen row by row, so emitting the full history forever means the tool
gets slower to use the longer you own it. ``since`` bounds the file.

Two properties this must hold, both easy to get wrong:

* It filters on the **local** watch date, after timezone conversion, so the
  window lines up with the dates the CSV actually shows rather than raw UTC.
* It is applied **after** rewatch flags are computed across the whole history.
  Filtering first would make a film you first saw in 2024 and rewatched last
  week export as ``Rewatch=false``, quietly writing a wrong diary entry.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date

from boxd_bridge.models import DiaryRow

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class InvalidSinceDate(ValueError):
    """The ``since`` parameter was not a plain YYYY-MM-DD calendar date."""


def parse_since(value: str | None) -> date | None:
    """Parse a user-supplied ``since`` value, or return None for "all time".

    Deliberately stricter than :meth:`date.fromisoformat`, which also accepts
    ``20260101`` and ISO week dates. The API documents one format, so it should
    accept exactly one format.
    """
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if not _ISO_DATE.match(candidate):
        raise InvalidSinceDate(
            f"since must be a YYYY-MM-DD date, got {value!r}"
        )
    try:
        return date.fromisoformat(candidate)
    except ValueError as exc:
        raise InvalidSinceDate(f"{value!r} is not a real calendar date") from exc


def filter_since(rows: Iterable[DiaryRow], since: date | None) -> list[DiaryRow]:
    """Keep rows watched on or after ``since``. The lower bound is inclusive.

    A future ``since`` is not an error: it simply matches nothing and yields a
    valid header-only CSV.
    """
    if since is None:
        return list(rows)
    cutoff = since.isoformat()
    # watched_date is always YYYY-MM-DD, where lexicographic order is
    # chronological order, so this needs no date parsing per row.
    return [row for row in rows if row.watched_date >= cutoff]
