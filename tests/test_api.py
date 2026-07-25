"""HTTP contract tests against the documented response shapes."""

from __future__ import annotations

import csv
import io

import httpx
import pytest
from fastapi.testclient import TestClient

from boxd_bridge.config import Settings
from boxd_bridge.main import create_app
from tests.test_sources import tautulli_app, tautulli_history_row

SECRET = "s" * 40

# Constructed, not captured: noon America/New_York on two consecutive days, so
# the local diary dates the `since` tests assert against are unambiguous.
WATCHED_JUL_25 = 1784995200  # 2026-07-25 12:00 ET
WATCHED_JUL_26 = 1785081600  # 2026-07-26 12:00 ET


def env_settings(**kw) -> Settings:
    base = dict(
        _env_file=None,
        auth_mode="env",
        tautulli_url="http://tautulli.test:8181",
        tautulli_apikey="key",
        display_timezone="America/New_York",
    )
    base.update(kw)
    return Settings(**base)


def hosted_settings(**kw) -> Settings:
    base = dict(
        _env_file=None,
        auth_mode="plex-oauth",
        session_secret=SECRET,
        public_base_url="https://boxd.example",
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def env_client():
    app = create_app(env_settings())
    rows = [
        tautulli_history_row(row_id=1, stopped=WATCHED_JUL_25),
        tautulli_history_row(row_id=2, stopped=WATCHED_JUL_26),
        tautulli_history_row(row_id=3, percent_complete=10, watched_status=0.25),
    ]
    with TestClient(app) as client:
        # Swap the real client for one backed by the recorded Tautulli shapes.
        app.state.http_client = httpx.AsyncClient(transport=tautulli_app(rows))
        yield client


def test_healthz_is_open_and_reports_mode(env_client):
    response = env_client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["auth_mode"] == "env"
    assert body["source"] == "tautulli"


def test_index_renders(env_client):
    response = env_client.get("/")
    assert response.status_code == 200
    assert "boxd" in response.text
    assert "text/html" in response.headers["content-type"]


def test_preview_shape(env_client):
    body = env_client.get("/api/preview").json()
    assert set(body) >= {"rows", "parts", "rewatches", "exact_id_matches", "sample"}
    assert body["rows"] == 2  # the 10%-complete play is excluded
    assert body["rewatches"] == 1
    assert body["timezone"] == "America/New_York"
    assert body["sample"][0]["Title"] == "Example Film"


def test_export_csv_headers_and_body(env_client):
    response = env_client.get("/api/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="letterboxd.csv"' in response.headers["content-disposition"]
    assert response.headers["X-Boxd-Parts"] == "1"

    rows = list(csv.reader(io.StringIO(response.text), doublequote=False, escapechar="\\"))
    assert rows[0] == ["tmdbID", "imdbID", "Title", "Year", "WatchedDate", "Rewatch"]
    assert len(rows) == 3
    assert rows[1][5] == "false"
    assert rows[2][5] == "true"


def test_export_rejects_an_out_of_range_part(env_client):
    assert env_client.get("/api/export.csv?part=9").status_code == 404


# --- since parameter -----------------------------------------------------


def test_preview_reports_the_unfiltered_total_alongside_the_window(env_client):
    body = env_client.get("/api/preview?since=2026-07-25").json()
    assert body["since"] == "2026-07-25"
    assert body["total_rows"] == 2
    assert body["rows"] + body["filtered_out"] == body["total_rows"]


def test_since_narrows_the_export(env_client):
    """The two fixture plays land on 2026-07-25 and 2026-07-26 (ET)."""
    everything = env_client.get("/api/preview").json()
    narrowed = env_client.get("/api/preview?since=2026-07-26").json()
    assert everything["rows"] == 2
    assert narrowed["rows"] == 1
    assert narrowed["filtered_out"] == 1


def test_since_lower_bound_is_inclusive_over_http(env_client):
    assert env_client.get("/api/preview?since=2026-07-25").json()["rows"] == 2


def test_preview_offers_a_next_since_cutoff(env_client):
    body = env_client.get("/api/preview").json()
    # Today in the configured display timezone.
    from datetime import datetime
    from zoneinfo import ZoneInfo

    assert body["next_since"] == datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def test_export_headers_reflect_the_active_filter(env_client):
    response = env_client.get("/api/export.csv?since=2026-07-26")
    assert response.headers["X-Boxd-Rows"] == "1"
    assert response.headers["X-Boxd-Total-Rows"] == "2"
    assert response.headers["X-Boxd-Since"] == "2026-07-26"
    assert "X-Boxd-Next-Since" in response.headers


def test_export_omits_the_since_header_when_unfiltered(env_client):
    response = env_client.get("/api/export.csv")
    assert "X-Boxd-Since" not in response.headers
    assert response.headers["X-Boxd-Rows"] == "2"


@pytest.mark.parametrize("value", ["not-a-date", "25-07-2026", "2026-13-01", "2026/07/25"])
def test_malformed_since_is_a_400(env_client, value):
    for path in ("/api/preview", "/api/export.csv"):
        response = env_client.get(f"{path}?since={value}")
        assert response.status_code == 400, path
        assert "YYYY-MM-DD" in response.json()["detail"] or "calendar date" in response.json()["detail"]


def test_future_since_yields_a_header_only_csv_not_an_error(env_client):
    response = env_client.get("/api/export.csv?since=2099-01-01")
    assert response.status_code == 200
    assert response.headers["X-Boxd-Rows"] == "0"
    assert response.text.strip() == "tmdbID,imdbID,Title,Year,WatchedDate,Rewatch"


def test_blank_since_is_treated_as_all_time(env_client):
    assert env_client.get("/api/preview?since=").json()["rows"] == 2


def test_index_exposes_the_range_control(env_client):
    page = env_client.get("/").text
    assert 'id="range"' in page
    assert "Last 30 days" in page
    assert "All time" in page


def test_users_endpoint_lists_tautulli_users(env_client):
    users = env_client.get("/api/users").json()["users"]
    # "Local" (id 0) and inactive accounts are excluded; the admin is not.
    assert [u["friendly_name"] for u in users] == ["owner", "moviefan"]


def test_hosted_mode_requires_a_session():
    app = create_app(hosted_settings())
    with TestClient(app) as client:
        assert client.get("/api/preview").status_code == 401
        assert client.get("/api/export.csv").status_code == 401


def test_hosted_mode_index_shows_the_sign_in_prompt():
    app = create_app(hosted_settings())
    with TestClient(app) as client:
        assert "Sign in with Plex" in client.get("/").text


def test_hosted_mode_rejects_a_forged_session_cookie():
    app = create_app(hosted_settings())
    with TestClient(app) as client:
        client.cookies.set("bb_session", "not-a-real-fernet-token")
        assert client.get("/api/preview").status_code == 401


def test_auth_routes_are_absent_in_env_mode(env_client):
    assert env_client.post("/auth/plex/start", follow_redirects=False).status_code == 404


def test_auth_routes_exist_in_hosted_mode():
    app = create_app(hosted_settings())
    with TestClient(app) as client:
        # Reaches the handler (502 from the unreachable plex.tv stub), not a 404.
        assert client.post("/auth/plex/start", follow_redirects=False).status_code != 404


def test_pin_rate_limit_returns_429():
    app = create_app(hosted_settings(rate_limit_requests=1, rate_limit_window_seconds=300))
    with TestClient(app) as client:
        client.post("/auth/plex/start", follow_redirects=False)
        second = client.post("/auth/plex/start", follow_redirects=False)
        assert second.status_code == 429


def test_plex_url_is_never_accepted_from_the_browser(env_client):
    """SSRF guard: a query parameter must not redirect where the app fetches."""
    response = env_client.get("/api/preview?plex_url=http://169.254.169.254/latest/meta-data")
    assert response.status_code == 200
    # Still served from the configured Tautulli source, not the injected URL.
    assert response.json()["rows"] == 2
