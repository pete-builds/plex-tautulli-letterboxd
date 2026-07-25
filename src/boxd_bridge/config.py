"""Application settings and the start-up validation that enforces each mode's contract.

Two independent axes, deliberately not conflated:

* ``AUTH_MODE``   -- ``env`` (single tenant, no login) or ``plex-oauth`` (multi tenant)
* source          -- ``tautulli`` or ``plex``; derived, not configured directly

Anything missing raises at import/startup rather than serving a half-working app.
"""

from __future__ import annotations

import enum
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthMode(enum.StrEnum):
    ENV = "env"
    PLEX_OAUTH = "plex-oauth"


class SourceKind(enum.StrEnum):
    TAUTULLI = "tautulli"
    PLEX = "plex"


class ConfigError(RuntimeError):
    """Raised when the configured mode is missing something it cannot run without."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    auth_mode: AuthMode = AuthMode.ENV

    # --- Source: Tautulli (single tenant only) ---
    tautulli_url: str | None = None
    tautulli_apikey: str | None = None

    # --- Source: Plex direct (single tenant only) ---
    # PLEX_URL is env-only and is never accepted from the browser. Letting a
    # hosted instance fetch a user-supplied URL would be textbook SSRF.
    plex_url: str | None = None
    plex_token: str | None = None

    # --- Transform ---
    display_timezone: str = "UTC"
    completion_threshold: int = Field(default=85, ge=0, le=100)
    csv_chunk_bytes: int = Field(default=900_000, ge=1_024, le=1_000_000)

    # Off by default. Neither source can return a *specific* user's star rating:
    # both hand back whichever account owns the token. Exporting that into
    # someone else's diary would be a false statement, so opting in is explicit
    # and rows that cannot be attributed stay empty regardless.
    export_ratings: bool = False

    # --- Hosted mode (plex-oauth) ---
    session_secret: str | None = None
    session_ttl_seconds: int = Field(default=1800, ge=60, le=86_400)
    cookie_secure: bool = True
    public_base_url: str | None = None
    app_product_name: str = "boxd-bridge"

    # --- Rate limiting (applies to the PIN-creation endpoint) ---
    rate_limit_requests: int = Field(default=5, ge=1)
    rate_limit_window_seconds: int = Field(default=300, ge=1)

    # --- Server ---
    bind_host: str = "0.0.0.0"
    bind_port: int = 8000
    request_timeout_seconds: float = Field(default=20.0, gt=0)

    @model_validator(mode="after")
    def _validate_mode(self) -> Settings:
        try:
            ZoneInfo(self.display_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(
                f"DISPLAY_TIMEZONE={self.display_timezone!r} is not a valid IANA timezone"
            ) from exc

        if self.auth_mode is AuthMode.ENV:
            if not (self.has_tautulli or self.has_plex_env):
                raise ConfigError(
                    "AUTH_MODE=env requires either TAUTULLI_URL + TAUTULLI_APIKEY "
                    "or PLEX_URL + PLEX_TOKEN."
                )
        else:
            missing = [
                name
                for name, value in (
                    ("SESSION_SECRET", self.session_secret),
                    ("PUBLIC_BASE_URL", self.public_base_url),
                )
                if not value
            ]
            if missing:
                raise ConfigError(
                    f"AUTH_MODE=plex-oauth requires {' and '.join(missing)}."
                )
            if self.session_secret and len(self.session_secret) < 32:
                raise ConfigError("SESSION_SECRET must be at least 32 characters.")
        return self

    @property
    def has_tautulli(self) -> bool:
        return bool(self.tautulli_url and self.tautulli_apikey)

    @property
    def has_plex_env(self) -> bool:
        return bool(self.plex_url and self.plex_token)

    @property
    def source_kind(self) -> SourceKind:
        """Hosted mode is always Plex. Self-host prefers Tautulli when configured.

        Tautulli only makes sense single tenant: you cannot OAuth into someone
        else's Tautulli.
        """
        if self.auth_mode is AuthMode.PLEX_OAUTH:
            return SourceKind.PLEX
        return SourceKind.TAUTULLI if self.has_tautulli else SourceKind.PLEX

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.display_timezone)

    @property
    def prefer_local_connections(self) -> bool:
        """Self-host prefers a LAN connection; hosted must never use one.

        In hosted mode a ``local`` connection URI means this server firing
        requests at 192.168.x.x on *its own* LAN, not the visitor's.
        """
        return self.auth_mode is AuthMode.ENV


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
