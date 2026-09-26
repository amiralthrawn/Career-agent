"""Companies and their (optional) job offers. Global reference data, not tied to a candidate."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    ContactResearchStatus,
    EmploymentType,
    OpportunityStatus,
    enum_column,
)
from app.models.partial_date import PartialDateType
from app.models.sources import Source

if TYPE_CHECKING:
    from app.models.contacts import Contact


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    # Normalised name (accent-, case- and legal-form-insensitive) used for de-duplication.
    name_key: Mapped[str] = mapped_column(String(255), index=True)
    # Normalised host of the website (no scheme, no www.). Unique when known.
    domain: Mapped[str | None] = mapped_column(String(255), unique=True)
    website_url: Mapped[str | None] = mapped_column(String(2048))
    careers_url: Mapped[str | None] = mapped_column(String(2048))
    siren: Mapped[str | None] = mapped_column(String(9), unique=True)
    location: Mapped[str | None] = mapped_column(String(255))
    country_code: Mapped[str | None] = mapped_column(String(2))
    sector: Mapped[str | None] = mapped_column(String(255))
    # Has a contact search been done? `not_found` only means the sources consulted gave nothing.
    contact_research: Mapped[ContactResearchStatus] = mapped_column(
        enum_column(ContactResearchStatus), default=ContactResearchStatus.NOT_STARTED
    )
    contact_research_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)

    source: Mapped[Source] = relationship(lazy="joined")
    # Read-only views used by the company detail endpoint.
    contacts: Mapped[list["Contact"]] = relationship(
        "Contact", viewonly=True, order_by="Contact.id"
    )
    opportunities: Mapped[list["Opportunity"]] = relationship(
        "Opportunity", viewonly=True, order_by="Opportunity.id"
    )


class Opportunity(TimestampMixin, Base):
    """A published offer. Optional: a spontaneous application has none."""

    __tablename__ = "opportunities"
    __table_args__ = (
        # Referenced by targets so that a target can never link an offer of another company.
        UniqueConstraint("id", "company_id"),
        Index(
            "uq_opportunities_company_url",
            "company_id",
            "url",
            unique=True,
            postgresql_where=text("url IS NOT NULL"),
            sqlite_where=text("url IS NOT NULL"),
        ),
        Index(
            "uq_opportunities_company_external_id",
            "company_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    # Normalised key of title + location, used when an offer has neither URL nor external id.
    title_key: Mapped[str] = mapped_column(String(255), index=True)
    url: Mapped[str | None] = mapped_column(String(2048))
    external_id: Mapped[str | None] = mapped_column(String(100))
    contract_type: Mapped[EmploymentType | None] = mapped_column(enum_column(EmploymentType))
    location: Mapped[str | None] = mapped_column(String(255))
    posted_on: Mapped[str | None] = mapped_column(PartialDateType)
    description_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[OpportunityStatus] = mapped_column(
        enum_column(OpportunityStatus), default=OpportunityStatus.UNKNOWN
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)

    source: Mapped[Source] = relationship(lazy="joined")
