"""Plex PIN ("OAuth-style") authentication, Forwarding variant.

Documented flow (<https://forums.plex.tv/t/authenticating-with-plex/609370>):

1. ``POST https://plex.tv/api/v2/pins`` with ``strong=true`` -> ``{id, code}``
2. Send the browser to ``https://app.plex.tv/auth#?clientID=..&code=..&forwardUrl=..``
   Note the parameters live in the **URL fragment**, after ``#?``.
3. The user authenticates on plex.tv and is forwarded back.
4. ``GET https://plex.tv/api/v2/pins/<id>?code=<code>`` -> ``authToken``
5. ``GET https://plex.tv/api/v2/user`` validates a token (200 ok / 401 dead).

Server discovery is ``GET /api/v2/resources``. Its ``connections`` list mixes
local and remote entries, and which one is correct is a function of *where this
app runs*, not a preference:

* hosted  -> must skip ``local``; otherwise this server fires requests at
  192.168.x.x on its own LAN instead of the visitor's
* self-host -> prefers ``local``, which is faster and avoids a relay hop
"""

from __future__ import annotations

import urllib.parse
from typing import Any, NamedTuple

import httpx

from boxd_bridge.models import PlexServer

PLEX_TV = "https://plex.tv/api/v2"
PLEX_AUTH_APP = "https://app.plex.tv/auth"


class PlexAuthError(RuntimeError):
    """plex.tv rejected a request or returned something unusable."""


class PlexPin(NamedTuple):
    pin_id: str
    code: str


class PlexOAuthClient:
    def __init__(
        self, client: httpx.AsyncClient, *, product: str, client_identifier: str
    ) -> None:
        self._client = client
        self._product = product
        self._client_identifier = client_identifier

    def _headers(self, token: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-Plex-Product": self._product,
            "X-Plex-Version": "0.1.0",
            "X-Plex-Client-Identifier": self._client_identifier,
            "X-Plex-Device": "boxd-bridge",
            "X-Plex-Platform": "Web",
        }
        if token:
            headers["X-Plex-Token"] = token
        return headers

    async def create_pin(self) -> PlexPin:
        try:
            response = await self._client.post(
                f"{PLEX_TV}/pins",
                headers=self._headers(),
                data={"strong": "true"},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise PlexAuthError(f"could not create a Plex PIN: {exc}") from exc
        except ValueError as exc:
            raise PlexAuthError("plex.tv returned a non-JSON PIN response") from exc
        pin_id, code = payload.get("id"), payload.get("code")
        if not pin_id or not code:
            raise PlexAuthError("plex.tv PIN response was missing id or code")
        return PlexPin(str(pin_id), str(code))

    def build_auth_url(self, pin: PlexPin, forward_url: str) -> str:
        """Parameters go in the fragment, which is why this is built by hand."""
        fragment = urllib.parse.urlencode(
            {
                "clientID": self._client_identifier,
                "code": pin.code,
                "forwardUrl": forward_url,
                "context[device][product]": self._product,
            }
        )
        return f"{PLEX_AUTH_APP}#?{fragment}"

    async def exchange_pin(self, pin: PlexPin) -> str:
        try:
            response = await self._client.get(
                f"{PLEX_TV}/pins/{pin.pin_id}",
                headers=self._headers(),
                params={"code": pin.code},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise PlexAuthError(f"could not exchange the Plex PIN: {exc}") from exc
        except ValueError as exc:
            raise PlexAuthError("plex.tv returned a non-JSON PIN exchange") from exc
        token = payload.get("authToken")
        if not token:
            raise PlexAuthError(
                "Plex has not linked this PIN yet. Finish signing in, then retry."
            )
        return str(token)

    async def validate_token(self, token: str) -> dict[str, Any]:
        response = await self._client.get(f"{PLEX_TV}/user", headers=self._headers(token))
        if response.status_code == 401:
            raise PlexAuthError("Plex token is no longer valid")
        try:
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PlexAuthError(f"could not validate the Plex token: {exc}") from exc

    async def list_servers(self, token: str, *, prefer_local: bool) -> list[PlexServer]:
        try:
            response = await self._client.get(
                f"{PLEX_TV}/resources",
                headers=self._headers(token),
                params={"includeHttps": 1, "includeRelay": 1},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise PlexAuthError(f"could not list Plex servers: {exc}") from exc
        except ValueError as exc:
            raise PlexAuthError("plex.tv returned a non-JSON resources list") from exc

        servers: list[PlexServer] = []
        for resource in payload if isinstance(payload, list) else []:
            if not isinstance(resource, dict):
                continue
            if "server" not in (resource.get("provides") or ""):
                continue
            connection = select_connection(
                resource.get("connections"), prefer_local=prefer_local
            )
            if connection is None:
                continue
            servers.append(
                PlexServer(
                    name=str(resource.get("name") or "Plex Server"),
                    machine_identifier=str(resource.get("clientIdentifier") or ""),
                    uri=connection["uri"],
                    access_token=str(
                        resource.get("accessToken") or token
                    ),
                    local=bool(connection.get("local")),
                )
            )
        return servers


def select_connection(
    connections: Any, *, prefer_local: bool
) -> dict[str, Any] | None:
    """Pick the connection appropriate to where this app is running.

    When ``prefer_local`` is False (hosted), local connections are **excluded
    entirely**, not merely deprioritized: a hosted instance reaching
    192.168.x.x would be hitting its own LAN.
    """
    if not isinstance(connections, list):
        return None
    usable = [
        c
        for c in connections
        if isinstance(c, dict) and isinstance(c.get("uri"), str) and c["uri"]
    ]
    if not usable:
        return None

    if prefer_local:
        ordered = sorted(
            usable,
            key=lambda c: (
                not bool(c.get("local")),
                bool(c.get("relay")),
            ),
        )
        return ordered[0]

    remote = [c for c in usable if not c.get("local")]
    if not remote:
        return None
    # Prefer a direct remote connection over a relay: relays are bandwidth
    # limited and slower.
    remote.sort(key=lambda c: bool(c.get("relay")))
    return remote[0]
