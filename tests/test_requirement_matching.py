"""Matching requirements against the Candidate Brain (step 3b). Pure: no database, no network.

Synthetic Brain snapshots only. The vocabulary of results is `covered / weak / gap /
unmeasurable`: `gap` means "not established by the Brain", never "the candidate lacks it".
"""

from datetime import date

import pytest

from app.models.enums import (
    EvidenceTargetType,
    InformationState,
    MatchFactRole,
    MatchNote,
    MatchStatus,
    RequirementImportance,
    RequirementKind,
)
from app.services.requirement_extraction import RequirementExtractor
from app.services.requirement_matching import (
    BrainSnapshot,
    ExperienceFact,
    MatchingInputs,
    MatchResult,
    RequirementMatcher,
    RequirementSpec,
    SkillFact,
    TextFact,
    earliest,
    latest,
)
from app.services.skill_taxonomy import load_taxonomy

taxonomy = load_taxonomy()
matcher = RequirementMatcher(taxonomy, RequirementExtractor(taxonomy))

KNOWN, VERIFIED = InformationState.KNOWN, InformationState.VERIFIED
UNKNOWN, UNCERTAIN = InformationState.UNKNOWN, InformationState.UNCERTAIN


def skill_req(name: str, id: int = 1) -> RequirementSpec:
    return RequirementSpec(
        id=id,
        kind=RequirementKind.SKILL,
        key=taxonomy.canonical_key(name),
        label=name,
        importance=RequirementImportance.UNSPECIFIED,
    )


def exp_req(value: str = "2 years", qualifier: str | None = None, id: int = 1) -> RequirementSpec:
    return RequirementSpec(
        id=id,
        kind=RequirementKind.EXPERIENCE,
        key=f"experience_years:{value[0]}",
        label=value,
        importance=RequirementImportance.UNSPECIFIED,
        value=value,
        qualifier=qualifier,
    )


def project(id: int, text: str, state: InformationState = KNOWN) -> TextFact:
    return TextFact(id, EvidenceTargetType.PROJECT, state, text)


def experience(
    id: int, start: str | None, end: str | None, state: InformationState = KNOWN, text: str = "Role"
) -> ExperienceFact:
    return ExperienceFact(id, state, start, end, text)


def match(requirement: RequirementSpec, brain: BrainSnapshot) -> MatchResult:
    (result,) = matcher.match_all([requirement], brain)
    return result


def refs(result: MatchResult) -> set[tuple[EvidenceTargetType, int, MatchFactRole]]:
    return {(fact.type, fact.id, fact.role) for fact in result.facts}


# --- Skills ------------------------------------------------------------------------------------


@pytest.mark.parametrize("state", [KNOWN, VERIFIED])
def test_a_skill_with_known_or_verified_evidence_covers(state: InformationState) -> None:
    result = match(skill_req("Python"), BrainSnapshot(skills=(SkillFact(7, "Python", state),)))

    assert (result.status, result.note) == (MatchStatus.COVERED, MatchNote.SKILL_ESTABLISHED)
    assert refs(result) == {(EvidenceTargetType.SKILL, 7, MatchFactRole.ESTABLISHES)}
    assert result.facts[0].state is state


@pytest.mark.parametrize("state", [UNCERTAIN, UNKNOWN])
def test_a_skill_with_weak_evidence_is_weak_not_covered(state: InformationState) -> None:
    result = match(skill_req("Python"), BrainSnapshot(skills=(SkillFact(7, "Python", state),)))

    assert (result.status, result.note) == (MatchStatus.WEAK, MatchNote.SKILL_UNCONFIRMED)
    assert result.facts[0].state is state  # the open question keeps the real state


def test_no_skill_in_the_brain_is_a_gap_meaning_not_established() -> None:
    result = match(skill_req("Power BI"), BrainSnapshot(skills=(SkillFact(1, "Python", KNOWN),)))

    assert (result.status, result.note) == (MatchStatus.GAP, MatchNote.NO_SKILL_IN_BRAIN)
    assert result.facts == ()


def test_a_project_alone_never_covers_a_requirement() -> None:
    brain = BrainSnapshot(projects=(project(3, "Dashboard\nBuilt with Power BI", VERIFIED),))

    result = match(skill_req("Power BI"), brain)

    assert result.status is MatchStatus.GAP  # never covered, even with a verified project
    assert result.note is MatchNote.MENTIONED_BY_PROJECT_ONLY
    assert refs(result) == {(EvidenceTargetType.PROJECT, 3, MatchFactRole.MENTIONS)}


def test_a_project_can_support_a_skill_that_is_already_established() -> None:
    brain = BrainSnapshot(
        skills=(SkillFact(1, "Python", KNOWN),),
        projects=(project(3, "Pipeline\nWritten in Python"), project(4, "Unrelated Java app")),
        experiences=(experience(5, "2021-01-01", "2022-01-01", text="Analyst\nPython scripts"),),
    )

    result = match(skill_req("Python"), brain)

    assert result.status is MatchStatus.COVERED
    assert refs(result) == {
        (EvidenceTargetType.SKILL, 1, MatchFactRole.ESTABLISHES),
        (EvidenceTargetType.PROJECT, 3, MatchFactRole.SUPPORTS),
        (EvidenceTargetType.EXPERIENCE, 5, MatchFactRole.SUPPORTS),
    }


def test_a_supporting_project_never_turns_a_weak_skill_into_a_covered_one() -> None:
    brain = BrainSnapshot(
        skills=(SkillFact(1, "Python", UNKNOWN),), projects=(project(3, "Written in Python"),)
    )

    assert match(skill_req("Python"), brain).status is MatchStatus.WEAK


@pytest.mark.parametrize(
    ("required", "held"),
    [
        ("Power BI", "Tableau"),  # same job, different tool
        ("PostgreSQL", "SQL"),  # coverage is ONE-WAY: the language does not prove the engine
        ("SQL", "NoSQL"),
        ("SQL", "MongoDB"),
        ("MySQL", "PostgreSQL"),  # two engines are neighbours, not each other
        ("Java", "JavaScript"),
        ("Python", "pandas"),
    ],
)
def test_a_neighbouring_skill_never_covers(required: str, held: str) -> None:
    brain = BrainSnapshot(skills=(SkillFact(1, held, VERIFIED),))

    result = match(skill_req(required), brain)

    assert result.status is MatchStatus.GAP and result.facts == ()


@pytest.mark.parametrize("state", [KNOWN, VERIFIED])
@pytest.mark.parametrize("held", ["PostgreSQL", "postgres", "MySQL", "SQL Server", "SQLite"])
def test_a_sql_engine_skill_explicitly_covers_a_sql_requirement(
    held: str, state: InformationState
) -> None:
    brain = BrainSnapshot(skills=(SkillFact(4, held, state),))

    result = match(skill_req("SQL"), brain)

    assert (result.status, result.note) == (MatchStatus.COVERED, MatchNote.SKILL_IMPLIED)
    assert refs(result) == {(EvidenceTargetType.SKILL, 4, MatchFactRole.ESTABLISHES)}
    assert result.facts[0].state is state  # the real evidence state is kept


@pytest.mark.parametrize("state", [UNCERTAIN, UNKNOWN])
def test_a_sql_engine_skill_without_established_evidence_only_makes_sql_weak(
    state: InformationState,
) -> None:
    result = match(skill_req("SQL"), BrainSnapshot(skills=(SkillFact(4, "PostgreSQL", state),)))

    assert (result.status, result.note) == (MatchStatus.WEAK, MatchNote.SKILL_UNCONFIRMED)


def test_a_direct_skill_is_reported_as_direct_even_next_to_an_implying_one() -> None:
    brain = BrainSnapshot(skills=(SkillFact(1, "SQL", KNOWN), SkillFact(2, "PostgreSQL", VERIFIED)))

    result = match(skill_req("SQL"), brain)

    assert (result.status, result.note) == (MatchStatus.COVERED, MatchNote.SKILL_ESTABLISHED)
    assert {fact.id for fact in result.facts} == {1, 2}


def test_a_project_citing_an_engine_supports_or_raises_a_question_for_sql() -> None:
    project_only = BrainSnapshot(projects=(project(3, "Migrations on PostgreSQL", VERIFIED),))
    with_skill = BrainSnapshot(
        skills=(SkillFact(1, "PostgreSQL", KNOWN),),
        projects=(project(3, "Migrations on PostgreSQL", VERIFIED),),
    )

    assert match(skill_req("SQL"), project_only).status is MatchStatus.GAP  # never covered
    assert match(skill_req("SQL"), project_only).note is MatchNote.MENTIONED_BY_PROJECT_ONLY
    supported = match(skill_req("SQL"), with_skill)
    assert supported.status is MatchStatus.COVERED
    assert (EvidenceTargetType.PROJECT, 3, MatchFactRole.SUPPORTS) in refs(supported)


def test_pandas_never_covers_python_and_a_tool_never_covers_its_competitor() -> None:
    assert match(
        skill_req("Python"), BrainSnapshot(skills=(SkillFact(1, "Pandas", VERIFIED),))
    ).status is (MatchStatus.GAP)
    assert match(
        skill_req("Power BI"), BrainSnapshot(skills=(SkillFact(1, "Tableau", KNOWN),))
    ).status is (MatchStatus.GAP)


def test_aliases_of_the_same_skill_do_cover() -> None:
    brain = BrainSnapshot(skills=(SkillFact(1, "postgres", KNOWN), SkillFact(2, "PowerBI", KNOWN)))

    assert match(skill_req("PostgreSQL"), brain).status is MatchStatus.COVERED
    assert match(skill_req("Power BI", id=2), brain).status is MatchStatus.COVERED


def test_the_best_evidence_among_duplicate_names_decides() -> None:
    brain = BrainSnapshot(
        skills=(SkillFact(1, "Python", UNKNOWN), SkillFact(2, "python", VERIFIED))
    )

    result = match(skill_req("Python"), brain)

    assert result.status is MatchStatus.COVERED
    assert refs(result) == {(EvidenceTargetType.SKILL, 2, MatchFactRole.ESTABLISHES)}


def test_a_label_outside_the_taxonomy_matches_by_normalised_name_only() -> None:
    requirement = RequirementSpec(
        1, RequirementKind.SKILL, "obscure tool", "Obscure Tool", RequirementImportance.UNSPECIFIED
    )

    assert match(
        requirement, BrainSnapshot(skills=(SkillFact(1, "obscure  TOOL", KNOWN),))
    ).status is (MatchStatus.COVERED)
    assert match(requirement, BrainSnapshot(skills=(SkillFact(1, "Obscure", KNOWN),))).status is (
        MatchStatus.GAP
    )


# --- Experience --------------------------------------------------------------------------------


def test_a_computable_duration_is_covered_and_deterministic() -> None:
    brain = BrainSnapshot(
        experiences=(
            experience(1, "2022-01-15", "2023-01-15"),
            experience(2, "2023-06-01", "2024-07-01"),  # 2 years and a half in total
        )
    )

    first = match(exp_req("2 years"), brain)
    second = RequirementMatcher(taxonomy, RequirementExtractor(taxonomy)).match_all(
        [exp_req("2 years")], brain
    )[0]

    assert first == second  # no clock, no state
    assert (first.status, first.note) == (MatchStatus.COVERED, MatchNote.EXPERIENCE_ESTABLISHED)
    assert refs(first) == {
        (EvidenceTargetType.EXPERIENCE, 1, MatchFactRole.ESTABLISHES),
        (EvidenceTargetType.EXPERIENCE, 2, MatchFactRole.ESTABLISHES),
    }


def test_overlapping_experiences_are_not_counted_twice() -> None:
    brain = BrainSnapshot(
        experiences=(
            experience(1, "2022-01-01", "2023-01-01"),
            experience(2, "2022-03-01", "2023-01-01"),
        )
    )

    assert match(exp_req("2 years"), brain).status is MatchStatus.GAP  # one year, not two


def test_the_duration_needs_no_more_than_the_dates_certainly_cover() -> None:
    exactly = BrainSnapshot(experiences=(experience(1, "2022-01-01", "2024-01-01"),))
    one_day_short = BrainSnapshot(experiences=(experience(1, "2022-01-02", "2024-01-01"),))

    assert match(exp_req("2 years"), exactly).status is MatchStatus.COVERED
    assert match(exp_req("2 years"), one_day_short).status is MatchStatus.GAP


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2022", "2024"),  # years only: could be 1 or 3 years
        ("2022-06", "2024-05"),  # months only: could be just under or over two years
        ("2021-01-01", None),  # no end date: unknown, not "today"
        (None, "2024-01-01"),  # no start date
    ],
)
def test_imprecise_dates_make_the_duration_unmeasurable_never_invented(
    start: str | None, end: str | None
) -> None:
    result = match(exp_req("2 years"), BrainSnapshot(experiences=(experience(1, start, end),)))

    assert (result.status, result.note) == (
        MatchStatus.UNMEASURABLE,
        MatchNote.EXPERIENCE_DATES_INSUFFICIENT,
    )


def test_imprecise_dates_that_cannot_reach_the_duration_are_not_unmeasurable() -> None:
    """Even the most generous reading of `2023` -> `2024` stays under three years."""
    brain = BrainSnapshot(experiences=(experience(1, "2023", "2024"),))

    result = match(exp_req("3 years"), brain)

    assert (result.status, result.note) == (MatchStatus.GAP, MatchNote.EXPERIENCE_INSUFFICIENT)


def test_a_year_only_experience_can_still_cover_when_the_lower_bound_is_enough() -> None:
    # 2018 -> 2024: certainly more than 5 years (from 2018-12-31 to 2024-01-01).
    brain = BrainSnapshot(experiences=(experience(1, "2018", "2024"),))

    assert match(exp_req("5 years"), brain).status is MatchStatus.COVERED


def test_a_duration_tied_to_a_domain_is_unmeasurable() -> None:
    brain = BrainSnapshot(experiences=(experience(1, "2015-01-01", "2024-01-01"),))

    result = match(exp_req("2 years", qualifier="in data analysis"), brain)

    assert (result.status, result.note) == (
        MatchStatus.UNMEASURABLE,
        MatchNote.EXPERIENCE_SCOPE_NOT_MEASURABLE,
    )


def test_no_experience_in_the_brain_is_a_gap_and_an_unreadable_value_is_unmeasurable() -> None:
    assert match(exp_req("2 years"), BrainSnapshot()).note is MatchNote.EXPERIENCE_NONE_IN_BRAIN
    unreadable = RequirementSpec(
        1,
        RequirementKind.EXPERIENCE,
        "experience_years:x",
        "x",
        RequirementImportance.REQUIRED,
        "x",
    )
    assert match(unreadable, BrainSnapshot()).status is MatchStatus.UNMEASURABLE


def test_experiences_without_established_evidence_only_make_it_weak() -> None:
    brain = BrainSnapshot(experiences=(experience(1, "2020-01-01", "2023-01-01", UNKNOWN),))

    result = match(exp_req("2 years"), brain)

    assert (result.status, result.note) == (MatchStatus.WEAK, MatchNote.EXPERIENCE_UNCONFIRMED)
    assert result.facts[0].state is UNKNOWN


def test_partial_date_bounds_never_state_a_date() -> None:
    assert (earliest("2022"), latest("2022")) == (date(2022, 1, 1), date(2022, 12, 31))
    assert (earliest("2024-02"), latest("2024-02")) == (date(2024, 2, 1), date(2024, 2, 29))
    assert earliest("2022-05-07") == latest("2022-05-07") == date(2022, 5, 7)


# --- No invented facts -------------------------------------------------------------------------


def test_matching_only_ever_references_facts_that_exist_in_the_brain() -> None:
    brain = BrainSnapshot(
        skills=(SkillFact(1, "Python", KNOWN), SkillFact(2, "SQL", UNKNOWN)),
        projects=(project(3, "Uses Python and SQL"),),
        experiences=(experience(4, "2020-01-01", "2023-01-01", text="Uses Python"),),
    )
    requirements = [
        skill_req("Python", 1),
        skill_req("SQL", 2),
        skill_req("Power BI", 3),
        exp_req("2 years", id=4),
    ]

    results = matcher.match_all(requirements, brain)

    existing = {
        (EvidenceTargetType.SKILL, 1),
        (EvidenceTargetType.SKILL, 2),
        (EvidenceTargetType.PROJECT, 3),
        (EvidenceTargetType.EXPERIENCE, 4),
    }
    assert {(f.type, f.id) for r in results for f in r.facts} <= existing
    assert [r.requirement_id for r in results] == [1, 2, 3, 4]
    assert results[2].facts == ()  # Power BI: nothing to reference, nothing invented


def test_an_empty_brain_establishes_nothing() -> None:
    results = matcher.match_all([skill_req("Python", 1), exp_req("2 years", id=2)], BrainSnapshot())

    assert {r.status for r in results} == {MatchStatus.GAP}
    assert all(r.facts == () for r in results)


# --- Fingerprint inputs ------------------------------------------------------------------------


def test_matching_inputs_change_when_the_brain_or_the_requirements_change() -> None:
    requirement = skill_req("Python")
    brain = BrainSnapshot(skills=(SkillFact(1, "Python", UNKNOWN),))
    base = MatchingInputs((requirement,), brain, "v1").payload()

    stronger = BrainSnapshot(skills=(SkillFact(1, "Python", KNOWN),))
    assert MatchingInputs((requirement,), stronger, "v1").payload() != base
    assert MatchingInputs((requirement,), brain, "v2").payload() != base
    assert MatchingInputs((requirement,), brain, "v1").payload() == base
    assert MatchingInputs((), stronger, "v1").payload() == {}  # nothing to match: Brain is moot
