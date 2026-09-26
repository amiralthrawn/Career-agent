"""Synthetic builders for the target pipeline tests. Every value is fictional (.invalid)."""

from typing import Any

from sqlalchemy.orm import Session

from app.models import Candidate, Company, Contact, Opportunity, Source, Target
from app.models.enums import EmploymentType, SourceKind

DOMAIN = "fixture-corp.example.invalid"
OTHER_DOMAIN = "other-corp.example.invalid"
PAGE = f"https://{DOMAIN}/team"
HR_EMAIL = f"hr.fixture@{DOMAIN}"
PERSON_EMAIL = f"person.fixture@{DOMAIN}"


def source(session: Session, kind: SourceKind = SourceKind.MANUAL, **fields: Any) -> Source:
    row = Source(
        kind=kind,
        label=fields.pop("label", "fixture source"),
        reference=fields.pop("reference", "note"),
        **fields,
    )
    session.add(row)
    session.flush()
    return row


def candidate(session: Session) -> Candidate:
    row = Candidate(first_name="Test", last_name="Candidate-Fixture")
    session.add(row)
    session.flush()
    return row


def company(session: Session, name: str = "Fixture Corp", domain: str | None = DOMAIN) -> Company:
    row = Company(
        name=name,
        name_key=name.casefold(),
        domain=domain,
        source_id=source(session).id,
    )
    session.add(row)
    session.flush()
    return row


def opportunity(
    session: Session, company_id: int, title: str = "Data Intern", **fields: Any
) -> Opportunity:
    row = Opportunity(
        company_id=company_id,
        title=title,
        title_key=title.casefold(),
        contract_type=fields.pop("contract_type", EmploymentType.INTERNSHIP),
        source_id=source(session).id,
        **fields,
    )
    session.add(row)
    session.flush()
    return row


def target(
    session: Session, candidate_id: int, company_id: int, opportunity_id: int | None = None
) -> Target:
    row = Target(
        candidate_id=candidate_id,
        company_id=company_id,
        opportunity_id=opportunity_id,
        source_id=source(session).id,
    )
    session.add(row)
    session.flush()
    return row


def contact(session: Session, company_id: int, name: str | None = "Pat Fixture") -> Contact:
    row = Contact(
        company_id=company_id,
        full_name=name,
        name_key=name.casefold() if name else None,
        is_generic=name is None,
        source_id=source(session).id,
    )
    session.add(row)
    session.flush()
    return row


def company_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "Fixture Corp",
        "website_url": f"https://{DOMAIN}",
        "location": "Faketown",
        "sector": "Synthetic software",
        **overrides,
    }


def offer_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "title": "Data Intern",
        "url": f"https://{DOMAIN}/jobs/1",
        "contract_type": "internship",
        "location": "Faketown",
        "posted_on": "2026-05",
        **overrides,
    }


def email_channel(value: str = HR_EMAIL, **overrides: Any) -> dict[str, Any]:
    return {
        "kind": "email",
        "value": value,
        "source": {"kind": "public_page", "label": "Team page", "url": PAGE},
        **overrides,
    }
