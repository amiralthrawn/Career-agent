"""Schemas of application drafts. `claims`/`selected_evidence`/`warnings` are APP-derived (built
from `PersonalizationBrief`), never parsed out of the model's own text: they stay true regardless
of what the model's prose says. `subject` and `body` are the model's output (or, for `subject`,
assembled by the app): a human must review them before anything is used.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.models.enums import DraftKind, DraftStatus
from app.schemas.common import ORMModel
from app.schemas.personalization import BriefFact

ModelId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class ClaimRead(BaseModel):
    """One requirement the Brain truly covers, with the facts it rests on. Safe to assert."""

    requirement: str
    facts: list[BriefFact]


class WarningRead(BaseModel):
    """Something the draft must NOT assert as fact (a gap, a weak match, an open question)."""

    code: str
    requirement: str
    text: str


class ApplicationDraftRead(ORMModel):
    id: int
    target_id: int
    qualification_id: int | None
    kind: DraftKind
    status: DraftStatus
    model: str
    subject: str
    body: str
    claims: list[ClaimRead]
    selected_evidence: list[BriefFact]
    warnings: list[WarningRead]
    usage_prompt_tokens: int | None
    usage_completion_tokens: int | None
    duration_ms: int | None
    created_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    superseded_by_id: int | None


class DraftGenerateRequest(BaseModel):
    kind: DraftKind = DraftKind.APPLICATION_EMAIL
    model: ModelId | None = None  # override the LLM client's default model for this call only
