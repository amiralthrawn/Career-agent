"""Application draft routes (step 4): generate, read, approve, reject. No send route here.

The LLM client comes from an injectable dependency, `None` unless the operator explicitly turned
`LLM_ENABLED=true` on AND set `OPENROUTER_MODEL` AND stored the `openrouter_api_key` secret: by
default every generation is refused (422), so nothing depends on OpenRouter in production yet.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.secrets import OPENROUTER_API_KEY, SecretStoreError, get_secret_store
from app.integrations.llm.openrouter import OpenRouterClient
from app.integrations.llm.ports import LLMClient
from app.schemas.drafts import ApplicationDraftRead, DraftGenerateRequest
from app.services.draft_generation import DraftService

router = APIRouter(prefix="/api", tags=["drafts"])

DbSession = Annotated[Session, Depends(get_db)]


def get_llm_client(settings: Annotated[Settings, Depends(get_settings)]) -> LLMClient | None:
    """`None` by default: draft generation is then refused, explicitly, before anything runs."""
    if not settings.llm_enabled or not settings.openrouter_model:
        return None
    try:
        store = get_secret_store()
        if not store.exists(OPENROUTER_API_KEY):
            return None
    except SecretStoreError:
        return None  # fail closed: no secure backend, no client
    return OpenRouterClient(store, settings.openrouter_model)


LLM = Annotated[LLMClient | None, Depends(get_llm_client)]


@router.post("/targets/{target_id}/drafts", response_model=ApplicationDraftRead, status_code=201)
def generate_draft(
    target_id: int, data: DraftGenerateRequest, session: DbSession, llm: LLM
) -> ApplicationDraftRead:
    """Generate a draft (`proposed`). Refused (422) if the target's qualification is missing or
    stale, or if no LLM client is configured. Never sends anything."""
    draft = DraftService(session, llm).generate(target_id, data, actor="api")
    return ApplicationDraftRead.model_validate(draft)


@router.get("/drafts/{draft_id}", response_model=ApplicationDraftRead)
def get_draft(draft_id: int, session: DbSession) -> ApplicationDraftRead:
    return ApplicationDraftRead.model_validate(DraftService(session, None).get(draft_id))


@router.post("/drafts/{draft_id}/approve", response_model=ApplicationDraftRead)
def approve_draft(draft_id: int, session: DbSession) -> ApplicationDraftRead:
    draft = DraftService(session, None).decide(draft_id, approve=True, actor="api")
    return ApplicationDraftRead.model_validate(draft)


@router.post("/drafts/{draft_id}/reject", response_model=ApplicationDraftRead)
def reject_draft(draft_id: int, session: DbSession) -> ApplicationDraftRead:
    draft = DraftService(session, None).decide(draft_id, approve=False, actor="api")
    return ApplicationDraftRead.model_validate(draft)
