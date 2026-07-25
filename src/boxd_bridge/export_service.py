"""The one place the pipeline is assembled.

    source adapter -> WatchEvent[] -> transform -> Letterboxd CSV part(s)

There is deliberately no watermark, no "last synced" state and no dedupe store.
Letterboxd's importer updates an existing diary entry when a film is imported
with a ``WatchedDate`` matching one already in the diary, and collapses repeated
identical lines on save. Re-importing the full history is therefore safe and
non-duplicating, which removes the entire class of state-tracking code that
comparable tools carry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from boxd_bridge.models import DiaryRow
from boxd_bridge.sources.base import WatchSource
from boxd_bridge.transform.csv_export import render_csv_parts
from boxd_bridge.transform.filters import filter_since
from boxd_bridge.transform.rewatch import build_diary_rows


@dataclass(frozen=True, slots=True)
class ExportResult:
    parts: list[str]
    rows: list[DiaryRow]
    total_rows: int = 0
    since: date | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def filtered_out(self) -> int:
        return max(0, self.total_rows - len(self.rows))

    @property
    def part_count(self) -> int:
        return len(self.parts)

    @property
    def rewatch_count(self) -> int:
        return sum(1 for row in self.rows if row.rewatch)

    @property
    def matched_count(self) -> int:
        """Rows carrying an exact id, which import without fuzzy title matching."""
        return sum(1 for row in self.rows if row.tmdb_id or row.imdb_id)


async def build_export(
    source: WatchSource,
    *,
    tz: ZoneInfo,
    user_id: str | None = None,
    max_bytes: int = 900_000,
    since: date | None = None,
) -> ExportResult:
    events = await source.fetch_movie_history(user_id=user_id)

    # Rewatch flags are computed over the COMPLETE history, then the window is
    # applied. Narrowing first would export a film you first saw years ago as a
    # first watch, writing a wrong diary entry rather than merely a partial one.
    all_rows = build_diary_rows(events, tz)
    rows = filter_since(all_rows, since)

    parts = render_csv_parts(rows, max_bytes=max_bytes)
    return ExportResult(
        parts=parts, rows=rows, total_rows=len(all_rows), since=since
    )
