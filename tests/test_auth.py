"""Session cookies, rate limiting, and the local/non-local connection filter."""

from __future__ import annotations

import time

import pytest

from boxd_bridge.auth.plex_oauth import PlexOAuthClient, PlexPin, select_connection
from boxd_bridge.auth.ratelimit import RateLimiter
from boxd_bridge.auth.session import SessionCodec, SessionExpired, SessionInvalid

SECRET = "x" * 48


def test_session_round_trip():
    codec = SessionCodec(SECRET, 900)
    payload = {"plex_token": "abc123", "server_uri": "https://x.plex.direct:32400"}
    assert codec.decode(codec.encode(payload)) == payload


def test_session_ciphertext_does_not_leak_the_token():
    codec = SessionCodec(SECRET, 900)
    token = codec.encode({"plex_token": "SUPERSECRETTOKEN"})
    assert "SUPERSECRETTOKEN" not in token


def test_session_rejects_a_forged_cookie():
    codec = SessionCodec(SECRET, 900)
    other = SessionCodec("y" * 48, 900)
    with pytest.raises(SessionInvalid):
        codec.decode(other.encode({"plex_token": "abc"}))


def test_session_rejects_a_tampered_cookie():
    codec = SessionCodec(SECRET, 900)
    token = codec.encode({"plex_token": "abc"})
    with pytest.raises(SessionInvalid):
        codec.decode(token[:-4] + "AAAA")


def test_session_expires():
    # Fernet stamps tokens with whole seconds, so a >1s sleep against a 0s TTL
    # is the shortest deterministic way to cross the boundary.
    codec = SessionCodec(SECRET, 900)
    token = codec.encode({"plex_token": "abc"})
    time.sleep(1.05)
    with pytest.raises(SessionExpired):
        codec.decode(token, ttl_seconds=0)


def test_session_requires_a_missing_cookie_to_fail_closed():
    codec = SessionCodec(SECRET, 900)
    with pytest.raises(SessionInvalid):
        codec.decode(None)


def test_session_secret_must_be_long_enough():
    with pytest.raises(ValueError):
        SessionCodec("short", 900)


# --------------------------------------------------------------------------


def test_rate_limiter_allows_up_to_the_limit_then_blocks():
    limiter = RateLimiter(3, 60)
    assert [limiter.allow("1.2.3.4", now=0) for _ in range(3)] == [True, True, True]
    assert limiter.allow("1.2.3.4", now=0) is False


def test_rate_limiter_is_per_key():
    limiter = RateLimiter(1, 60)
    assert limiter.allow("a", now=0) is True
    assert limiter.allow("b", now=0) is True
    assert limiter.allow("a", now=0) is False


def test_rate_limiter_window_slides():
    limiter = RateLimiter(1, 60)
    assert limiter.allow("a", now=0) is True
    assert limiter.allow("a", now=30) is False
    assert limiter.allow("a", now=61) is True


# --------------------------------------------------------------------------


LOCAL = {"uri": "http://127.0.0.1:32400", "local": True, "relay": False}
REMOTE = {"uri": "https://abc.plex.direct:32400", "local": False, "relay": False}
RELAY = {"uri": "https://relay.plex.tv", "local": False, "relay": True}


def test_hosted_mode_never_selects_a_local_connection():
    """A hosted instance using a local URI would hit its OWN LAN, not the user's."""
    chosen = select_connection([LOCAL, REMOTE], prefer_local=False)
    assert chosen["uri"] == REMOTE["uri"]


def test_hosted_mode_returns_none_when_only_local_is_available():
    assert select_connection([LOCAL], prefer_local=False) is None


def test_hosted_mode_prefers_direct_over_relay():
    assert select_connection([RELAY, REMOTE], prefer_local=False)["uri"] == REMOTE["uri"]


def test_self_host_mode_prefers_local():
    assert select_connection([REMOTE, LOCAL], prefer_local=True)["uri"] == LOCAL["uri"]


def test_self_host_falls_back_to_remote_without_a_local_connection():
    assert select_connection([RELAY, REMOTE], prefer_local=True)["uri"] == REMOTE["uri"]


def test_select_connection_handles_junk():
    assert select_connection(None, prefer_local=True) is None
    assert select_connection([], prefer_local=True) is None
    assert select_connection([{"local": True}], prefer_local=True) is None


def test_auth_url_puts_parameters_in_the_fragment():
    """Plex's forwarding flow reads params after '#?', not from the query string."""
    client = PlexOAuthClient(None, product="boxd-bridge", client_identifier="cid-1")
    url = client.build_auth_url(PlexPin("123", "ABCD"), "https://app.example/auth/plex/callback")
    assert url.startswith("https://app.plex.tv/auth#?")
    assert "?" not in url.split("#", 1)[0]
    fragment = url.split("#?", 1)[1]
    assert "clientID=cid-1" in fragment
    assert "code=ABCD" in fragment
    assert "forwardUrl=https%3A%2F%2Fapp.example%2Fauth%2Fplex%2Fcallback" in fragment
