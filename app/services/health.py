from app.core.config import Settings
from app.schemas.health import HealthResponse


def get_health_status(settings: Settings) -> HealthResponse:
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )
