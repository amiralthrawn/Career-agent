"""Generate an application draft from a PersonalizationBrief. Human-in-the-loop, never auto-sent.

Target -> generate -> ApplicationDraft(status=proposed) -> a human approves or rejects it.
Nothing here ever calls a mail provider: sending is a later, separate step (`MailSender`).

**The LLM is a generator, never a source of truth.** `claims`, `selected_evidence` and `warnings`
are built by THIS module directly from the brief (`RequirementMatch` + Candidate Brain evidence
states): they are true regardless of what the model's prose says, because they never come from
parsing that prose. Only `subject` (assembled here from already-validated names) and `body` (the
model's own text) need a human's review before anything is used.

The model receives a bounded, JSON-serialisable CONTEXT built only from the brief: covered
requirements with their supporting facts, and what must NOT be claimed (gaps, weak matches, an
explicit instruction never to mention them). It never sees the raw database, an offer's full text,
or anything beyond what the brief already vetted.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, UnprocessableError
from app.core.normalize import normalize_text
from app.integrations.llm.ports import GenerationRequest, LLMClient, LLMError
from app.models import ApplicationDraft
from app.models.audit import AuditEventType
from app.models.enums import DraftStatus
from app.repositories import candidate_brain as brain_repo
from app.repositories import drafts as repo
from app.repositories.targets import stage
from app.schemas.drafts import DraftGenerateRequest
from app.schemas.personalization import BriefTarget, DoNotClaim, PersonalizationBrief, Strength
from app.services.audit import AuditLog
from app.services.personalization import PersonalizationService

MAX_CONTEXT_STRENGTHS = 20
MAX_CONTEXT_DO_NOT_CLAIM = 20
MAX_SUBJECT_CHARS = 200

SYSTEM_PROMPT_V1 = (
    "You write the BODY of a job application e-mail in English, in a professional and concise "
    "tone. Use ONLY the facts listed under 'strengths' as things the candidate may truthfully "
    "claim; each already carries the evidence that supports it. NEVER mention, imply or hint at "
    "anything listed under 'do_not_claim': it is not established and must not be presented as "
    "fact. Never invent a company detail, technology, mission, responsibility or experience "
    "that is not present in the given context. If the context is too thin to make a compelling "
    "case, say so plainly instead of inventing. Output the body text only, no subject line, no "
    "salutation placeholders beyond a generic greeting."
)


def _fact_dict(fact: Any) -> dict[str, Any]:
    return {
        "type": fact.type.value,
        "id": fact.id,
        "name": fact.name,
        "state": fact.state.value,
        "role": fact.role.value,
    }


def _strength_dict(strength: Strength) -> dict[str, Any]:
    return {
        "requirement": strength.requirement.label,
        "facts": [_fact_dict(fact) for fact in strength.facts],
    }


def _build_claims(strengths: list[Strength]) -> list[dict[str, Any]]:
    return [_strength_dict(strength) for strength in strengths]


def _build_evidence(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicated facts referenced by `claims`: exactly what was exposed as evidence."""
    seen: dict[tuple[str, int], dict[str, Any]] = {}
    for claim in claims:
        for fact in claim["facts"]:
            seen[(fact["type"], fact["id"])] = fact
    return [seen[key] for key in sorted(seen)]


def _build_warnings(brief: PersonalizationBrief) -> list[dict[str, Any]]:
    warnings = [
        {"code": d.status.value, "requirement": d.requirement.label, "text": d.text}
        for d in brief.do_not_claim
    ]
    warnings += [
        {"code": "open_question", "requirement": q.requirement.label, "text": q.text}
        for q in brief.open_questions
    ]
    return warnings


def build_context(brief: PersonalizationBrief) -> dict[str, Any]:
    """Bounded, JSON-serialisable: the only thing the model ever sees of this candidate/company."""
    company_context = {
        key: value
        for key, value in (
            ("sector", brief.company_context.sector),
            ("location", brief.company_context.location),
            ("country_code", brief.company_context.country_code),
            ("offer_location", brief.company_context.offer_location),
            ("offer_remote_mode", brief.company_context.offer_remote_mode),
        )
        if value is not None
    }
    return {
        "mode": brief.target.mode,
        "company_name": brief.target.company.name,
        "offer_title": brief.target.offer.title if brief.target.offer else None,
        "contract_type": brief.target.contract_type,
        "strengths": [_strength_dict(s) for s in brief.strengths[:MAX_CONTEXT_STRENGTHS]],
        "do_not_claim": [
            {"requirement": d.requirement.label, "status": d.status.value}
            for d in brief.do_not_claim[:MAX_CONTEXT_DO_NOT_CLAIM]
        ],
        "company_context": company_context,
    }


def build_subject(target: BriefTarget) -> str:
    """Assembled by the app from already-validated names: the model never writes the subject."""
    if target.offer is not None:
        subject = f"Application for {target.offer.title} at {target.company.name}"
    else:
        subject = f"Spontaneous application to {target.company.name}"
    return subject[:MAX_SUBJECT_CHARS]


def scan_forbidden_mentions(body: str, do_not_claim: list[DoNotClaim]) -> list[str]:
    """Literal, best-effort check: a `do_not_claim` label found verbatim in the generated text.

    Not a semantic guarantee - a model could still make an unestablished claim without using the
    requirement's exact wording. This never blocks generation; it only adds a warning so a human
    reviewer notices it. Human review stays mandatory regardless of this check's result.
    """
    padded = f" {normalize_text(body)} "
    return [
        d.requirement.label
        for d in do_not_claim
        if (label := normalize_text(d.requirement.label)) and f" {label} " in padded
    ]


class DraftService:
    def __init__(self, session: Session, llm: LLMClient | None) -> None:
        self._session = session
        self._llm = llm
        self._personalization = PersonalizationService(session)

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    # --- generation ---------------------------------------------------------------------

    def generate(
        self, target_id: int, data: DraftGenerateRequest, *, actor: str = "api"
    ) -> ApplicationDraft:
        if self._llm is None:
            raise UnprocessableError("No LLM client is configured")
        # `PersonalizationService.brief` raises NotFoundError if the target was never qualified;
        # its ownership check (candidate <-> target) is reused as-is, nothing is duplicated here.
        brief = self._personalization.brief(target_id)
        if brief.qualification.stale:
            # The documented contract of docs/requirements.md: a stale brief must not be used to
            # generate content. Re-qualifying the target produces a current one.
            raise UnprocessableError(
                "The qualification is stale: re-qualify the target before generating a draft"
            )

        claims = _build_claims(brief.strengths)
        warnings = _build_warnings(brief)
        request = GenerationRequest(
            task=data.kind.value,
            system=SYSTEM_PROMPT_V1,
            context=build_context(brief),
            model=data.model,
        )
        try:
            result = self._llm.generate(request)
        except LLMError as error:
            AuditLog(self._session).record(
                AuditEventType.DRAFT_GENERATED,
                actor=actor,
                subject=f"target:{target_id}",
                details={
                    "reason": f"llm_error:{error.code.value}"[:64],
                    "model": (data.model or "unset")[:64],
                },
            )
            self._session.commit()
            raise UnprocessableError("The LLM call failed") from error

        for label in scan_forbidden_mentions(result.text, brief.do_not_claim):
            warnings.append(
                {
                    "code": "forbidden_mention_detected",
                    "requirement": label,
                    "text": (
                        f"The generated text appears to mention '{label}', which is not "
                        "established: review carefully before approving."
                    ),
                }
            )

        previous = repo.find_pending(self._session, target_id, data.kind)
        if previous is not None:
            previous.status = DraftStatus.SUPERSEDED
            self._session.flush()  # frees the "one pending draft" slot before the new row exists
        draft = stage(
            self._session,
            ApplicationDraft(
                candidate_id=self._candidate_id(),
                target_id=target_id,
                qualification_id=brief.qualification.id,
                kind=data.kind,
                model=result.model,
                subject=build_subject(brief.target),
                body=result.text,
                claims=claims,
                selected_evidence=_build_evidence(claims),
                warnings=warnings,
                usage_prompt_tokens=result.usage.prompt_tokens if result.usage else None,
                usage_completion_tokens=result.usage.completion_tokens if result.usage else None,
                duration_ms=result.duration_ms,
            ),
        )
        self._session.flush()
        if previous is not None:
            previous.superseded_by_id = draft.id
        AuditLog(self._session).record(
            AuditEventType.DRAFT_GENERATED,
            actor=actor,
            subject=f"target:{target_id}",
            details={"reason": "generated", "model": result.model[:64]},
        )
        self._session.commit()
        self._session.refresh(draft)
        return draft

    # --- read and decide ------------------------------------------------------------------

    def get(self, draft_id: int) -> ApplicationDraft:
        draft = repo.get_draft(self._session, self._candidate_id(), draft_id)
        if draft is None:
            raise NotFoundError(f"Draft {draft_id} not found")
        return draft

    def decide(self, draft_id: int, *, approve: bool, actor: str = "api") -> ApplicationDraft:
        draft = self.get(draft_id)
        if draft.status is not DraftStatus.PROPOSED:
            raise UnprocessableError("Only a proposed draft can be approved or rejected")
        draft.status = DraftStatus.APPROVED if approve else DraftStatus.REJECTED
        draft.decided_at = datetime.now(UTC)
        draft.decided_by = actor
        AuditLog(self._session).record(
            AuditEventType.DRAFT_DECIDED,
            actor=actor,
            subject=f"draft:{draft_id}",
            details={"reason": "approved" if approve else "rejected"},
        )
        self._session.commit()
        self._session.refresh(draft)
        return draft
