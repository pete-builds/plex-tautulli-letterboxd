"""Regression tests for the "Watched since" control.

The reported bug: on Android Chrome, choosing a specific date made the field snap
back to today, so an arbitrary `since` could not be set.

Root cause was the markup. The ``<input type="date">`` was a *descendant* of its
``<label>``. Tapping to dismiss the native picker lands on the label, which
re-forwards activation to the input, reopening the picker and re-seeding it with
today. Explicit ``for=`` association with the input as a sibling removes the
re-activation path entirely.

These tests pin the structure, because the failure is invisible to any test that
only checks that the elements exist.
"""

from __future__ import annotations

from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from boxd_bridge.main import create_app
from tests.test_api import env_settings

STATIC = Path(__file__).resolve().parents[1] / "src" / "boxd_bridge" / "static"


class _LabelNesting(HTMLParser):
    """Records whether #since ever appears inside an open <label>."""

    def __init__(self) -> None:
        super().__init__()
        self.label_depth = 0
        self.since_inside_label = False
        self.since_seen = False
        self.label_for_since = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self.label_depth += 1
            if a.get("for") == "since":
                self.label_for_since = True
        elif tag == "input" and a.get("id") == "since":
            self.since_seen = True
            if self.label_depth > 0:
                self.since_inside_label = True

    def handle_endtag(self, tag):
        if tag == "label" and self.label_depth > 0:
            self.label_depth -= 1


@pytest.fixture
def page() -> str:
    with TestClient(create_app(env_settings())) as client:
        return client.get("/").text


def parse(page: str) -> _LabelNesting:
    p = _LabelNesting()
    p.feed(page)
    return p


def test_date_input_is_rendered(page):
    assert parse(page).since_seen


def test_date_input_is_not_nested_inside_a_label(page):
    """The actual regression. Nesting reopens the picker on mobile Chrome."""
    assert parse(page).since_inside_label is False


def test_date_input_is_associated_by_an_explicit_for_attribute(page):
    """De-nesting must not cost the accessible label."""
    assert parse(page).label_for_since is True


def test_date_input_has_no_max_attribute(page):
    """A `max` would let the browser clamp a chosen date, reintroducing a revert."""
    assert 'id="since"' in page
    marker = page.split('id="since"')[0].rsplit("<input", 1)[-1]
    assert "max=" not in marker


def test_presets_and_custom_option_are_present(page):
    for expected in ("All time", "Last 30 days", "Last 90 days", "Last year"):
        assert expected in page
    assert 'value="custom"' in page


def test_bookmarked_since_is_read_from_the_query_string():
    js = (STATIC / "app.js").read_text()
    assert 'new URLSearchParams(window.location.search).get("since")' in js


def test_there_is_exactly_one_source_of_truth_for_since():
    """No mirrored copy of the date that could drift from the control."""
    js = (STATIC / "app.js").read_text()
    assert "customSince" not in js
    # The browser does no date arithmetic; option values are already dates.
    assert "isoDaysAgo" not in js
    assert "setDate(" not in js


def test_download_url_is_built_at_click_time_not_cached_in_href():
    """A cached href goes stale when the selection changes.

    That is how a 90-day window was downloaded while the control read
    "Last year".
    """
    js = (STATIC / "app.js").read_text()
    assert 'addEventListener("click", (event) => {' in js
    assert "buildUrl(\"/api/export.csv\").toString()" in js
    assert "dl.href = buildUrl" not in js


def test_both_input_and_change_events_refresh_the_resolved_date():
    """Mobile commits via `input`; desktop via `change`."""
    js = (STATIC / "app.js").read_text()
    assert '$("since")?.addEventListener("input", updateResolved);' in js
    assert '$("since")?.addEventListener("change", updateResolved);' in js


def test_nothing_ever_writes_next_since_into_the_date_field():
    """next_since is today, and belongs only in the bookmark URL."""
    js = (STATIC / "app.js").read_text()
    for forbidden in (
        '$("since").value = data.next_since',
        "since.value = data.next_since",
    ):
        assert forbidden not in js
    assert "next_since" in js  # still used for the bookmark link


def test_custom_with_no_date_does_not_silently_export_all_time():
    js = (STATIC / "app.js").read_text()
    assert 'Pick a date to export from' in js


# --- preset values on the wire -------------------------------------------


def test_all_time_is_the_default_and_sends_no_since():
    """An empty value means the query parameter is omitted entirely."""
    from boxd_bridge.routers.pages import build_since_options

    options = build_since_options(date(2026, 7, 25))
    assert options[0] == {"label": "All time", "value": ""}


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Last 30 days", "2026-06-25"),
        ("Last 90 days", "2026-04-26"),
        ("Last year", "2025-07-25"),
    ],
)
def test_each_preset_resolves_to_the_correct_date(label, expected):
    """Pinned against a fixed today so the arithmetic cannot drift."""
    from boxd_bridge.routers.pages import build_since_options

    options = {o["label"]: o["value"] for o in build_since_options(date(2026, 7, 25))}
    assert options[label] == expected


def test_last_year_is_365_days_back_not_the_calendar_year():
    """The preset that failed. Jan 1st would be 2026-01-01, which is wrong."""
    from boxd_bridge.routers.pages import build_since_options

    options = {o["label"]: o["value"] for o in build_since_options(date(2026, 7, 25))}
    assert options["Last year"] == "2025-07-25"
    assert options["Last year"] != "2026-01-01"


def test_custom_is_the_final_option():
    from boxd_bridge.routers.pages import build_since_options

    options = build_since_options(date(2026, 7, 25))
    assert options[-1]["value"] == "custom"


def test_preset_dates_are_rendered_into_the_page_as_option_values(page):
    """The value the browser sends is the value the server resolved."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("America/New_York")).date()
    for days in (30, 90, 365):
        assert f'value="{(today - timedelta(days=days)).isoformat()}"' in page


def test_page_shows_the_resolved_scope(page):
    assert 'id="resolved"' in page
    assert "Exporting your entire history" in page


def test_the_select_is_the_only_control_carrying_preset_values(page):
    """One control owns `since`; the date input is hidden until custom."""
    assert page.count('id="range"') == 1
    assert page.count('id="since"') == 1
    field = page.split('id="since-field"')[1][:40]
    assert "hidden" in page.split('id="since-field"')[0][-60:] or "hidden" in field
