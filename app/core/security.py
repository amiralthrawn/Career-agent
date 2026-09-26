"""Protection of the local API.

Every `/api/*` route requires `Authorization: Bearer <API_TOKEN>`. The token is compared in
constant time, is never logged and is never included in a response. Without a configured
token the API fails closed (503). `/health` stays public. The `Host` header is restricted
separately by `TrustedHostMiddleware` (see `app.main`).
"""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings

bearer_scheme = HTTPBearer(auto_error=False, description="Local API token (API_TOKEN in .env)")


def require_api_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if settings.api_token is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "API token is not configured: the API refuses every request",
        )
    provided = credentials.credentials if credentials is not None else ""
    if not secrets.compare_digest(
        provided.encode("utf-8"), settings.api_token.get_secret_value().encode("utf-8")
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing or invalid API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
