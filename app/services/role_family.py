"""Non-gating job-family label for a target's offer (step 3a display) - see
docs/qualification_v1.md.

Purely informational: derived at read time from the SAME data and vocabulary used by the
required `keyword` criterion of "Criteria v1" (`app.services.criteria_v1`,
`app.services.role_taxonomy`), and it never changes `QualificationStatus` - a target with no
recognisable family stays whatever `QualificationService` decided, never re-excluded here.

Families are searched in the taxonomy's own declaration order (development, data/ia,
cybersecurity, infra/cloud/devops, qa/testing, it/technical, then adjacent-technical): the FIRST
matching family is reported, so a title that happens to mention terms from two families still
gets one stable, reproducible answer.
"""

from dataclasses import dataclass

from app.core.normalize import normalize_text
from app.models import Target
from app.models.enums import RoleFamily
from app.services.role_taxonomy import FAMILY_TERMS


@dataclass(frozen=True)
class RoleFamilyResult:
    family: RoleFamily
    # The exact taxonomy term found in the offer text; `None` only when `family` is `unknown`.
    matched_term: str | None


def _found_term(text: str, terms: tuple[str, ...]) -> str | None:
    padded = f" {normalize_text(text)} "
    return next((term for term in terms if f" {normalize_text(term)} " in padded), None)


def classify(target: Target) -> RoleFamilyResult:
    """`unknown` when there is no offer, or its title/description matches nothing recognised -
    this is silence, never a negative signal (a spontaneous target is always `unknown` here)."""
    offer = target.opportunity
    if offer is None:
        return RoleFamilyResult(RoleFamily.UNKNOWN, None)
    text = " ".join(part for part in (offer.title, offer.description_text) if part)
    if not normalize_text(text):
        return RoleFamilyResult(RoleFamily.UNKNOWN, None)
    for family, terms in FAMILY_TERMS.items():
        term = _found_term(text, terms)
        if term is not None:
            return RoleFamilyResult(family, term)
    return RoleFamilyResult(RoleFamily.UNKNOWN, None)
