"""Start-up contracts: each mode must refuse to run without what it needs."""

from __future__ import annotations

import pytest

from boxd_bridge.config import AuthMode, ConfigError, Settings, SourceKind
from boxd_bridge.guids import parse_guid_strings, parse_plex_guid_objects

BASE = {"_env_file": None}
SECRET = "s" * 40


def settings(**kw) -> Settings:
    return Settings(**BASE, **kw)


def test_env_mode_requires_a_configured_source():
    with pytest.raises(ConfigError, match="AUTH_MODE=env requires"):
        settings(auth_mode="env")


def test_env_mode_accepts_tautulli():
    s = settings(auth_mode="env", tautulli_url="http://t:8181", tautulli_apikey="k")
    assert s.source_kind is SourceKind.TAUTULLI


def test_env_mode_accepts_plex():
    s = settings(auth_mode="env", plex_url="http://localhost:32400", plex_token="t")
    assert s.source_kind is SourceKind.PLEX


def test_env_mode_prefers_tautulli_when_both_are_configured():
    """Tautulli retains history independently of Plex, which prunes it."""
    s = settings(
        auth_mode="env",
        tautulli_url="http://t:8181",
        tautulli_apikey="k",
        plex_url="http://localhost:32400",
        plex_token="t",
    )
    assert s.source_kind is SourceKind.TAUTULLI


def test_partial_tautulli_config_is_not_enough():
    with pytest.raises(ConfigError):
        settings(auth_mode="env", tautulli_url="http://t:8181")


def test_hosted_mode_requires_a_session_secret_and_base_url():
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        settings(auth_mode="plex-oauth", public_base_url="https://x.example")
    with pytest.raises(ConfigError, match="PUBLIC_BASE_URL"):
        settings(auth_mode="plex-oauth", session_secret=SECRET)


def test_hosted_mode_rejects_a_weak_session_secret():
    with pytest.raises(ConfigError, match="at least 32"):
        settings(auth_mode="plex-oauth", session_secret="short", public_base_url="https://x")


def test_hosted_mode_is_always_the_plex_source():
    """You cannot OAuth into someone else's Tautulli."""
    s = settings(
        auth_mode="plex-oauth",
        session_secret=SECRET,
        public_base_url="https://x.example",
        tautulli_url="http://t:8181",
        tautulli_apikey="k",
    )
    assert s.source_kind is SourceKind.PLEX


def test_connection_preference_flips_with_mode():
    hosted = settings(
        auth_mode="plex-oauth", session_secret=SECRET, public_base_url="https://x"
    )
    selfhost = settings(auth_mode="env", plex_url="http://localhost:32400", plex_token="t")
    assert hosted.prefer_local_connections is False
    assert selfhost.prefer_local_connections is True


def test_invalid_timezone_is_rejected():
    with pytest.raises(ConfigError, match="not a valid IANA timezone"):
        settings(
            auth_mode="env",
            plex_url="http://localhost:32400",
            plex_token="t",
            display_timezone="Mars/Olympus",
        )


def test_valid_timezone_is_accepted():
    s = settings(
        auth_mode="env",
        plex_url="http://localhost:32400",
        plex_token="t",
        display_timezone="America/New_York",
    )
    assert str(s.tzinfo) == "America/New_York"


def test_chunk_size_cannot_exceed_the_letterboxd_limit():
    with pytest.raises(Exception):
        settings(
            auth_mode="env",
            plex_url="http://localhost:32400",
            plex_token="t",
            csv_chunk_bytes=5_000_000,
        )


def test_default_mode_is_env():
    assert Settings.model_fields["auth_mode"].default is AuthMode.ENV


# --------------------------------------------------------------------------


def test_parse_guid_strings():
    ids = parse_guid_strings(["imdb://tt1234567", "tmdb://12345", "tvdb://67890"])
    assert ids.imdb_id == "tt1234567"
    assert ids.tmdb_id == "12345"


def test_parse_guid_strings_ignores_opaque_plex_guid():
    ids = parse_guid_strings(["plex://movie/000000000000000000000001"])
    assert ids == (None, None)


def test_parse_guid_strings_strips_query_suffix():
    assert parse_guid_strings(["tmdb://12345?lang=en"]).tmdb_id == "12345"


def test_parse_guid_strings_handles_junk():
    assert parse_guid_strings(None) == (None, None)
    assert parse_guid_strings(["", "nonsense", "tmdb://"]) == (None, None)


def test_parse_plex_guid_objects():
    ids = parse_plex_guid_objects([{"id": "imdb://tt7654321"}, {"id": "tmdb://54321"}])
    assert ids.imdb_id == "tt7654321"
    assert ids.tmdb_id == "54321"


def test_parse_plex_guid_objects_handles_missing():
    assert parse_plex_guid_objects(None) == (None, None)
    assert parse_plex_guid_objects([{"no_id": 1}]) == (None, None)
