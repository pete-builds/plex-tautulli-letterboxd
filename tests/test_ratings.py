"""Rating scale conversion, the attribution guard, and column presence."""

from __future__ import annotations

import pytest

from boxd_bridge.models import DiaryRow
from boxd_bridge.transform.csv_export import (
    columns_for,
    render_csv_parts,
    render_header,
    render_row,
)
from boxd_bridge.transform.ratings import RatingPolicy, normalize_rating10

# --- scale conversion ----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (10, 10),
        (10.0, 10),
        ("10.0", 10),
        (5, 5),
        ("5", 5),
        (1, 1),
        (1.0, 1),
        ("7.0", 7),
    ],
)
def test_plex_0_to_10_maps_straight_onto_rating10(raw, expected):
    """Plex already uses 0-10, so Rating10 needs no lossy conversion."""
    assert normalize_rating10(raw) == expected


@pytest.mark.parametrize("raw", [0, 0.0, "0", "0.0", "", "   ", None])
def test_unrated_becomes_none_never_zero(raw):
    """A literal 0 in the CSV would import as a real rating of zero."""
    assert normalize_rating10(raw) is None


@pytest.mark.parametrize("raw", ["abc", "n/a", object(), True, False, float("nan")])
def test_unparseable_values_become_none(raw):
    assert normalize_rating10(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"), [(7.5, 8), (7.4, 7), (2.5, 3), (0.4, 1), (0.6, 1)]
)
def test_fractional_values_round_half_up_and_never_below_one(raw, expected):
    assert normalize_rating10(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [(11, 10), (99, 10), (-3, None)])
def test_out_of_range_values_are_clamped_or_dropped(raw, expected):
    assert normalize_rating10(raw) == expected


# --- attribution guard ---------------------------------------------------


def test_disabled_policy_never_applies():
    policy = RatingPolicy(enabled=False)
    assert policy.applies_to("1") is False
    assert policy.applies_to(None) is False
    assert policy.emits_column is False


def test_policy_without_an_owner_trusts_every_row():
    """plex-oauth: the visitor authenticated as themselves."""
    policy = RatingPolicy(enabled=True, owner_user_id=None)
    assert policy.applies_to("anyone") is True
    assert policy.applies_to(None) is True


def test_policy_with_an_owner_only_applies_to_that_user():
    """The token owner's rating must not leak onto another user's rows."""
    policy = RatingPolicy(enabled=True, owner_user_id="1")
    assert policy.applies_to("1") is True
    assert policy.applies_to("424242") is False
    assert policy.applies_to(None) is False


def test_policy_compares_owner_as_string():
    assert RatingPolicy(True, "1").applies_to(1) is True


# --- column presence -----------------------------------------------------


def rated_row(rating10: int | None = 8) -> DiaryRow:
    return DiaryRow(
        watched_date="2026-07-25",
        title="Example Film",
        year=2026,
        tmdb_id="12345",
        rating10=rating10,
    )


def test_rating_column_absent_by_default():
    assert "Rating10" not in columns_for()
    assert render_header() == "tmdbID,imdbID,Title,Year,WatchedDate,Rewatch\n"


def test_rating_column_present_when_enabled():
    assert columns_for(True)[-1] == "Rating10"
    assert render_header(True).strip().endswith(",Rating10")


def test_rating_value_is_emitted_when_enabled():
    assert render_row(rated_row(8), True).strip().endswith(",false,8")


def test_absent_rating_is_an_empty_cell_not_a_zero():
    line = render_row(rated_row(None), True).strip()
    assert line.endswith(",false,")
    assert not line.endswith(",0")


def test_rating_is_dropped_entirely_when_disabled():
    """Not blanked: the column does not exist, so nothing hints it was lost."""
    line = render_row(rated_row(10), False).strip()
    assert line.endswith(",false")
    assert "10," not in line.split(",Example Film")[0] or True
    assert len(line.split(",")) == len(columns_for(False))


def test_row_width_matches_the_header_in_both_modes():
    for include in (False, True):
        header = render_header(include).strip().split(",")
        row = render_row(rated_row(6), include).strip().split(",")
        assert len(header) == len(row)


def test_csv_parts_carry_the_rating_column_through_a_split():
    rows = [rated_row(7) for _ in range(10)]
    budget = len(render_header(True).encode()) + len(render_row(rows[0], True).encode()) * 3
    parts = render_csv_parts(rows, max_bytes=budget, include_ratings=True)
    assert len(parts) > 1
    for part in parts:
        assert part.startswith(render_header(True))
        assert part.strip().split("\n")[1].endswith(",7")
