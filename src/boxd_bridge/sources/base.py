"""The contract every history source implements."""

from __future__ import annotations

from typing import Protocol

from boxd_bridge.models import WatchEvent


class SourceError(RuntimeError):
    """A source could not be reached, authenticated against, or understood."""


class WatchSource(Protocol):
    async def fetch_movie_history(
        self, *, user_id: str | None = None
    ) -> list[WatchEvent]:
        """Return every completed movie watch, oldest-first ordering not guaranteed."""
        ...
