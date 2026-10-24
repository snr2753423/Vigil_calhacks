from fastapi import APIRouter

from app import __version__
from app.config import get_settings
from app.state import sim_state
from app.ws.hub import hub, utc_now_iso

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "service": "vigil-backend",
        "version": __version__,
        "time": utc_now_iso(),
        "ws_clients": hub.client_count,
        "sim": sim_state.to_dict(),
        "configured": {
            "tiger": settings.tiger_configured,
            "snowflake": settings.snowflake_configured,
        },
    }
