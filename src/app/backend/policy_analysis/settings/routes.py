"""Read-only, masked effective configuration routes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request

from policy_analysis.auth.permissions import require_admin
from policy_analysis.auth.service import PublicUser
from policy_analysis.collectors.webfetch import WebFetchClient
from policy_analysis.core.settings import AppSettings, masked_settings

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
logger = logging.getLogger(__name__)

WebFetchReadyProbe = Callable[..., bool]


def probe_webfetch_ready(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float,
    max_attempts: int,
) -> bool:
    with WebFetchClient(
        base_url,
        api_key,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
    ) as client:
        return client.ready()


def get_webfetch_ready_probe() -> WebFetchReadyProbe:
    return probe_webfetch_ready


@router.get("/effective")
def effective_settings(
    request: Request,
    _admin: PublicUser = Depends(require_admin),
    ready_probe: WebFetchReadyProbe = Depends(get_webfetch_ready_probe),
) -> dict[str, object]:
    settings: AppSettings = request.app.state.settings
    sources: dict[str, str] = request.app.state.settings_sources
    api_key = settings.webfetch.api_key.get_secret_value()
    configured = bool(settings.webfetch.base_url and api_key)
    status = {"status": "not_configured", "checked": False}
    if configured:
        try:
            ready = ready_probe(
                base_url=settings.webfetch.base_url,
                api_key=api_key,
                timeout_seconds=min(settings.webfetch.timeout_seconds, 2.0),
                max_attempts=1,
            )
        except Exception:
            logger.warning("webfetch_ready_probe_failed")
            ready = False
        status = {"status": "ready" if ready else "unavailable", "checked": True}

    values = masked_settings(settings)
    _mask_userinfo_base_url(values)
    return {
        "values": values,
        "sources": sources,
        "webfetch": status,
    }


def _mask_userinfo_base_url(values: dict[str, object]) -> None:
    webfetch = values.get("webfetch")
    if not isinstance(webfetch, dict):
        return
    base_url = webfetch.get("base_url")
    if not isinstance(base_url, str):
        return
    try:
        parsed = urlsplit(base_url)
    except ValueError:
        parsed = None
    if parsed is not None and (parsed.username is not None or parsed.password is not None):
        webfetch["base_url"] = "********"
