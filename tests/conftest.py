from __future__ import annotations

from datetime import UTC, datetime

import pytest

from boxd_bridge.models import WatchEvent


def utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def make_event():
    def _make(
        *,
        at: datetime,
        title: str = "Heat",
        year: int | None = 1995,
        tmdb_id: str | None = "949",
        imdb_id: str | None = None,
        user_id: str | None = "1",
    ) -> WatchEvent:
        return WatchEvent(
            watched_at_utc=at,
            title=title,
            year=year,
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            user_id=user_id,
        )

    return _make
