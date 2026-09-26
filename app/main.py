import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import ConfigurationError, Settings, get_settings
from app.core.database import get_session_factory
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.models.audit import AuditEventType
from app.services.audit import AuditLog

logger = logging.getLogger("career_agent")


def record_startup(settings: Settings) -> None:
    """Audit the start of the application with its send mode.

    Skipped when no database is configured. When sending is enabled the audit is mandatory:
    if it cannot be written, the application refuses to start.
    """
    if not settings.database_url:
        return
    try:
        with get_session_factory()() as session:
            AuditLog(session).record(
                AuditEventType.APP_STARTED,
                actor="system",
                details={"mode": settings.send_mode.value, "app_version": settings.app_version},
            )
    except Exception:
        if settings.send_mode.value != "disabled":
            raise
        logger.warning("Startup audit event could not be recorded")  # no details, on purpose


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        record_startup(settings)
        yield

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.app_debug,
        lifespan=lifespan,
    )

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    # Added last so it runs first: a request with an unexpected Host header is refused
    # before anything else (protection against DNS rebinding).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)

    app.include_router(api_router)

    @app.exception_handler(NotFoundError)
    def _not_found(_: Request, error: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @app.exception_handler(ConflictError)
    def _conflict(_: Request, error: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(UnprocessableError)
    def _unprocessable(_: Request, error: UnprocessableError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(error)})

    @app.exception_handler(ConfigurationError)
    def _not_configured(_: Request, error: ConfigurationError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(error)})

    return app


app = create_app()
