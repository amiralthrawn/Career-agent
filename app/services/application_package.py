"""Application package (step 9): a Target turned into an exploitable, reviewable candidature.

Target -> Company/Opportunity -> Qualification + RequirementMatch (step 3) -> Company research
(step 7) -> GitHub evidence (step 9) -> accepted Contact (step 8) -> selected evidence ->
`ApplicationDraft` (step 4, reused unchanged) -> human validation.

**This module never invents anything.** Company evidence comes only from already-accepted
`CompanyResearchFact` rows; contact context comes only from an ACCEPTED `Contact` (never a
`pending` `ContactResearchObservation`); GitHub evidence comes only from the candidate's own
public repositories, matched deterministically against requirement labels
(`app.services.github_evidence`), and is always presented as a personal project, never
professional experience. If a source is unavailable (no accepted contact, GitHub down, no
CompanyResearchFact yet), the package is still prepared with the rest - nothing here ever blocks
on a missing OPTIONAL source; only a missing/stale qualification blocks (same rule as step 4).

**Evidence-first, then generation.** Everything below is computed BEFORE the existing
`DraftService.generate()` is called; the LLM receives it as an already-vetted `extra_context`
(see `app.services.draft_generation`) and never decides what counts as evidence.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.integrations.github.ports import GitHubError, GitHubProvider, GitHubStatus
from app.integrations.llm.ports import LLMClient
from app.models import (
    ApplicationPackage,
    CompanyResearchFact,
    Contact,
    ContactChannel,
    DocumentIngestion,
    Qualification,
    Target,
)
from app.models.audit import AuditEventType
from app.models.enums import ApplicationPackageStatus, ChannelKind, InfoStatus, RoleCategory
from app.repositories import application_package as repo
from app.repositories import candidate_brain as brain_repo
from app.repositories.targets import (
    get_company,
    get_contact,
    get_target,
    list_target_contacts,
    stage,
)
from app.schemas.application_package import ApplicationPrepareRequest
from app.schemas.drafts import DraftGenerateRequest
from app.schemas.personalization import PersonalizationBrief
from app.services.audit import AuditLog
from app.services.draft_generation import DraftService
from app.services.github_evidence import GitHubEvidence, match_repositories
from app.services.personalization import PersonalizationService

MAX_COMPANY_FACTS = 3  # "quelques éléments très pertinents suffisent" - never a data dump

# A fixed, deterministic tone hint per role category. Never a fact about the person: only a
# generic instruction to the generator about WHICH ANGLE to lead with (see the module docstring
# example: recruiter/HR -> role fit, manager -> team mission, tech -> a project, founder ->
# direct/contribution-oriented). `UNKNOWN` and any category without a specific angle stay generic.
APPROACH_HINT: dict[RoleCategory, str] = {
    RoleCategory.RECRUITER: "emphasize interest in the role and fit with the requirements",
    RoleCategory.HR: "emphasize interest in the role and fit with the requirements",
    RoleCategory.MANAGER: "emphasize interest in the team's mission and challenges",
    RoleCategory.TECH: "highlight one genuinely relevant technical project",
    RoleCategory.FOUNDER: "be direct and contribution-oriented",
    RoleCategory.OTHER: "keep a general, professional tone",
    RoleCategory.UNKNOWN: "keep a general, professional tone",
}


@dataclass
class _Inputs:
    target: Target
    brief: PersonalizationBrief
    company_facts: list[CompanyResearchFact]
    contact: Contact | None
    contact_context: dict[str, Any] | None
    github_evidence: list[GitHubEvidence]
    cv_document: DocumentIngestion | None
    warnings: list[dict[str, str]] = field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        payload = {
            "qualification": self.brief.qualification.inputs_fingerprint,
            "company_fact_ids": sorted(f.id for f in self.company_facts),
            "contact_id": self.contact.id if self.contact else None,
            "contact_updated_at": (self.contact.updated_at.isoformat() if self.contact else None),
            "github_repos": sorted(
                f"{e.repository_name}|{e.description}|{e.primary_language}"
                for e in self.github_evidence
            ),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()


def _company_fact_dict(fact: CompanyResearchFact) -> dict[str, Any]:
    return {
        "claim": fact.claim,
        "source_url": fact.source_url,
        "source_title": fact.source_title,
        "excerpt": fact.excerpt,
        "published": fact.published,
    }


def _github_evidence_dict(evidence: GitHubEvidence) -> dict[str, Any]:
    return {
        "rank": evidence.rank,
        "repository_name": evidence.repository_name,
        "url": evidence.url,
        "description": evidence.description,
        "primary_language": evidence.primary_language,
        "matched_requirement_labels": list(evidence.matched_requirement_labels),
        "matched_terms": list(evidence.matched_terms),
        "source_url": evidence.source_url,
        "is_personal_project": evidence.is_personal_project,
    }


def best_contact_channel(contact: Contact) -> ContactChannel | None:
    """Any channel, for DISPLAY in the personalization context (e-mail preferred, then any
    other kind). For deciding an actual SEND target, use `best_email_channel` instead - step 10
    never sends to a phone number or a LinkedIn URL."""
    if not contact.channels:
        return None
    return sorted(
        contact.channels,
        key=lambda c: (c.kind is not ChannelKind.EMAIL, c.status is not InfoStatus.FOUND, c.id),
    )[0]


def best_email_channel(contact: Contact) -> ContactChannel | None:
    """The channel a send would actually use: EMAIL kind only, `found` preferred over
    `uncertain`, lowest id as a deterministic tie-break. `None` if the contact has no e-mail at
    all - reused by `app.services.send_batch`, never re-derived or guessed there."""
    emails = [c for c in contact.channels if c.kind is ChannelKind.EMAIL]
    if not emails:
        return None
    return sorted(emails, key=lambda c: (c.status is not InfoStatus.FOUND, c.id))[0]


def _contact_context_dict(contact: Contact) -> dict[str, Any]:
    channel = best_contact_channel(contact)
    return {
        "contact_id": contact.id,
        "full_name": contact.full_name,
        "is_generic": contact.is_generic,
        "role_title": contact.role_title,
        "role_category": contact.role_category.value,
        "channel": (
            {"kind": channel.kind.value, "value": channel.value, "status": channel.status.value}
            if channel
            else None
        ),
        "approach_hint": APPROACH_HINT[contact.role_category],
    }


def _requirement_labels(brief: PersonalizationBrief) -> list[str]:
    """Every requirement label the qualification produced, covered or not: a GitHub project may
    supply supplementary evidence even for a gap the Brain itself could not confirm (see the
    module docstring's "Commodity-Arbitrage-Analysis" example)."""
    labels = [s.requirement.label for s in brief.strengths]
    labels += [d.requirement.label for d in brief.do_not_claim]
    return labels


class ApplicationPackageService:
    def __init__(
        self,
        session: Session,
        llm: LLMClient | None,
        github: GitHubProvider | None,
        github_username: str | None,
    ) -> None:
        self._session = session
        self._llm = llm
        self._github = github
        self._github_username = github_username
        self._personalization = PersonalizationService(session)
        self._drafts = DraftService(session, llm)

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    # --- evidence gathering (read-only, no writes) --------------------------------------

    def _accepted_contact(self, target: Target) -> Contact | None:
        links = sorted(
            list_target_contacts(self._session, target.id),
            key=lambda link: (not link.is_primary, link.contact_id),
        )
        for link in links:
            contact = get_contact(self._session, link.contact_id)
            if contact is not None and not contact.do_not_contact:
                return contact
        return None

    def _github_evidence(
        self, requirement_labels: list[str]
    ) -> tuple[list[GitHubEvidence], dict[str, str] | None]:
        if self._github is None or not self._github_username:
            return [], None  # capability off: absence, never a warning
        try:
            result = self._github.research(self._github_username)
        except GitHubError:
            return [], {
                "code": "github_unavailable",
                "text": "GitHub research failed; the package was prepared without it.",
            }
        if result.status is GitHubStatus.NO_RESULTS:
            return [], None
        return match_repositories(result.repositories, requirement_labels), None

    def _gather(self, target_id: int, profile_id: int | None) -> _Inputs:
        candidate_id = self._candidate_id()
        target = get_target(self._session, candidate_id, target_id)
        if target is None:
            raise NotFoundError(f"Target {target_id} not found")
        brief = self._personalization.brief(target_id, profile_id)
        company = get_company(self._session, target.company_id)
        assert company is not None  # a target always owns a company
        company_facts = list(company.research_facts)[:MAX_COMPANY_FACTS]

        contact = self._accepted_contact(target)
        warnings: list[dict[str, str]] = []
        if contact is None:
            warnings.append(
                {
                    "code": "no_accepted_contact",
                    "text": "No accepted contact for this target; the package has none.",
                }
            )

        github_evidence, github_warning = self._github_evidence(_requirement_labels(brief))
        if github_warning:
            warnings.append(github_warning)

        cv_document = repo.latest_cv_ingestion(self._session, candidate_id)
        if cv_document is None:
            warnings.append(
                {
                    "code": "no_cv_reference",
                    "text": "No ingested CV document found; the package has no CV reference.",
                }
            )

        return _Inputs(
            target=target,
            brief=brief,
            company_facts=company_facts,
            contact=contact,
            contact_context=_contact_context_dict(contact) if contact else None,
            github_evidence=github_evidence,
            cv_document=cv_document,
            warnings=warnings,
        )

    # --- prepare (writes) -----------------------------------------------------------------

    def prepare(
        self, target_id: int, data: ApplicationPrepareRequest, *, actor: str = "api"
    ) -> ApplicationPackage:
        inputs = self._gather(target_id, data.profile_id)
        if inputs.brief.qualification.stale:
            raise UnprocessableError(
                "The qualification is stale: re-qualify the target before preparing a package"
            )

        existing = repo.find_pending(self._session, target_id)
        if (
            existing is not None
            and existing.status is ApplicationPackageStatus.PENDING_VALIDATION
            and existing.inputs_fingerprint == inputs.fingerprint
        ):
            return existing  # idempotent: nothing changed, no wasted LLM call

        extra_context: dict[str, Any] = {}
        if inputs.company_facts:
            extra_context["company_evidence"] = [
                _company_fact_dict(f) for f in inputs.company_facts
            ]
        if inputs.contact_context:
            extra_context["contact"] = inputs.contact_context
        if inputs.github_evidence:
            extra_context["github_evidence"] = [
                _github_evidence_dict(e) for e in inputs.github_evidence
            ]

        draft_id = None
        status = ApplicationPackageStatus.DRAFT
        if self._llm is not None:
            draft = self._drafts.generate(
                target_id,
                DraftGenerateRequest(model=data.model),
                actor=actor,
                extra_context=extra_context or None,
            )
            draft_id = draft.id
            status = ApplicationPackageStatus.PENDING_VALIDATION

        if existing is not None:
            existing.status = ApplicationPackageStatus.SUPERSEDED
            self._session.flush()  # frees the "one pending package" slot before the new row exists

        package = stage(
            self._session,
            ApplicationPackage(
                candidate_id=self._candidate_id(),
                target_id=target_id,
                qualification_id=inputs.brief.qualification.id,
                draft_id=draft_id,
                cv_document_id=inputs.cv_document.id if inputs.cv_document else None,
                contact_id=inputs.contact.id if inputs.contact else None,
                status=status,
                personalization_context={
                    "company_evidence": [_company_fact_dict(f) for f in inputs.company_facts],
                    "contact": inputs.contact_context,
                    "github_evidence": [_github_evidence_dict(e) for e in inputs.github_evidence],
                },
                warnings=inputs.warnings,
                inputs_fingerprint=inputs.fingerprint,
            ),
        )
        self._session.flush()
        if existing is not None:
            existing.superseded_by_id = package.id
        AuditLog(self._session).record(
            AuditEventType.APPLICATION_PREPARED,
            actor=actor,
            subject=f"target:{target_id}",
            details={
                "reason": "prepared",
                "rows": len(inputs.company_facts),
                "created": len(inputs.github_evidence),
                "matched": 1 if inputs.contact else 0,
            },
        )
        self._session.commit()
        self._session.refresh(package)
        return package

    # --- read and decide --------------------------------------------------------------------

    def get(self, package_id: int) -> ApplicationPackage:
        package = repo.get_package(self._session, self._candidate_id(), package_id)
        if package is None:
            raise NotFoundError(f"Application package {package_id} not found")
        return package

    def list_ready_to_send(self) -> Sequence[ApplicationPackage]:
        return repo.list_ready_to_send(self._session, self._candidate_id())

    def cv_source_uri(self, package: ApplicationPackage) -> str | None:
        if package.cv_document_id is None:
            return None
        document = self._session.get(DocumentIngestion, package.cv_document_id)
        return document.source_uri if document is not None else None

    def is_stale(self, package: ApplicationPackage) -> bool:
        profile_id = None
        if package.qualification_id is not None:
            qualification = self._session.get(Qualification, package.qualification_id)
            if qualification is not None:
                profile_id = qualification.profile_id
        try:
            current = self._gather(package.target_id, profile_id)
        except (NotFoundError, UnprocessableError):
            return True  # the target or its qualification no longer supports this package as-is
        if current.brief.qualification.stale:
            # The Candidate Brain (or the criteria/requirements) changed since this package's
            # own qualification ran, but no NEW `Qualification` row exists yet: the package's
            # `inputs_fingerprint` (built from that unchanged row's own fingerprint) would
            # otherwise look identical - this check catches it anyway. `prepare()` already
            # refuses to build a package from a stale brief in the first place; this is what
            # lets an ALREADY-approved package be caught too, once its qualification goes stale.
            return True
        return current.fingerprint != package.inputs_fingerprint

    def decide(self, package_id: int, *, approve: bool, actor: str = "api") -> ApplicationPackage:
        package = self.get(package_id)
        if package.status is not ApplicationPackageStatus.PENDING_VALIDATION:
            raise ConflictError(
                "Only a package pending validation can be approved or rejected"
                f" (currently {package.status.value})"
            )
        package.status = (
            ApplicationPackageStatus.APPROVED if approve else ApplicationPackageStatus.REJECTED
        )
        package.decided_at = datetime.now(UTC)
        package.decided_by = actor
        AuditLog(self._session).record(
            AuditEventType.APPLICATION_DECIDED,
            actor=actor,
            subject=f"application:{package_id}",
            details={"reason": "approved" if approve else "rejected"},
        )
        self._session.commit()
        self._session.refresh(package)
        return package
