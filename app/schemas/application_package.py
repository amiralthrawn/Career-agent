"""Schemas of the application package (step 9). `personalization_context` keeps company facts,
contact framing and GitHub evidence in clearly SEPARATE buckets - never fused with
`ApplicationDraft.claims`/`selected_evidence` (Candidate-Brain-only, unchanged).
"""

from datetime import datetime

from pydantic import BaseModel

from app.models.application_package import ApplicationPackage
from app.models.enums import ApplicationPackageStatus, ChannelKind, InfoStatus, RoleCategory
from app.schemas.common import ORMModel


class CompanyFactRead(BaseModel):
    """One accepted `CompanyResearchFact`, unchanged, sourced (step 7)."""

    claim: str
    source_url: str | None
    source_title: str | None
    excerpt: str | None
    published: str | None


class GitHubEvidenceRead(BaseModel):
    """One public repository offered as complementary evidence - never professional experience."""

    rank: int
    repository_name: str
    url: str
    description: str | None
    primary_language: str | None
    matched_requirement_labels: list[str]
    matched_terms: list[str]
    source_url: str
    is_personal_project: bool


class ContactChannelContextRead(BaseModel):
    kind: ChannelKind
    value: str
    status: InfoStatus


class ContactContextRead(BaseModel):
    """An ACCEPTED contact only (step 8) - never a `pending` observation."""

    contact_id: int
    full_name: str | None
    is_generic: bool
    role_title: str | None
    role_category: RoleCategory
    channel: ContactChannelContextRead | None
    # A fixed, deterministic tone hint from `role_category` (see
    # `app.services.application_package.APPROACH_HINT`) - never an invented fact about the
    # person's own responsibilities, projects or opinions.
    approach_hint: str


class PersonalizationContextRead(BaseModel):
    company_evidence: list[CompanyFactRead]
    contact: ContactContextRead | None
    github_evidence: list[GitHubEvidenceRead]


class PackageWarningRead(BaseModel):
    code: str
    text: str


class ApplicationPackageRead(ORMModel):
    id: int
    target_id: int
    qualification_id: int | None
    draft_id: int | None
    cv_document_id: int | None
    # Resolved for convenience (relative path under data/private/documents/); not a DB column.
    cv_source_uri: str | None = None
    contact_id: int | None
    status: ApplicationPackageStatus
    personalization_context: PersonalizationContextRead
    warnings: list[PackageWarningRead]
    # Whether the inputs changed since this package was prepared; not a DB column, computed on
    # read exactly like `BriefQualification.stale`.
    stale: bool = False
    created_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    superseded_by_id: int | None


class ApplicationPrepareRequest(BaseModel):
    profile_id: int | None = None
    model: str | None = None


def read_application_package(
    package: ApplicationPackage, *, stale: bool, cv_source_uri: str | None
) -> ApplicationPackageRead:
    base = ApplicationPackageRead.model_validate(package)
    return base.model_copy(update={"stale": stale, "cv_source_uri": cv_source_uri})
