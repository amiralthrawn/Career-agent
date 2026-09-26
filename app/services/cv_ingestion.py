"""CV ingestion workflow: private file -> proposals -> human decision -> Candidate Brain.

Guarantees enforced here:
- ingestion only creates PROPOSALS (status `pending`); it never creates a fact or an evidence;
- a proposal becomes a fact only through an explicit human `accept`;
- acceptance creates the fact (or reuses the identical existing one), the document Evidence and
  the EvidenceLink in ONE transaction, and never marks anything `verified`;
- a proposal can be decided only once (no double acceptance, no revival of a rejection);
- the original document is only ever read.

No logging happens here and no message contains document content.
"""

import hashlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.models import (
    Base,
    Certification,
    DocumentIngestion,
    Education,
    Evidence,
    EvidenceLink,
    Experience,
    IngestionProposal,
    Language,
    Project,
    Skill,
)
from app.models.base import CandidateOwnedMixin
from app.models.enums import Confidence, EvidenceTargetType, ProposalStatus, SourceType
from app.models.evidence import LINK_COLUMNS
from app.repositories import candidate_brain as brain_repo
from app.repositories import ingestion as repo
from app.schemas.facts import (
    CertificationCreate,
    EducationCreate,
    ExperienceCreate,
    LanguageCreate,
    ProjectCreate,
    SkillCreate,
)
from app.schemas.ingestion import CVIngestionRequest, ProposalAccept, ProposalReject
from app.services.cv_parser import PARSER_VERSION, fingerprint, natural_key, parse_cv
from app.services.document_text import extract_docx_text
from app.services.private_files import read_document_bytes, resolve_private_file

# A CV is the candidate's own, human-reviewed statement: "known", never "verified".
CV_EVIDENCE_CONFIDENCE = Confidence.MEDIUM

FACT_SCHEMAS: dict[EvidenceTargetType, type[BaseModel]] = {
    EvidenceTargetType.EDUCATION: EducationCreate,
    EvidenceTargetType.EXPERIENCE: ExperienceCreate,
    EvidenceTargetType.PROJECT: ProjectCreate,
    EvidenceTargetType.SKILL: SkillCreate,
    EvidenceTargetType.CERTIFICATION: CertificationCreate,
    EvidenceTargetType.LANGUAGE: LanguageCreate,
}
FACT_MODELS: dict[EvidenceTargetType, type[Base]] = {
    EvidenceTargetType.EDUCATION: Education,
    EvidenceTargetType.EXPERIENCE: Experience,
    EvidenceTargetType.PROJECT: Project,
    EvidenceTargetType.SKILL: Skill,
    EvidenceTargetType.CERTIFICATION: Certification,
    EvidenceTargetType.LANGUAGE: Language,
}


def _attribute_getter(obj: object) -> Callable[[str], Any]:
    def get(name: str) -> Any:
        return getattr(obj, name, None)

    return get


class CVIngestionService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # --- ingestion (creates proposals only) ------------------------------------------

    def ingest_cv(self, request: CVIngestionRequest) -> DocumentIngestion:
        candidate = self._candidate_id()
        path = resolve_private_file(self._settings, request.source_path)
        data = read_document_bytes(path)
        sha256 = hashlib.sha256(data).hexdigest()
        if repo.get_ingestion_by_hash(self._session, candidate, sha256) is not None:
            raise ConflictError("This exact document has already been ingested")

        result = parse_cv(extract_docx_text(data))
        ingestion = DocumentIngestion(
            candidate_id=candidate,
            source_uri=Path(request.source_path).as_posix(),
            sha256=sha256,
            file_size=len(data),
            parser_version=PARSER_VERSION,
            stats=result.stats,
        )
        ingestion.proposals = [
            IngestionProposal(
                candidate_id=candidate,
                kind=draft.kind,
                status=ProposalStatus.PENDING,
                data=draft.data,
                source_excerpt=draft.source_excerpt,
                uncertainties=draft.uncertainties,
                fingerprint=fingerprint(draft.kind, draft.data, draft.source_excerpt),
            )
            for draft in result.drafts
        ]
        return brain_repo.add(self._session, ingestion)

    def list_ingestions(self) -> Sequence[DocumentIngestion]:
        return brain_repo.list_for_candidate(self._session, DocumentIngestion, self._candidate_id())

    def get_ingestion(self, ingestion_id: int) -> DocumentIngestion:
        found = brain_repo.get_for_candidate(
            self._session, DocumentIngestion, self._candidate_id(), ingestion_id
        )
        if found is None:
            raise NotFoundError(f"Ingestion {ingestion_id} not found")
        return found

    # --- proposals --------------------------------------------------------------------

    def list_proposals(
        self,
        *,
        status: ProposalStatus | None = None,
        kind: EvidenceTargetType | None = None,
        ingestion_id: int | None = None,
    ) -> Sequence[IngestionProposal]:
        return repo.list_proposals(
            self._session, self._candidate_id(), status=status, kind=kind, ingestion_id=ingestion_id
        )

    def get_proposal(self, proposal_id: int) -> IngestionProposal:
        found = brain_repo.get_for_candidate(
            self._session, IngestionProposal, self._candidate_id(), proposal_id
        )
        if found is None:
            raise NotFoundError(f"Proposal {proposal_id} not found")
        return found

    def reject(self, proposal_id: int, decision: ProposalReject) -> IngestionProposal:
        """Reject a pending proposal. Creates no fact, no evidence and no link."""
        proposal = self.get_proposal(proposal_id)
        self._require_pending(proposal)
        if not repo.claim_pending(self._session, proposal.id, ProposalStatus.REJECTED):
            raise ConflictError("The proposal has already been decided")
        proposal.review_note = decision.note
        proposal.decided_at = datetime.now(UTC)
        repo.commit(self._session)
        self._session.refresh(proposal)
        return proposal

    def accept(self, proposal_id: int, decision: ProposalAccept) -> IngestionProposal:
        """Human validation: create (or reuse) the fact, and link the document evidence to it."""
        candidate_id = self._candidate_id()
        proposal = self.get_proposal(proposal_id)
        self._require_pending(proposal)
        if proposal.uncertainties and not decision.acknowledge_uncertainties:
            raise ConflictError(
                "This proposal lists uncertainties: review them and resend with "
                "acknowledge_uncertainties=true"
            )
        validated = self._validate(proposal, decision.corrections)

        # From here on everything happens in a single transaction.
        if not repo.claim_pending(self._session, proposal.id, ProposalStatus.ACCEPTED):
            raise ConflictError("The proposal has already been decided")

        existing = self._find_existing_fact(candidate_id, proposal.kind, validated)
        if existing is None:
            fact = FACT_MODELS[proposal.kind](candidate_id=candidate_id, **validated.model_dump())
            repo.stage(self._session, fact)
        else:
            fact = existing
        fact_id: int = fact.id  # type: ignore[attr-defined]

        evidence = self._document_evidence(proposal.ingestion, candidate_id)
        column = LINK_COLUMNS[proposal.kind]
        already_linked = any(
            getattr(link, column) == fact_id and link.evidence_id == evidence.id
            for link in evidence.links
        )
        if not already_linked:
            repo.stage(
                self._session,
                EvidenceLink(
                    evidence_id=evidence.id,
                    note=proposal.source_excerpt,
                    **{column: fact_id},
                ),
            )

        proposal.reviewed_data = validated.model_dump(mode="json")
        proposal.resulting_fact_id = fact_id
        proposal.linked_existing = existing is not None
        proposal.decided_at = datetime.now(UTC)
        repo.commit(self._session)
        self._session.refresh(proposal)
        return proposal

    # --- helpers ----------------------------------------------------------------------

    def _candidate_id(self) -> int:
        candidate = brain_repo.get_first_candidate(self._session)
        if candidate is None:
            raise NotFoundError("No candidate has been created yet")
        return candidate.id

    @staticmethod
    def _require_pending(proposal: IngestionProposal) -> None:
        if proposal.status is not ProposalStatus.PENDING:
            raise ConflictError(f"The proposal has already been decided ({proposal.status.value})")

    @staticmethod
    def _validate(proposal: IngestionProposal, corrections: dict[str, Any]) -> BaseModel:
        schema = FACT_SCHEMAS[proposal.kind]
        unknown = sorted(set(corrections) - set(schema.model_fields))
        if unknown:
            raise UnprocessableError(f"Unknown correction fields: {', '.join(unknown)}")
        try:
            return schema.model_validate({**proposal.data, **corrections})
        except ValidationError as error:
            fields = sorted(
                {".".join(str(part) for part in item["loc"]) for item in error.errors()}
            )
            raise UnprocessableError(
                f"Invalid or missing values for: {', '.join(fields)}. Provide them as corrections."
            ) from None

    def _find_existing_fact(
        self, candidate_id: int, kind: EvidenceTargetType, validated: BaseModel
    ) -> Base | None:
        model = FACT_MODELS[kind]
        assert issubclass(model, CandidateOwnedMixin)
        wanted = natural_key(kind, _attribute_getter(validated))
        for fact in brain_repo.list_for_candidate(self._session, model, candidate_id):
            if natural_key(kind, _attribute_getter(fact)) == wanted:
                return fact
        return None

    def _document_evidence(self, ingestion: DocumentIngestion, candidate_id: int) -> Evidence:
        """Evidence for the document; created lazily, i.e. only once a human accepts something."""
        if ingestion.evidence_id is not None:
            evidence = self._session.get(Evidence, ingestion.evidence_id)
            if evidence is not None:
                return evidence
        evidence = Evidence(
            candidate_id=candidate_id,
            source_type=SourceType.CV,
            source_name=Path(ingestion.source_uri).name,
            source_uri=ingestion.source_uri,
            source_metadata={
                "sha256": ingestion.sha256,
                "parser_version": ingestion.parser_version,
                "ingestion_id": ingestion.id,
            },
            confidence=CV_EVIDENCE_CONFIDENCE,
            verified=False,  # never automatic: human acceptance is not independent verification
        )
        repo.stage(self._session, evidence)
        ingestion.evidence_id = evidence.id
        return evidence
