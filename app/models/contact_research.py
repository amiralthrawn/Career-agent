"""Contact research (step 8): a professional contact REPORTED by a `ResearchProvider`, not yet
a `Contact`.

Nothing here is a fact. An observation only becomes a `Contact`/`ContactChannel` (via
`app.services.contacts.ContactService.record_contact`) when a human accepts it and supplies the
actual structured fields THEMSELVES - Perplexity's free-text `claim`/`excerpt` is never
auto-parsed into a name, a title or an address. This mirrors `app.models.ingestion
.IngestionProposal` field-for-field on purpose (same PENDING/ACCEPTED/REJECTED lifecycle via the
same `ProposalStatus`, same mutable-in-place decision fields, no immutability trigger) rather than
inventing a second, parallel human-in-the-loop architecture. It deliberately does NOT mirror
`app.models.company_research.CompanyResearchFact` (append-only, auto-accepted): a company fact
becomes usable the moment it is sourced, but a CONTACT is personal data, so it stays a proposal
until a human explicitly confirms it.

Scoped to `(target_id, company_id, requested_role_category)`: research is always for one
candidature, never mixed into another target's contact list, even when several targets share a
company (see `app.services.contact_research`, which fans out one provider call to every target
that needs it without re-searching).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import ProposalStatus, RoleCategory, enum_column


class ContactResearchObservation(TimestampMixin, Base):
    __tablename__ = "contact_research_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["target_id", "company_id"],
            ["targets.id", "targets.company_id"],
            name="fk_contact_research_target_company",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["resulting_contact_id", "company_id"],
            ["contacts.id", "contacts.company_id"],
            name="fk_contact_research_contact_company",
        ),
        # Same (source, claim) reported again for the same target is a re-run, not a new finding.
        UniqueConstraint("target_id", "fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(index=True)
    company_id: Mapped[int] = mapped_column(index=True)
    # What kind of contact this search was looking for (recruiter, HR, tech lead...).
    requested_role_category: Mapped[RoleCategory] = mapped_column(enum_column(RoleCategory))
    status: Mapped[ProposalStatus] = mapped_column(
        enum_column(ProposalStatus), default=ProposalStatus.PENDING, index=True
    )

    # --- as reported by the provider, verbatim; never rewritten after creation --------------
    claim: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(2048))
    source_title: Mapped[str | None] = mapped_column(String(500))
    excerpt: Mapped[str | None] = mapped_column(Text)
    published: Mapped[str | None] = mapped_column(String(50))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Stable hash of (source_url, claim); makes a repeated batch run idempotent (see fingerprint()).
    fingerprint: Mapped[str] = mapped_column(String(64))

    # --- human decision, set on accept/reject; NULL means still pending ---------------------
    # The structured fields a human confirmed from the source - never auto-extracted from `claim`.
    reviewed_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    review_note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set on acceptance, via the existing `ContactService.record_contact` (never a second path).
    # No direct FK column: enforced together with `company_id` by the composite constraint above.
    resulting_contact_id: Mapped[int | None] = mapped_column(index=True)
