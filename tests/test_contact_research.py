"""ContactResearchService (step 8): search, store, accept, reject. All synthetic, no network, no
real Perplexity call - see Part 9 of the step 8 instructions for the exact scenarios covered here.
"""

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.integrations.research.ports import (
    Observation,
    ResearchQuery,
    ResearchResult,
    ResearchStatus,
    ResearchSubject,
)
from app.models import Contact, ContactChannel, ContactResearchObservation, TargetContact
from app.models.enums import ChannelKind, InfoStatus, ProposalStatus, RoleCategory
from app.schemas.contact_research import ContactObservationAccept, ContactObservationReject
from app.services.contact_research import ContactResearchService
from tests import targets_factory as f

FIXTURE_QUERY = ResearchQuery(
    objective="fixture objective", subject=ResearchSubject(company_name="Fixture Corp")
)


class StaticProvider:
    """A fake `ResearchProvider`: returns a fixed result and remembers what it was asked."""

    def __init__(self, result: ResearchResult) -> None:
        self.result = result
        self.asked: list[ResearchQuery] = []

    def research(self, query: ResearchQuery) -> ResearchResult:
        self.asked.append(query)
        return self.result


def result(
    *observations: Observation, status: ResearchStatus = ResearchStatus.OK
) -> ResearchResult:
    return ResearchResult(
        provider="perplexity",
        model="fake",
        query=FIXTURE_QUERY,
        status=status,
        observations=observations,
    )


def obs(
    claim: str = "Works in recruiting.", url: str = "https://x.invalid/a", **over: Any
) -> Observation:
    return Observation(claim=claim, source_url=url, source_title="Team page", **over)


@pytest.fixture
def scenario(db_session: Session) -> dict[str, int]:
    candidate = f.candidate(db_session)
    company = f.company(db_session)
    target = f.target(db_session, candidate.id, company.id)
    return {"candidate_id": candidate.id, "company_id": company.id, "target_id": target.id}


# --- search: no result, several found, no candidate data ------------------------------------


def test_a_search_with_no_sourced_observation_creates_nothing(
    db_session: Session, scenario: dict[str, int]
) -> None:
    provider = StaticProvider(result(status=ResearchStatus.NO_RESULTS))

    created = ContactResearchService(db_session, provider).search(
        scenario["target_id"], [RoleCategory.RECRUITER]
    )

    assert created == []


def test_a_search_can_find_several_contacts_at_once(
    db_session: Session, scenario: dict[str, int]
) -> None:
    provider = StaticProvider(
        result(obs("A recruiter.", "https://x.invalid/a"), obs("A manager.", "https://x.invalid/b"))
    )

    created = ContactResearchService(db_session, provider).search(
        scenario["target_id"], [RoleCategory.RECRUITER]
    )

    assert len(created) == 2
    assert all(o.status is ProposalStatus.PENDING for o in created)


def test_the_query_never_carries_candidate_data(
    db_session: Session, scenario: dict[str, int]
) -> None:
    """The fixed objective text tells Perplexity never to judge a candidature (it may say the
    word "candidate" as an instruction) - what matters is that no actual candidate data (a
    skill, a criterion value) ever reaches `subject`/`focus_areas`, which have no field for it."""
    provider = StaticProvider(result(obs()))

    ContactResearchService(db_session, provider).search(scenario["target_id"], [RoleCategory.HR])

    (sent,) = provider.asked
    dump = f"{sent.subject} {sent.focus_areas}".lower()
    assert "skill" not in dump and "criterion" not in dump


def test_no_provider_configured_is_refused(db_session: Session, scenario: dict[str, int]) -> None:
    with pytest.raises(UnprocessableError):
        ContactResearchService(db_session, None).search(scenario["target_id"], [RoleCategory.HR])


def test_an_unknown_target_is_refused(db_session: Session) -> None:
    f.candidate(db_session)  # a candidate must exist for the lookup itself to be meaningful
    provider = StaticProvider(result(obs()))

    with pytest.raises(NotFoundError):
        ContactResearchService(db_session, provider).search(999, [RoleCategory.HR])


def test_unknown_is_not_a_searchable_category(
    db_session: Session, scenario: dict[str, int]
) -> None:
    provider = StaticProvider(result(obs()))

    with pytest.raises(UnprocessableError):
        ContactResearchService(db_session, provider).search(
            scenario["target_id"], [RoleCategory.UNKNOWN]
        )


# --- provenance and idempotence --------------------------------------------------------------


def test_an_observation_without_a_source_url_is_never_stored(
    db_session: Session, scenario: dict[str, int]
) -> None:
    provider = StaticProvider(result(Observation(claim="Unsourced.", source_url=None)))

    created = ContactResearchService(db_session, provider).search(
        scenario["target_id"], [RoleCategory.HR]
    )

    assert created == []
    assert db_session.scalar(select(func.count()).select_from(ContactResearchObservation)) == 0


def test_stored_observations_keep_full_provenance(
    db_session: Session, scenario: dict[str, int]
) -> None:
    provider = StaticProvider(result(obs("A recruiter named Alex.", "https://x.invalid/team")))

    (created,) = ContactResearchService(db_session, provider).search(
        scenario["target_id"], [RoleCategory.RECRUITER]
    )

    assert created.claim == "A recruiter named Alex."
    assert created.source_url == "https://x.invalid/team"
    assert created.requested_role_category is RoleCategory.RECRUITER
    assert created.status is ProposalStatus.PENDING


def test_running_the_same_search_twice_never_duplicates_a_finding(
    db_session: Session, scenario: dict[str, int]
) -> None:
    provider = StaticProvider(result(obs()))
    service = ContactResearchService(db_session, provider)

    first = service.search(scenario["target_id"], [RoleCategory.HR])
    second = service.search(scenario["target_id"], [RoleCategory.HR])

    assert len(first) == 1
    assert second == []  # the same (source, claim) was already stored for this target
    assert db_session.scalar(select(func.count()).select_from(ContactResearchObservation)) == 1


# --- accept / reject (human-in-the-loop) ------------------------------------------------------


def _search_one(
    db_session: Session, scenario: dict[str, int], **over: Any
) -> ContactResearchObservation:
    provider = StaticProvider(result(obs(**over)))
    (created,) = ContactResearchService(db_session, provider).search(
        scenario["target_id"], [RoleCategory.RECRUITER]
    )
    return created


def test_accepting_creates_a_contact_with_the_observations_provenance(
    db_session: Session, scenario: dict[str, int]
) -> None:
    observation = _search_one(db_session, scenario, url="https://x.invalid/team")
    service = ContactResearchService(db_session, None)

    accepted = service.accept(
        observation.id, ContactObservationAccept(full_name="Alex Fixture", role_title="Recruiter")
    )

    assert accepted.status is ProposalStatus.ACCEPTED
    assert accepted.resulting_contact_id is not None
    contact = db_session.get(Contact, accepted.resulting_contact_id)
    assert contact is not None and contact.full_name == "Alex Fixture"
    assert contact.verified is False  # acceptance is not independent verification
    assert contact.source.url == "https://x.invalid/team"
    link = db_session.get(TargetContact, (scenario["target_id"], contact.id))
    assert link is not None


def test_accepting_without_a_channel_leaves_the_contact_without_an_email(
    db_session: Session, scenario: dict[str, int]
) -> None:
    observation = _search_one(db_session, scenario)
    service = ContactResearchService(db_session, None)

    accepted = service.accept(observation.id, ContactObservationAccept(full_name="No Email Person"))

    contact = db_session.get(Contact, accepted.resulting_contact_id)
    assert contact is not None and contact.channels == []


def test_accepting_with_a_published_email_stores_it_sourced(
    db_session: Session, scenario: dict[str, int]
) -> None:
    observation = _search_one(db_session, scenario, url="https://x.invalid/team")
    service = ContactResearchService(db_session, None)

    accepted = service.accept(
        observation.id,
        ContactObservationAccept(
            full_name="Has Email",
            channel_kind=ChannelKind.EMAIL,
            channel_value="has.email@fixture-corp.example.invalid",
        ),
    )

    contact = db_session.get(Contact, accepted.resulting_contact_id)
    assert contact is not None
    (channel,) = contact.channels
    assert channel.kind is ChannelKind.EMAIL
    assert channel.value == "has.email@fixture-corp.example.invalid"
    assert channel.source.url == "https://x.invalid/team"
    assert channel.status is InfoStatus.FOUND


def test_acceptance_never_invents_an_email_from_the_claim_text(
    db_session: Session, scenario: dict[str, int]
) -> None:
    """Even when the raw claim/excerpt happens to contain something e-mail-shaped, no channel is
    ever created unless the human explicitly supplies channel_kind/channel_value."""
    observation = _search_one(
        db_session,
        scenario,
        claim="Contact them at ceo@fixture-corp.example.invalid for more information.",
    )
    service = ContactResearchService(db_session, None)

    accepted = service.accept(observation.id, ContactObservationAccept(full_name="Someone"))

    contact = db_session.get(Contact, accepted.resulting_contact_id)
    assert contact is not None and contact.channels == []
    assert (
        db_session.scalar(select(func.count()).select_from(ContactChannel)) == 0
    )  # nothing was ever parsed out of the claim


def test_dedup_merges_the_same_person_reported_twice(
    db_session: Session, scenario: dict[str, int]
) -> None:
    provider = StaticProvider(
        result(
            obs("First mention.", "https://x.invalid/a"),
            obs("Second mention.", "https://x.invalid/b"),
        )
    )
    service = ContactResearchService(db_session, provider)
    first, second = service.search(scenario["target_id"], [RoleCategory.RECRUITER])

    a1 = service.accept(first.id, ContactObservationAccept(full_name="Same Person"))
    a2 = service.accept(second.id, ContactObservationAccept(full_name="Same Person"))

    assert a1.resulting_contact_id == a2.resulting_contact_id
    assert db_session.scalar(select(func.count()).select_from(Contact)) == 1


def test_homonyms_at_different_companies_are_never_merged(db_session: Session) -> None:
    candidate = f.candidate(db_session)
    company_a = f.company(db_session, name="Company A", domain="a.invalid")
    company_b = f.company(db_session, name="Company B", domain="b.invalid")
    target_a = f.target(db_session, candidate.id, company_a.id)
    target_b = f.target(db_session, candidate.id, company_b.id)
    provider = StaticProvider(result(obs()))
    service = ContactResearchService(db_session, provider)
    (oa,) = service.search(target_a.id, [RoleCategory.RECRUITER])
    (ob,) = service.search(target_b.id, [RoleCategory.RECRUITER])

    aa = service.accept(oa.id, ContactObservationAccept(full_name="Chris Homonym"))
    ab = service.accept(ob.id, ContactObservationAccept(full_name="Chris Homonym"))

    assert aa.resulting_contact_id != ab.resulting_contact_id
    assert db_session.scalar(select(func.count()).select_from(Contact)) == 2


def test_do_not_contact_is_respected_not_silently_overridden(
    db_session: Session, scenario: dict[str, int]
) -> None:
    existing = f.contact(db_session, scenario["company_id"], name="Blocked Person")
    existing.do_not_contact = True
    db_session.commit()
    observation = _search_one(db_session, scenario, claim="Blocked Person now leads hiring.")
    service = ContactResearchService(db_session, None)

    accepted = service.accept(observation.id, ContactObservationAccept(full_name="Blocked Person"))

    contact = db_session.get(Contact, accepted.resulting_contact_id)
    assert contact is not None
    assert contact.id == existing.id
    assert contact.do_not_contact is True  # never silently cleared


def test_rejecting_creates_no_contact(db_session: Session, scenario: dict[str, int]) -> None:
    observation = _search_one(db_session, scenario)
    service = ContactResearchService(db_session, None)

    rejected = service.reject(observation.id, ContactObservationReject(note="Not relevant"))

    assert rejected.status is ProposalStatus.REJECTED
    assert rejected.resulting_contact_id is None
    assert db_session.scalar(select(func.count()).select_from(Contact)) == 0


def test_a_decided_observation_cannot_be_decided_again(
    db_session: Session, scenario: dict[str, int]
) -> None:
    observation = _search_one(db_session, scenario)
    service = ContactResearchService(db_session, None)
    service.reject(observation.id, ContactObservationReject())

    with pytest.raises(ConflictError):
        service.accept(observation.id, ContactObservationAccept(full_name="Too Late"))
    with pytest.raises(ConflictError):
        service.reject(observation.id, ContactObservationReject())


def test_pending_observations_are_distinguishable_from_accepted_and_rejected(
    db_session: Session, scenario: dict[str, int]
) -> None:
    provider = StaticProvider(
        result(
            obs("A.", "https://x.invalid/a"),
            obs("B.", "https://x.invalid/b"),
            obs("C.", "https://x.invalid/c"),
        )
    )
    service = ContactResearchService(db_session, provider)
    pending_a, pending_b, pending_c = service.search(
        scenario["target_id"], [RoleCategory.RECRUITER]
    )
    service.accept(pending_a.id, ContactObservationAccept(full_name="A"))
    service.reject(pending_b.id, ContactObservationReject())

    statuses = {o.id: o.status for o in service.list_observations(target_id=scenario["target_id"])}
    assert statuses[pending_a.id] is ProposalStatus.ACCEPTED
    assert statuses[pending_b.id] is ProposalStatus.REJECTED
    assert statuses[pending_c.id] is ProposalStatus.PENDING


# --- no real send ------------------------------------------------------------------------------


def test_no_send_capability_exists_anywhere_in_this_module() -> None:
    import inspect

    from app.services import contact_research

    source = inspect.getsource(contact_research)
    for banned in ("smtplib", "send_mail", "sendgrid", ".send("):
        assert banned not in source
