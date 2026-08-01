"""Read-only, masked effective configuration routes."""

from fastapi import APIRouter, Depends, Request

from policy_analysis.auth.permissions import require_admin
from policy_analysis.auth.service import PublicUser
from policy_analysis.core.settings import AppSettings, masked_settings

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@router.get("/effective")
def effective_settings(
    request: Request,
    _admin: PublicUser = Depends(require_admin),
) -> dict[str, object]:
    settings: AppSettings = request.app.state.settings
    sources: dict[str, str] = request.app.state.settings_sources
    configured = bool(settings.webfetch.base_url and settings.webfetch.api_key.get_secret_value())
    return {
        "values": masked_settings(settings),
        "sources": sources,
        "webfetch": {
            "status": "configured" if configured else "not_configured",
            "checked": False,
        },
    }
