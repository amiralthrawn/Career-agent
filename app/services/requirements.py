"""Requirements of a target: explicit extraction from the offer, manual entry, versioning.

- Nothing happens by itself: requirements are created only by `extract` (an explicit call) or by a
  manual entry. Reading a target, qualifying it or building a brief never extracts anything.
- Requirements are append-only. A changed requirement is a NEW row that supersedes the old one
  (which is deactivated, not deleted), so what a past qualification saw stays readable.
- Extracting the same content twice creates nothing (idempotent). A requirement written by a human
  is never overwritten by an extraction.
- A spontaneous target has no offer text: extraction reports `no_offer` and creates nothing;
  `requirements_total = 0` is a normal state, not an error.
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, UnprocessableError
from app.core.normalize import normalize_text
from app.models import Experience, Project, Skill, Target, TargetRequirement
from app.models.audit import AuditEventType
from app.models.enums import (
    EvidenceTargetType,
    RequirementKind,
    RequirementOrigin,
)
from app.models.sources import SourceSpec
from app.repositories import candidate_brain as brain_repo
from app.repositories import requirements as repo
from app.repositories import targets as target_repo
from app.schemas.requirements import (
    ExtractionOutcome,
    ExtractionReport,
    ManualRequirementCreate,
    RequirementRead,
)
from app.services.audit import AuditLog
from app.services.requirement_extraction import (
    EXPERIENCE_KEY_PREFIX,
    EXTRACTOR_VERSION,
    ExtractedRequirement,
    RequirementExtractor,
    experience_years,
)
from app.services.requirement_matching import (
    BrainSnapshot,
    ExperienceFact,
    MatchingInputs,
    RequirementMatcher,
    RequirementSpec,
    SkillFact,
    TextFact,
)
from app.services.skill_taxonomy import SkillTaxonomy, load_taxonomy
from app.services.sources import Provenance, spec_from_input

OFFER_TEXT_FIELD = "opportunity.description_text"
MANUAL_FIELD = "manual"


@lru_cache(maxsize=4)
def shared_extractor(taxonomy: SkillTaxonomy) -> RequirementExtractor:
    """Compiling the taxonomy patterns is done once per taxonomy, not once per request."""
    return RequirementExtractor(taxonomy)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_spec(requirement: TargetRequirement) -> RequirementSpec:
    return RequirementSpec(
        id=requirement.id,
        kind=requirement.kind,
        key=requirement.key,
        label=requirement.label,
        importance=requirement.importance,
        value=requirement.value,
        qualifier=requirement.qualifier,
    )


def load_brain_snapshot(session: Session, candidate_id: int) -> BrainSnapshot:
    """The part of the Candidate Brain that matching may read: names, texts, dates, states."""
    skills = brain_repo.list_for_candidate(session, Skill, candidate_id)
    projects = brain_repo.list_for_candidate(session, Project, candidate_id)
    experiences = brain_repo.list_for_candidate(session, Experience, candidate_id)
    return BrainSnapshot(
        skills=tuple(SkillFact(s.id, s.name, s.state) for s in skills),
        projects=tuple(
            TextFact(
                p.id,
                EvidenceTargetType.PROJECT,
                p.state,
                "\n".join(part for part in (p.name, p.description, p.domain) if part),
            )
            for p in projects
        ),
        experiences=tuple(
            ExperienceFact(
                e.id,
                e.state,
                e.start_date,
                e.end_date,
                "\n".join(part for part in (e.title, e.description) if part),
            )
            for e in experiences
        ),
    )


class RequirementService:
    def __init__(self, session: Session, taxonomy: SkillTaxonomy | None = None) -> None:
        self._session = session
        self.taxonomy = taxonomy or load_taxonomy()
        self.extractor = shared_extractor(self.taxonomy)
        self.matcher = RequirementMatcher(self.taxonomy, self.extractor)

    # --- helpers ----------------------------------------------------------------------

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    def target(self, target_id: int) -> Target:
        found = target_repo.get_target(self._session, self._candidate_id(), target_id)
        if found is None:
            raise NotFoundError(f"Target {target_id} not found")
        return found

    def _replace(self, old: TargetRequirement, new: TargetRequirement) -> TargetRequirement:
        """Version a requirement: deactivate the old one (kept), then insert the new one."""
        old.active = False
        old.deactivated_at = datetime.now(UTC)
        self._session.flush()  # frees the "one active requirement per key" slot
        created = target_repo.stage(self._session, new)
        old.superseded_by_id = created.id
        self._session.flush()
        return created

    # --- read -------------------------------------------------------------------------

    def list(
        self, target_id: int, *, include_inactive: bool = False
    ) -> Sequence[TargetRequirement]:
        self.target(target_id)
        return repo.list_for_target(self._session, target_id, include_inactive=include_inactive)

    def matching_inputs(self, target: Target) -> MatchingInputs:
        """Active requirements + the Brain snapshot they would be matched against."""
        requirements = repo.list_for_target(self._session, target.id)
        if not requirements:
            return MatchingInputs()
        return MatchingInputs(
            requirements=tuple(to_spec(r) for r in requirements),
            brain=load_brain_snapshot(self._session, target.candidate_id),
            taxonomy_version=self.taxonomy.version,
        )

    # --- manual entry -----------------------------------------------------------------

    def add_manual(
        self, target_id: int, data: ManualRequirementCreate
    ) -> tuple[TargetRequirement, bool]:
        target = self.target(target_id)
        if data.kind is RequirementKind.EXPERIENCE:
            years = experience_years(data.value)
            if years is None:
                raise UnprocessableError("The duration must start with a number of years")
            key = f"{EXPERIENCE_KEY_PREFIX}:{years}"
            if data.qualifier:
                key += f":{normalize_text(data.qualifier)[:60]}"
            label = data.label
        else:
            key = self.taxonomy.canonical_key(data.label)
            entry = self.taxonomy.resolve(data.label)
            label = entry.name if entry else data.label

        spec: SourceSpec = spec_from_input(data.source)
        existing = repo.find_active(self._session, target.id, data.kind, key)
        if existing is not None and self._same_manual(existing, data, label, spec):
            return existing, False  # same requirement, same source: nothing to do
        source = Provenance(self._session).source(spec)
        candidate = TargetRequirement(
            candidate_id=target.candidate_id,
            target_id=target.id,
            kind=data.kind,
            key=key,
            label=label,
            importance=data.importance,
            origin=RequirementOrigin.MANUAL,
            source_field=MANUAL_FIELD,
            source_id=source.id,
            source_hash=sha256_text(data.excerpt),
            excerpt=data.excerpt,
            value=data.value,
            qualifier=data.qualifier,
            extractor_version=None,
        )
        stored = self._replace(existing, candidate) if existing else self._stage(candidate)
        self._session.commit()
        self._session.refresh(stored)
        return stored, True

    def _stage(self, requirement: TargetRequirement) -> TargetRequirement:
        return target_repo.stage(self._session, requirement)

    @staticmethod
    def _same_manual(
        old: TargetRequirement, data: ManualRequirementCreate, label: str, spec: SourceSpec
    ) -> bool:
        return (
            old.origin is RequirementOrigin.MANUAL
            and old.label == label
            and old.importance is data.importance
            and old.excerpt == data.excerpt
            and old.value == data.value
            and old.qualifier == data.qualifier
            and old.source.reference == spec.reference
            and old.source.url == spec.url
        )

    # --- explicit extraction ----------------------------------------------------------

    def extract(self, target_id: int, *, actor: str = "api") -> ExtractionReport:
        target = self.target(target_id)
        offer = target.opportunity
        if offer is None:
            return self._report(target, "no_offer")
        text = offer.description_text
        if not text or not text.strip():
            return self._report(target, "no_description")

        digest = sha256_text(text)
        found = self.extractor.extract(text)
        counts = {"created": 0, "unchanged": 0, "superseded": 0, "retired": 0, "kept_manual": 0}
        seen: set[tuple[RequirementKind, str]] = set()
        for item in found:
            seen.add((item.kind, item.key))
            counts[self._apply(target, offer.source_id, digest, item)] += 1
        for current in repo.list_for_target(self._session, target.id):
            if current.origin is RequirementOrigin.OFFER_TEXT and (
                (current.kind, current.key) not in seen
            ):
                current.active = False
                current.deactivated_at = datetime.now(UTC)
                counts["retired"] += 1
        self._session.flush()
        AuditLog(self._session).record(
            AuditEventType.REQUIREMENTS_EXTRACTED,
            actor=actor,
            subject=f"target:{target.id}",
            details={
                "rows": len(found),
                "created": counts["created"] + counts["superseded"],
                "matched": counts["unchanged"] + counts["kept_manual"],
            },
        )
        return self._report(target, "extracted", found=len(found), **counts)

    def _apply(
        self, target: Target, source_id: int, digest: str, item: ExtractedRequirement
    ) -> str:
        new = TargetRequirement(
            candidate_id=target.candidate_id,
            target_id=target.id,
            kind=item.kind,
            key=item.key,
            label=item.label,
            importance=item.importance,
            origin=RequirementOrigin.OFFER_TEXT,
            source_field=OFFER_TEXT_FIELD,
            source_id=source_id,
            source_hash=digest,
            excerpt=item.excerpt,
            value=item.value,
            qualifier=item.qualifier,
            extractor_version=EXTRACTOR_VERSION,
        )
        existing = repo.find_active(self._session, target.id, item.kind, item.key)
        if existing is None:
            self._stage(new)
            return "created"
        if existing.origin is not RequirementOrigin.OFFER_TEXT:
            return "kept_manual"  # a human's requirement is never overwritten by an extraction
        if (
            existing.label == new.label
            and existing.importance is new.importance
            and existing.excerpt == new.excerpt
            and existing.value == new.value
            and existing.qualifier == new.qualifier
        ):
            return "unchanged"
        self._replace(existing, new)
        return "superseded"

    def _report(
        self,
        target: Target,
        outcome: ExtractionOutcome,
        *,
        found: int = 0,
        created: int = 0,
        unchanged: int = 0,
        superseded: int = 0,
        retired: int = 0,
        kept_manual: int = 0,
    ) -> ExtractionReport:
        self._session.expire_all()
        active = repo.list_for_target(self._session, target.id)
        return ExtractionReport(
            target_id=target.id,
            outcome=outcome,
            found=found,
            created=created,
            unchanged=unchanged,
            superseded=superseded,
            retired=retired,
            kept_manual=kept_manual,
            requirements=[RequirementRead.model_validate(r) for r in active],
        )
