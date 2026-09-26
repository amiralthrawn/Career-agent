"""Data access for companies, offers, contacts and targets. Queries and persistence only.

Nothing here commits: the calling service owns the transaction, which lets the CSV import
run each row in a savepoint and roll a whole preview back.
"""

from collections.abc import Sequence

from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.models import (
    Base,
    Company,
    Contact,
    ContactChannel,
    Opportunity,
    Qualification,
    Target,
    TargetContact,
)
from app.models.enums import ChannelKind, EmploymentType, QualificationStatus, TargetStatus


def stage[T: Base](session: Session, instance: T) -> T:
    """Add and flush (no commit). Integrity violations become `ConflictError`."""
    session.add(instance)
    try:
        session.flush()
    except IntegrityError as error:
        raise ConflictError("The record conflicts with an existing one") from error
    return instance


# --- Companies -------------------------------------------------------------------------


def get_company(session: Session, company_id: int) -> Company | None:
    return session.get(Company, company_id)


def company_by_domain(session: Session, domain: str) -> Company | None:
    return session.scalars(select(Company).where(Company.domain == domain)).first()


def company_by_siren(session: Session, siren: str) -> Company | None:
    return session.scalars(select(Company).where(Company.siren == siren)).first()


def companies_by_name_key(session: Session, name_key: str) -> Sequence[Company]:
    return session.scalars(
        select(Company).where(Company.name_key == name_key).order_by(Company.id)
    ).all()


def list_companies(
    session: Session, *, query_key: str | None, limit: int, offset: int
) -> Sequence[Company]:
    statement = select(Company).order_by(Company.id).limit(limit).offset(offset)
    if query_key:
        statement = statement.where(
            Company.name_key.contains(query_key) | Company.domain.contains(query_key)
        )
    return session.scalars(statement).all()


# --- Opportunities ---------------------------------------------------------------------


def opportunity_by_url(session: Session, company_id: int, url: str) -> Opportunity | None:
    return session.scalars(
        select(Opportunity).where(Opportunity.company_id == company_id, Opportunity.url == url)
    ).first()


def opportunity_by_external_id(
    session: Session, company_id: int, external_id: str
) -> Opportunity | None:
    return session.scalars(
        select(Opportunity).where(
            Opportunity.company_id == company_id, Opportunity.external_id == external_id
        )
    ).first()


def opportunity_by_title_key(
    session: Session, company_id: int, title_key: str
) -> Opportunity | None:
    """Only offers with neither URL nor external id can be matched on their title."""
    return session.scalars(
        select(Opportunity).where(
            Opportunity.company_id == company_id,
            Opportunity.title_key == title_key,
            Opportunity.url.is_(None),
            Opportunity.external_id.is_(None),
        )
    ).first()


def get_opportunity(session: Session, opportunity_id: int) -> Opportunity | None:
    return session.get(Opportunity, opportunity_id)


# --- Contacts --------------------------------------------------------------------------


def get_contact(session: Session, contact_id: int) -> Contact | None:
    return session.get(Contact, contact_id)


def contact_by_name_key(session: Session, company_id: int, name_key: str) -> Contact | None:
    return session.scalars(
        select(Contact).where(Contact.company_id == company_id, Contact.name_key == name_key)
    ).first()


def contact_with_email(session: Session, company_id: int, email: str) -> Contact | None:
    return session.scalars(
        select(Contact)
        .join(ContactChannel, ContactChannel.contact_id == Contact.id)
        .where(
            Contact.company_id == company_id,
            ContactChannel.kind == ChannelKind.EMAIL,
            ContactChannel.value == email,
        )
    ).first()


def list_contacts(session: Session, company_id: int) -> Sequence[Contact]:
    return session.scalars(
        select(Contact).where(Contact.company_id == company_id).order_by(Contact.id)
    ).all()


# --- Targets ---------------------------------------------------------------------------


def get_target(session: Session, candidate_id: int, target_id: int) -> Target | None:
    return session.scalars(
        select(Target).where(Target.id == target_id, Target.candidate_id == candidate_id)
    ).first()


def find_target(
    session: Session, candidate_id: int, company_id: int, opportunity_id: int | None
) -> Target | None:
    statement = select(Target).where(
        Target.candidate_id == candidate_id, Target.company_id == company_id
    )
    if opportunity_id is None:
        statement = statement.where(Target.opportunity_id.is_(None))
    else:
        statement = statement.where(Target.opportunity_id == opportunity_id)
    return session.scalars(statement).first()


def list_targets(
    session: Session,
    candidate_id: int,
    *,
    mode: str | None,
    contract_type: EmploymentType | None,
    status: TargetStatus | None,
    company_id: int | None,
    has_email: bool | None,
    limit: int,
    offset: int,
    qualification: str | None = None,
    qualification_profile_id: int | None = None,
) -> Sequence[Target]:
    statement = select(Target).where(Target.candidate_id == candidate_id)
    if mode == "offer":
        statement = statement.where(Target.opportunity_id.is_not(None))
    elif mode == "spontaneous":
        statement = statement.where(Target.opportunity_id.is_(None))
    if contract_type is not None:
        statement = statement.where(Target.contract_type == contract_type)
    if status is not None:
        statement = statement.where(Target.status == status)
    if company_id is not None:
        statement = statement.where(Target.company_id == company_id)
    if has_email is not None:
        with_email = exists().where(
            TargetContact.target_id == Target.id,
            ContactChannel.contact_id == TargetContact.contact_id,
            ContactChannel.kind == ChannelKind.EMAIL,
        )
        statement = statement.where(with_email if has_email else ~with_email)
    if qualification is not None and qualification_profile_id is not None:
        # Compare with the LATEST qualification of each target for this profile.
        latest = (
            select(func.max(Qualification.id))
            .where(Qualification.profile_id == qualification_profile_id)
            .group_by(Qualification.target_id)
        )
        if qualification == "none":
            qualified = exists().where(
                Qualification.target_id == Target.id,
                Qualification.profile_id == qualification_profile_id,
            )
            statement = statement.where(~qualified)
        else:
            wanted = select(Qualification.target_id).where(
                Qualification.id.in_(latest),
                Qualification.status == QualificationStatus(qualification),
            )
            statement = statement.where(Target.id.in_(wanted))
    return session.scalars(statement.order_by(Target.id).limit(limit).offset(offset)).all()


def get_link(session: Session, target_id: int, contact_id: int) -> TargetContact | None:
    return session.get(TargetContact, (target_id, contact_id))


def target_has_primary(session: Session, target_id: int) -> bool:
    return (
        session.scalars(
            select(TargetContact).where(
                TargetContact.target_id == target_id, TargetContact.is_primary
            )
        ).first()
        is not None
    )
