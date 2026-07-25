"""Parsing of Plex-style external ID strings.

Both sources hand us the same shape, just under different keys:

* Tautulli ``get_metadata`` -> ``guids``: ``["imdb://tt1234567", "tmdb://12345", ...]``
* Plex ``/library/metadata/<rk>`` -> ``Guid``: ``[{"id": "imdb://tt7654321"}, ...]``

Both verified live against real Tautulli and Plex instances on 2026-07-25.
"""

from __future__ import annotations

from typing import Any, NamedTuple


class ExternalIds(NamedTuple):
    tmdb_id: str | None
    imdb_id: str | None


def parse_guid_strings(guids: list[str] | None) -> ExternalIds:
    """Extract TMDB and IMDb ids from a list of ``scheme://id`` strings."""
    tmdb: str | None = None
    imdb: str | None = None
    for raw in guids or []:
        if not isinstance(raw, str):
            continue
        scheme, sep, value = raw.partition("://")
        if not sep or not value:
            continue
        scheme = scheme.lower()
        # Strip any Plex query suffix, e.g. "tmdb://123?lang=en".
        value = value.split("?", 1)[0].strip()
        if not value:
            continue
        if scheme == "tmdb" and tmdb is None:
            tmdb = value
        elif scheme == "imdb" and imdb is None:
            imdb = value
    return ExternalIds(tmdb, imdb)


def parse_plex_guid_objects(guid_objects: Any) -> ExternalIds:
    """Extract ids from Plex's ``Guid`` array of ``{"id": "..."}`` objects."""
    if not isinstance(guid_objects, list):
        return ExternalIds(None, None)
    strings = [g.get("id") for g in guid_objects if isinstance(g, dict)]
    return parse_guid_strings([s for s in strings if isinstance(s, str)])
