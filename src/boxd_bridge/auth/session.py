"""Signed + encrypted session cookies.

The user's Plex token is never written to disk. It lives inside a Fernet token
in the user's own cookie, which gives us authenticated encryption and a built-in
issue timestamp we enforce a TTL against on read. Nothing at rest means there is
no token store to leak and nothing to clean up on teardown.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class SessionInvalid(Exception):
    """Cookie was missing, malformed, or not authentic."""


class SessionExpired(SessionInvalid):
    """Cookie was authentic but older than the configured TTL."""


def _derive_key(secret: str, salt: bytes) -> bytes:
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"boxd-bridge-session-v1",
    ).derive(secret.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


class SessionCodec:
    def __init__(self, secret: str, ttl_seconds: int) -> None:
        if not secret or len(secret) < 32:
            raise ValueError("session secret must be at least 32 characters")
        self._fernet = Fernet(_derive_key(secret, b"boxd-bridge-session-salt"))
        self._ttl = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def encode(self, payload: dict[str, Any]) -> str:
        blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(blob).decode("ascii")

    def decode(self, token: str | None, *, ttl_seconds: int | None = None) -> dict[str, Any]:
        if not token:
            raise SessionInvalid("no session cookie")
        ttl = self._ttl if ttl_seconds is None else ttl_seconds
        try:
            blob = self._fernet.decrypt(token.encode("ascii"), ttl=ttl)
        except InvalidToken as exc:
            # Fernet does not distinguish "expired" from "forged" in its
            # exception type; both are equally unusable, so both are rejected.
            raise SessionExpired("session cookie is expired or invalid") from exc
        except (UnicodeEncodeError, ValueError) as exc:
            raise SessionInvalid("malformed session cookie") from exc
        try:
            data = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise SessionInvalid("session payload was not valid JSON") from exc
        if not isinstance(data, dict):
            raise SessionInvalid("session payload was not an object")
        return data
