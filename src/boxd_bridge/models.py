"""Normalized domain types shared by every source adapter and the transform layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class WatchEvent:
    """A single completed viewing of a film, normalized across sources.

    ``watched_at_utc`` is always timezone-aware and always UTC. Sources are
    responsible for converting whatever they store into that form; the
    transform layer converts to the display timezone exactly once, at the end.
    """

    watched_at_utc: datetime
    title: str
    year: int | None = None
    tmdb_id: str | None = None
    imdb_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    # 1-10, or None when unrated or not attributable to this event's user.
    rating10: int | None = None

    def __post_init__(self) -> None:
        if self.watched_at_utc.tzinfo is None:
            raise ValueError("watched_at_utc must be timezone-aware")


@dataclass(frozen=True, slots=True)
class DiaryRow:
    """One Letterboxd diary entry, ready to be written to CSV."""

    watched_date: str  # YYYY-MM-DD in the display timezone
    title: str
    year: int | None = None
    tmdb_id: str | None = None
    imdb_id: str | None = None
    rewatch: bool = False
    rating10: int | None = None


@dataclass(frozen=True, slots=True)
class PlexServer:
    """A Plex Media Server reachable at a chosen connection URI."""

    name: str
    machine_identifier: str
    uri: str
    access_token: str
    local: bool
