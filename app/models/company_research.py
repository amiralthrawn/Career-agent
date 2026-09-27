"""CompanyResearchFact: an ACCEPTED, sourced external observation about a company (step 7).

Career-agent never rewrites `Company` fields from research (an existing company is never
modified by a later source - see `docs/targets.md`), and never turns a claim into a clean,
structured "fact" (`sector = Finance`) by itself. Instead, an accepted `Observation` is stored
here, verbatim, with its provenance, as MORE SEARCHABLE TEXT. The existing, unchanged
deterministic matcher (`app.services.criteria_evaluation`) is the only thing that ever decides
whether a criterion is satisfied - it now simply has more free text to search for the term it
already looks for, exactly as it already searches `Company.sector`. Nothing here invents a
verdict; it only reduces missing data, and the row itself is never mistaken for the claim.

Immutable (append-only, like every other provenance record in this project): an accepted
observation is a historical fact about what research returned and when: it is never edited, only
superseded by a later, separate acceptance.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, event, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.mapper import Mapper

from app.models.base import Base
from app.models.immutability import make_immutable


class CompanyResearchFact(Base):
    __tablename__ = "company_research_facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    # The observation, exactly as the research provider returned it. Never rewritten.
    claim: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source_title: Mapped[str | None] = mapped_column(String(500))
    excerpt: Mapped[str | None] = mapped_column(Text)
    # As the source gave it; never parsed.
    published: Mapped[str | None] = mapped_column(String(100))
    # When the provider returned it, vs. when Career-agent stored it (`created_at`): kept apart.
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


make_immutable(CompanyResearchFact.__table__)  # type: ignore[arg-type]


@event.listens_for(CompanyResearchFact, "before_update")
def _refuse_update(mapper: Mapper[Any], connection: Any, target: CompanyResearchFact) -> None:
    raise RuntimeError("company research facts are immutable: accept a new observation instead")
