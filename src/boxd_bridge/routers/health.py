"""Liveness endpoint. Auth-exempt by design, and it reveals no configuration."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from boxd_bridge import __version__
from boxd_bridge.config import Settings
from boxd_bridge.deps import get_settings_dep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict[str, str]:
    return {
        "status": "ok",
        "version": __version__,
        "auth_mode": settings.auth_mode.value,
        "source": settings.source_kind.value,
    }
