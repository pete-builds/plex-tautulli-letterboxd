"""Resolving a plex.tv identity to a server's local account id.

The two numbers are not the same, which is the whole problem. Verified against a
live Plex Media Server on 2026-07-25:

* ``/accounts`` lists the **server owner** as ``id=1`` with their username as
  ``name``. Their plex.tv id is a completely different number.
* A **shared** user appears under their plex.tv id directly.
* ``/status/sessions/history/all`` honours ``accountID``, and filtering by a
  plex.tv id that is not a local id returns **zero rows and no error**, which is
  how a server owner silently got an empty export.
"""

from __future__ import annotations

import httpx
import pytest

from boxd_bridge.sources.base import SourceError
from boxd_bridge.sources.plex import PlexSource

# Mirrors the real shape: owner is local id 1, shared users carry plex.tv ids.
ACCOUNTS = [
    {"id": 1, "name": "owner"},
    {"id": 2, "name": ""},
    {"id": 555001, "name": "sharedfan"},
    {"id": 555002, "name": "otherfan"},
]

OWNER_PLEX_TV_ID = "999100"  # not present in /accounts
SHARED_PLEX_TV_ID = "555001"  # present in /accounts


def history_row(account_id, rating_key="1001", title="Example Film", viewed=1770003600):
    return {
        "accountID": account_id,
        "ratingKey": rating_key,
        "title": title,
        "type": "movie",
        "viewedAt": viewed,
        "year": 2026,
    }


ALL_ROWS = [
    history_row(1, "1001", "Owner Film"),
    history_row(1, "1002", "Owner Film Two"),
    history_row(555001, "1003", "Shared Film"),
    history_row(555002, "1004", "Another Users Film"),
]


def transport(*, accounts=ACCOUNTS, honour_filter=True, rows=ALL_ROWS):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/accounts":
            return httpx.Response(
                200, json={"MediaContainer": {"size": len(accounts), "Account": accounts}}
            )
        if path == "/status/sessions/history/all":
            wanted = request.url.params.get("accountID")
            page = rows
            if honour_filter and wanted:
                page = [r for r in rows if str(r["accountID"]) == str(wanted)]
            return httpx.Response(
                200,
                json={
                    "MediaContainer": {
                        "size": len(page),
                        "totalSize": len(page),
                        "Metadata": page,
                    }
                },
            )
        if path.startswith("/library/metadata/"):
            return httpx.Response(
                200,
                json={"MediaContainer": {"Metadata": [{"Guid": [], "year": 2026}]}},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def hosted_source(*, account_id, username, **kw):
    client = httpx.AsyncClient(transport=transport(**kw))
    source = PlexSource(
        "http://plex.example.com:32400",
        "token",
        client,
        account_id=account_id,
        account_username=username,
        require_account_scope=True,
    )
    return source, client


# --- the bug -------------------------------------------------------------


async def test_server_owner_gets_their_rows_not_an_empty_export():
    """The regression: owner's plex.tv id is absent from /accounts.

    Before the fix this filtered by 999100, matched nothing, and returned an
    empty CSV with no error.
    """
    source, client = hosted_source(account_id=OWNER_PLEX_TV_ID, username="owner")
    async with client:
        events = await source.fetch_movie_history()
    assert len(events) == 2
    assert {e.title for e in events} == {"Owner Film", "Owner Film Two"}


async def test_shared_user_still_resolves_by_exact_id():
    """Fixing the owner case must not break the shared case."""
    source, client = hosted_source(account_id=SHARED_PLEX_TV_ID, username="sharedfan")
    async with client:
        events = await source.fetch_movie_history()
    assert [e.title for e in events] == ["Shared Film"]


async def test_owner_resolution_is_case_insensitive():
    source, client = hosted_source(account_id=OWNER_PLEX_TV_ID, username="OWNER")
    async with client:
        assert len(await source.fetch_movie_history()) == 2


async def test_exact_id_match_wins_over_name_match():
    accounts = [{"id": 1, "name": "sharedfan"}, {"id": 555001, "name": "sharedfan"}]
    source, client = hosted_source(
        account_id=SHARED_PLEX_TV_ID, username="sharedfan", accounts=accounts
    )
    async with client:
        events = await source.fetch_movie_history()
    assert [e.title for e in events] == ["Shared Film"]


# --- isolation: the part that must never regress -------------------------


async def test_a_user_never_receives_another_users_rows():
    for plex_tv_id, username, expected in (
        (OWNER_PLEX_TV_ID, "owner", {1}),
        (SHARED_PLEX_TV_ID, "sharedfan", {555001}),
        ("555002", "otherfan", {555002}),
    ):
        source, client = hosted_source(account_id=plex_tv_id, username=username)
        async with client:
            events = await source.fetch_movie_history()
        assert {int(e.user_id) for e in events} == expected, username


async def test_rows_are_filtered_client_side_even_if_plex_ignores_accountid():
    """Defence in depth: Plex already ignores the `type` filter.

    If a future version also ignored accountID, trusting it would leak every
    user's history into one person's export.
    """
    source, client = hosted_source(
        account_id=OWNER_PLEX_TV_ID, username="owner", honour_filter=False
    )
    async with client:
        events = await source.fetch_movie_history()
    assert {int(e.user_id) for e in events} == {1}
    assert len(events) == 2


async def test_unresolvable_identity_fails_closed_rather_than_exporting_everything():
    source, client = hosted_source(account_id="999999", username="ghost")
    async with client:
        with pytest.raises(SourceError, match="Could not identify your account"):
            await source.fetch_movie_history()


async def test_unresolvable_identity_does_not_fall_back_to_unfiltered():
    """The dangerous failure is a populated export, not an empty one."""
    source, client = hosted_source(
        account_id="999999", username="ghost", honour_filter=False
    )
    async with client:
        with pytest.raises(SourceError):
            await source.fetch_movie_history()


async def test_missing_identity_entirely_fails_closed():
    source, client = hosted_source(account_id=None, username=None)
    async with client:
        with pytest.raises(SourceError):
            await source.fetch_movie_history()


# --- self-host mode is unaffected ----------------------------------------


async def test_self_host_mode_does_not_require_resolution():
    client = httpx.AsyncClient(transport=transport())
    source = PlexSource("http://localhost:32400", "token", client)
    async with client:
        events = await source.fetch_movie_history()
    assert len(events) == 4  # every account, which is correct for a single-tenant box
