"""The Target: the unit of the application pipeline.

A target is a company the candidate may apply to, with an OPTIONAL job offer:
- with an offer    -> application to an existing offer;
- without an offer -> spontaneous application.

There is no parallel model: a spontaneous target is a target whose `opportunity_id` is NULL,
and everything downstream (analysis, contact, drafting, validation, sending, tracking) only
depends on `target_id`.
"""

from sqlalchemy import (
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CandidateOwnedMixin, TimestampMixin
from app.models.companies import Company, Opportunity
from app.models.contacts import Contact
from app.models.enums import EmploymentType, TargetStatus, enum_column
from app.models.sources import Source


class Target(TimestampMixin, CandidateOwnedMixin, Base):
    __tablename__ = "targets"
    __table_args__ = (
        # An offer must belong to the target's company (enforced by the database).
        ForeignKeyConstraint(
            ["opportunity_id", "company_id"],
            ["opportunities.id", "opportunities.company_id"],
            name="fk_targets_opportunity_company",
        ),
        UniqueConstraint("id", "company_id"),
        # One spontaneous target per company, one target per offer.
        Index(
            "uq_targets_spontaneous",
            "candidate_id",
            "company_id",
            unique=True,
            postgresql_where=text("opportunity_id IS NULL"),
            sqlite_where=text("opportunity_id IS NULL"),
        ),
        Index(
            "uq_targets_offer",
            "candidate_id",
            "opportunity_id",
            unique=True,
            postgresql_where=text("opportunity_id IS NOT NULL"),
            sqlite_where=text("opportunity_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    opportunity_id: Mapped[int | None] = mapped_column(index=True)
    # The contract wanted for this application (from the offer, or the candidate's aim).
    contract_type: Mapped[EmploymentType | None] = mapped_column(enum_column(EmploymentType))
    status: Mapped[TargetStatus] = mapped_column(
        enum_column(TargetStatus), default=TargetStatus.NEW
    )
    dismissed_reason: Mapped[str | None] = mapped_column(String(500))
    # Minimal explanatory context ("why this target"). Not a score and not computed.
    relevance_note: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)

    source: Mapped[Source] = relationship(lazy="joined")
    company: Mapped[Company] = relationship(lazy="joined")
    opportunity: Mapped[Opportunity | None] = relationship(
        lazy="joined",
        viewonly=True,
        primaryjoin="Opportunity.id == Target.opportunity_id",
        foreign_keys="Target.opportunity_id",
    )
    contact_links: Mapped[list["TargetContact"]] = relationship(
        lazy="selectin",
        viewonly=True,
        order_by="TargetContact.contact_id",
        primaryjoin="Target.id == TargetContact.target_id",
        foreign_keys="TargetContact.target_id",
    )

    @property
    def mode(self) -> str:
        """`offer` or `spontaneous`, derived from `opportunity_id`; never stored."""
        return "spontaneous" if self.opportunity_id is None else "offer"


class TargetContact(Base):
    """A contact selected for a target. Both must belong to the same company."""

    __tablename__ = "target_contacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["target_id", "company_id"],
            ["targets.id", "targets.company_id"],
            name="fk_target_contacts_target_company",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["contact_id", "company_id"],
            ["contacts.id", "contacts.company_id"],
            name="fk_target_contacts_contact_company",
            ondelete="CASCADE",
        ),
        Index(
            "uq_target_contacts_primary",
            "target_id",
            unique=True,
            postgresql_where=text("is_primary"),
            sqlite_where=text("is_primary"),
        ),
    )

    target_id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int]
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    contact: Mapped[Contact] = relationship(
        lazy="joined",
        viewonly=True,
        primaryjoin="Contact.id == TargetContact.contact_id",
        foreign_keys="TargetContact.contact_id",
    )
