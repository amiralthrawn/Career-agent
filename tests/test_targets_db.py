"""Integrity rules enforced by the database itself (SQLite here, PostgreSQL DDL checked offline)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Contact, ContactChannel, Source, Target, TargetContact
from app.models.enums import ChannelKind, SourceKind
from tests import targets_factory as f


@pytest.fixture
def base(db_session: Session) -> tuple[int, int]:
    """(candidate id, company id)."""
    return f.candidate(db_session).id, f.company(db_session).id


def commit_fails(session: Session) -> None:
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- The target: with or without an offer ----------------------------------------------


def test_a_target_may_have_no_offer(db_session: Session, base: tuple[int, int]) -> None:
    candidate_id, company_id = base

    spontaneous = f.target(db_session, candidate_id, company_id)

    assert spontaneous.opportunity_id is None and spontaneous.mode == "spontaneous"


def test_a_target_with_an_offer_is_in_offer_mode(
    db_session: Session, base: tuple[int, int]
) -> None:
    candidate_id, company_id = base
    offer = f.opportunity(db_session, company_id)

    with_offer = f.target(db_session, candidate_id, company_id, offer.id)

    assert with_offer.mode == "offer"


def test_a_company_can_have_a_spontaneous_target_and_an_offer_target(
    db_session: Session, base: tuple[int, int]
) -> None:
    candidate_id, company_id = base
    offer = f.opportunity(db_session, company_id)

    f.target(db_session, candidate_id, company_id)
    f.target(db_session, candidate_id, company_id, offer.id)

    assert db_session.query(Target).count() == 2


def test_only_one_spontaneous_target_per_company(
    db_session: Session, base: tuple[int, int]
) -> None:
    candidate_id, company_id = base
    f.target(db_session, candidate_id, company_id)

    db_session.add(
        Target(candidate_id=candidate_id, company_id=company_id, source_id=f.source(db_session).id)
    )

    commit_fails(db_session)


def test_only_one_target_per_offer(db_session: Session, base: tuple[int, int]) -> None:
    candidate_id, company_id = base
    offer = f.opportunity(db_session, company_id)
    f.target(db_session, candidate_id, company_id, offer.id)

    db_session.add(
        Target(
            candidate_id=candidate_id,
            company_id=company_id,
            opportunity_id=offer.id,
            source_id=f.source(db_session).id,
        )
    )

    commit_fails(db_session)


def test_an_offer_of_another_company_is_refused_by_the_database(
    db_session: Session, base: tuple[int, int]
) -> None:
    candidate_id, company_id = base
    other = f.company(db_session, "Other Corp", f.OTHER_DOMAIN)
    foreign_offer = f.opportunity(db_session, other.id)

    db_session.add(
        Target(
            candidate_id=candidate_id,
            company_id=company_id,
            opportunity_id=foreign_offer.id,
            source_id=f.source(db_session).id,
        )
    )

    commit_fails(db_session)


def test_the_same_offer_url_or_external_id_cannot_be_stored_twice(
    db_session: Session, base: tuple[int, int]
) -> None:
    _, company_id = base
    f.opportunity(db_session, company_id, url="https://x.example.invalid/1", external_id="A1")

    f.opportunity(db_session, company_id, title="Other title", url=None, external_id=None)
    f.opportunity(
        db_session, company_id, title="Third", url=None, external_id=None
    )  # NULLs coexist
    with pytest.raises(IntegrityError):
        f.opportunity(db_session, company_id, title="X", url="https://x.example.invalid/1")
    db_session.rollback()


# --- Company identity ------------------------------------------------------------------


def test_company_domain_and_siren_are_unique(db_session: Session) -> None:
    f.company(db_session, "A", f.DOMAIN)

    with pytest.raises(IntegrityError):
        f.company(db_session, "B", f.DOMAIN)
    db_session.rollback()


# --- Contacts --------------------------------------------------------------------------


def test_a_contact_needs_a_name_or_to_be_generic(
    db_session: Session, base: tuple[int, int]
) -> None:
    _, company_id = base

    db_session.add(
        Contact(
            company_id=company_id,
            full_name=None,
            is_generic=False,
            source_id=f.source(db_session).id,
        )
    )

    commit_fails(db_session)


def test_a_generic_mailbox_needs_no_name(db_session: Session, base: tuple[int, int]) -> None:
    _, company_id = base

    generic = f.contact(db_session, company_id, name=None)

    assert generic.full_name is None and generic.is_generic


def test_the_same_person_cannot_be_stored_twice_in_a_company(
    db_session: Session, base: tuple[int, int]
) -> None:
    _, company_id = base
    f.contact(db_session, company_id, "Pat Fixture")

    db_session.add(
        Contact(
            company_id=company_id,
            full_name="Pat  Fixture",
            name_key="pat fixture",
            source_id=f.source(db_session).id,
        )
    )

    commit_fails(db_session)


def test_a_channel_value_is_unique_per_contact(db_session: Session, base: tuple[int, int]) -> None:
    _, company_id = base
    person = f.contact(db_session, company_id)
    channel = dict(contact_id=person.id, kind=ChannelKind.EMAIL, value=f.HR_EMAIL)
    db_session.add(ContactChannel(**channel, source_id=f.source(db_session).id))
    db_session.flush()

    db_session.add(ContactChannel(**channel, source_id=f.source(db_session).id))

    commit_fails(db_session)


# --- Target contacts -------------------------------------------------------------------


def test_only_contacts_of_the_targets_company_can_be_linked(
    db_session: Session, base: tuple[int, int]
) -> None:
    candidate_id, company_id = base
    other = f.company(db_session, "Other Corp", f.OTHER_DOMAIN)
    the_target = f.target(db_session, candidate_id, company_id)
    stranger = f.contact(db_session, other.id, "Sam Stranger")

    db_session.add(
        TargetContact(target_id=the_target.id, contact_id=stranger.id, company_id=company_id)
    )

    commit_fails(db_session)


def test_a_target_has_at_most_one_primary_contact(
    db_session: Session, base: tuple[int, int]
) -> None:
    candidate_id, company_id = base
    the_target = f.target(db_session, candidate_id, company_id)
    first, second = (
        f.contact(db_session, company_id, "A One"),
        f.contact(db_session, company_id, "B Two"),
    )
    db_session.add(
        TargetContact(
            target_id=the_target.id, contact_id=first.id, company_id=company_id, is_primary=True
        )
    )
    db_session.flush()

    db_session.add(
        TargetContact(
            target_id=the_target.id, contact_id=second.id, company_id=company_id, is_primary=True
        )
    )

    commit_fails(db_session)


def test_deleting_a_contact_removes_its_channels_and_links(
    db_session: Session, base: tuple[int, int]
) -> None:
    candidate_id, company_id = base
    the_target = f.target(db_session, candidate_id, company_id)
    person = f.contact(db_session, company_id)
    db_session.add(
        ContactChannel(
            contact_id=person.id,
            kind=ChannelKind.EMAIL,
            value=f.HR_EMAIL,
            source_id=f.source(db_session).id,
        )
    )
    db_session.add(
        TargetContact(target_id=the_target.id, contact_id=person.id, company_id=company_id)
    )
    db_session.commit()

    db_session.execute(text("DELETE FROM contacts"))
    db_session.commit()

    assert db_session.query(ContactChannel).count() == 0
    assert db_session.query(TargetContact).count() == 0
    assert db_session.query(Target).count() == 1  # the target itself is untouched


def test_a_company_with_targets_cannot_be_deleted(
    db_session: Session, base: tuple[int, int]
) -> None:
    candidate_id, company_id = base
    f.target(db_session, candidate_id, company_id)
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM companies"))
    db_session.rollback()


# --- Provenance ------------------------------------------------------------------------


def test_every_record_needs_a_source(db_session: Session, base: tuple[int, int]) -> None:
    _, company_id = base

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO contacts (company_id, full_name, name_key, is_generic, role_category, "
                "status, verified, do_not_contact, created_at, updated_at) VALUES "
                f"({company_id}, 'X Y', 'x y', 0, 'unknown', 'found', 0, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    db_session.rollback()


@pytest.mark.parametrize(
    ("kind", "url", "reference", "valid"),
    [
        (SourceKind.MANUAL, None, None, True),
        (SourceKind.IMPORT_FILE, None, "abcd1234:row 1", True),
        (SourceKind.IMPORT_FILE, None, None, False),
        (SourceKind.PUBLIC_PAGE, "https://x.example.invalid/p", None, True),
        (SourceKind.PUBLIC_PAGE, None, "note", True),
        (SourceKind.PUBLIC_PAGE, None, None, False),
        (SourceKind.OFFICIAL_API, None, None, False),
        (SourceKind.OFFICIAL_API, None, "api-record-1", True),
    ],
)
def test_source_rules_are_enforced_in_the_database(
    db_session: Session, kind: SourceKind, url: str | None, reference: str | None, valid: bool
) -> None:
    db_session.add(Source(kind=kind, label="fixture", url=url, reference=reference))

    if valid:
        db_session.commit()
    else:
        commit_fails(db_session)
