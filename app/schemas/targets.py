"""Schemas of the target pipeline: companies, offers, contacts, targets.

Inputs validate and normalise; reads expose every piece of information together with its
source. Absence is represented by `null` / an empty list, never by an invented value.
"""

import re
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    StringConstraints,
    computed_field,
    model_validator,
)

from app.core.normalize import normalize_channel_value, normalize_siren, normalize_url
from app.models.enums import (
    ChannelKind,
    ContactResearchStatus,
    EmploymentType,
    InfoStatus,
    OpportunityStatus,
    RoleCategory,
    SourceKind,
    TargetStatus,
)
from app.models.partial_date import DatePrecision, precision_of
from app.schemas.common import LongText, ORMModel, PartialDateStr, ShortStr

_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


def _url(value: str) -> str:
    normalized = normalize_url(value)
    if normalized is None:
        raise ValueError("must be an http(s) URL")
    return normalized


def _siren(value: str) -> str:
    normalized = normalize_siren(value)
    if normalized is None:
        raise ValueError("must be a valid 9-digit SIREN")
    return normalized


def _country(value: str) -> str:
    if not _COUNTRY_RE.match(value.strip()):
        raise ValueError("must be a 2-letter country code")
    return value.strip().upper()


NormalizedUrl = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=2048), AfterValidator(_url)
]
Siren = Annotated[str, AfterValidator(_siren)]
CountryCode = Annotated[str, AfterValidator(_country)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


# --- Provenance ------------------------------------------------------------------------


class SourceInput(BaseModel):
    """Where information comes from. `import_file` is reserved for the importer."""

    kind: Literal[SourceKind.MANUAL, SourceKind.OFFICIAL_API, SourceKind.PUBLIC_PAGE] = (
        SourceKind.MANUAL
    )
    label: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ] = "Manual entry"
    url: NormalizedUrl | None = None
    reference: Reference | None = None

    @model_validator(mode="after")
    def _external_sources_are_locatable(self) -> Self:
        if self.kind is not SourceKind.MANUAL and not (self.url or self.reference):
            raise ValueError("an api or public page source needs a url or a reference")
        return self


class SourceRead(ORMModel):
    id: int
    kind: SourceKind
    label: str
    url: str | None
    reference: str | None
    retrieved_at: datetime


# --- Inputs ----------------------------------------------------------------------------


class CompanyInput(BaseModel):
    name: ShortStr
    website_url: NormalizedUrl | None = None
    careers_url: NormalizedUrl | None = None
    siren: Siren | None = None
    location: ShortStr | None = None
    country_code: CountryCode | None = None
    sector: ShortStr | None = None


class OpportunityInput(BaseModel):
    title: ShortStr
    url: NormalizedUrl | None = None
    external_id: Reference | None = None
    contract_type: EmploymentType | None = None
    location: ShortStr | None = None
    posted_on: PartialDateStr | None = None
    description_text: LongText | None = None
    status: OpportunityStatus = OpportunityStatus.UNKNOWN


class ChannelInput(BaseModel):
    kind: ChannelKind
    value: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    status: InfoStatus = InfoStatus.FOUND
    # Mandatory: an address without a source is never stored.
    source: SourceInput

    @model_validator(mode="after")
    def _value_matches_kind(self) -> Self:
        normalized = normalize_channel_value(self.kind.value, self.value)
        if normalized is None:
            raise ValueError(f"invalid {self.kind.value} value")
        self.value = normalized
        return self


class ContactInput(BaseModel):
    full_name: ShortStr | None = None
    is_generic: bool = False
    role_title: ShortStr | None = None
    role_category: RoleCategory = RoleCategory.UNKNOWN
    status: InfoStatus = InfoStatus.FOUND
    # Source of the person/role. Defaults to the target's source.
    source: SourceInput | None = None
    channels: list[ChannelInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _named_or_generic(self) -> Self:
        if not self.full_name and not self.is_generic:
            raise ValueError("a contact needs a full_name, or is_generic=true for a shared mailbox")
        if self.is_generic and not self.channels:
            raise ValueError("a shared mailbox needs at least one channel")
        return self


class TargetCreate(BaseModel):
    """Create a target. `opportunity` absent = spontaneous application."""

    company_id: int | None = None
    company: CompanyInput | None = None
    opportunity: OpportunityInput | None = None
    contract_type: EmploymentType | None = None
    relevance_note: LongText | None = None
    source: SourceInput | None = None
    contacts: list[ContactInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_company(self) -> Self:
        if (self.company_id is None) == (self.company is None):
            raise ValueError("give either company_id or company")
        return self


class TargetUpdate(BaseModel):
    status: TargetStatus | None = None
    dismissed_reason: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None
    ) = None
    relevance_note: LongText | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("nothing to update")
        return self


class AttachContact(BaseModel):
    contact_id: int | None = None
    contact: ContactInput | None = None
    is_primary: bool = False

    @model_validator(mode="after")
    def _one_contact(self) -> Self:
        if (self.contact_id is None) == (self.contact is None):
            raise ValueError("give either contact_id or contact")
        return self


class ContactUpdate(BaseModel):
    status: InfoStatus | None = None
    verified: bool | None = None
    do_not_contact: bool | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("nothing to update")
        return self


# --- Reads -----------------------------------------------------------------------------


class ChannelRead(ORMModel):
    id: int
    kind: ChannelKind
    value: str
    status: InfoStatus
    verified: bool
    source: SourceRead


class ContactRead(ORMModel):
    id: int
    company_id: int
    full_name: str | None
    is_generic: bool
    role_title: str | None
    role_category: RoleCategory
    status: InfoStatus
    verified: bool
    do_not_contact: bool
    source: SourceRead
    channels: list[ChannelRead]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def email(self) -> ChannelRead | None:
        """Best e-mail channel (found before uncertain), or null: absence is never invented."""
        emails = [channel for channel in self.channels if channel.kind is ChannelKind.EMAIL]
        emails.sort(key=lambda channel: (channel.status is not InfoStatus.FOUND, channel.id))
        return emails[0] if emails else None


class CompanyRead(ORMModel):
    id: int
    name: str
    domain: str | None
    website_url: str | None
    careers_url: str | None
    siren: str | None
    location: str | None
    country_code: str | None
    sector: str | None
    contact_research: ContactResearchStatus
    contact_research_at: datetime | None
    source: SourceRead


class OpportunityRead(ORMModel):
    id: int
    company_id: int
    title: str
    url: str | None
    external_id: str | None
    contract_type: EmploymentType | None
    location: str | None
    posted_on: str | None
    description_text: str | None
    status: OpportunityStatus
    source: SourceRead

    @computed_field  # type: ignore[prop-decorator]
    @property
    def posted_on_precision(self) -> DatePrecision | None:
        return precision_of(self.posted_on)


class CompanyDetailRead(CompanyRead):
    contacts: list[ContactRead]
    opportunities: list[OpportunityRead]


class TargetContactRead(ORMModel):
    is_primary: bool
    contact: ContactRead


class TargetRead(ORMModel):
    """Same shape for both flows: `opportunity` is null for a spontaneous application."""

    id: int
    candidate_id: int
    mode: Literal["offer", "spontaneous"]
    contract_type: EmploymentType | None
    status: TargetStatus
    dismissed_reason: str | None
    relevance_note: str | None
    company: CompanyRead
    opportunity: OpportunityRead | None
    contacts: list[TargetContactRead] = Field(validation_alias="contact_links")
    source: SourceRead
    created_at: datetime
    updated_at: datetime


class TargetCreateResult(BaseModel):
    target: TargetRead
    created: bool
    company_created: bool
    opportunity_created: bool
