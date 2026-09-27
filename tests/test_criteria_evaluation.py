"""Pure evaluation of criteria: what is known, what is not, and what can exclude."""

from dataclasses import replace

import pytest

from app.models.enums import (
    CriterionDimension as D,
)
from app.models.enums import (
    CriterionLevel as L,
)
from app.models.enums import (
    CriterionOperator as Op,
)
from app.models.enums import (
    CriterionOutcome as O,
)
from app.models.enums import (
    EvaluationCode as C,
)
from app.models.enums import (
    QualificationStatus as S,
)
from app.models.enums import ReasonCode as R
from app.services import criteria_evaluation as ev
from app.services.criteria_evaluation import CriterionSpec, TargetView, evaluate

BASE = TargetView(
    target_id=1,
    contract_type="apprenticeship",
    has_offer=True,
    offer_title="Data Analyst Intern",
    offer_location="Faketown",
    offer_remote_mode=None,
    offer_description="You will work with Python and SQL on synthetic dashboards.",
    company_name_key="fixture corp",
    company_domain="fixture-corp.example.invalid",
    company_location="Faketown",
    company_country="FR",
    company_sector="Synthetic software",
)
SPONTANEOUS = replace(
    BASE, has_offer=False, offer_title=None, offer_location=None, offer_description=None
)


def spec(
    dimension: D, values: list[str], operator: Op = Op.ANY_OF, level: L = L.REQUIRED
) -> CriterionSpec:
    return CriterionSpec(1, dimension, operator, tuple(values), level)


def check(
    dimension: D, values: list[str], operator: Op, view: TargetView, outcome: O, code: C
) -> None:
    result = evaluate(spec(dimension, values, operator), view)
    assert (result.outcome, result.code) == (outcome, code)


# --- Closed vocabularies: a mismatch IS a known incompatibility ------------------------


@pytest.mark.parametrize(
    ("values", "operator", "contract", "outcome", "code"),
    [
        (["apprenticeship"], Op.ANY_OF, "apprenticeship", O.SATISFIED, C.MATCH),
        (["apprenticeship", "internship"], Op.ANY_OF, "internship", O.SATISFIED, C.MATCH),
        (["apprenticeship"], Op.ANY_OF, "full_time", O.INCOMPATIBLE, C.NOT_IN_ALLOWED_SET),
        (["apprenticeship"], Op.ANY_OF, None, O.UNKNOWN, C.DATA_MISSING),
        (["full_time"], Op.NONE_OF, "full_time", O.INCOMPATIBLE, C.EXCLUDED_VALUE_PRESENT),
        (["full_time"], Op.NONE_OF, "apprenticeship", O.SATISFIED, C.EXCLUDED_TERM_ABSENT),
        (["full_time"], Op.NONE_OF, None, O.UNKNOWN, C.DATA_MISSING),
    ],
)
def test_contract_type(
    values: list[str], operator: Op, contract: str | None, outcome: O, code: C
) -> None:
    check(D.CONTRACT_TYPE, values, operator, replace(BASE, contract_type=contract), outcome, code)


@pytest.mark.parametrize(
    ("values", "operator", "country", "outcome", "code"),
    [
        (["FR"], Op.ANY_OF, "FR", O.SATISFIED, C.MATCH),
        (["fr"], Op.ANY_OF, "fr", O.SATISFIED, C.MATCH),
        (["FR"], Op.ANY_OF, "DE", O.INCOMPATIBLE, C.NOT_IN_ALLOWED_SET),
        (["FR"], Op.ANY_OF, None, O.UNKNOWN, C.DATA_MISSING),
        (["DE"], Op.NONE_OF, "DE", O.INCOMPATIBLE, C.EXCLUDED_VALUE_PRESENT),
        (["DE"], Op.NONE_OF, "FR", O.SATISFIED, C.EXCLUDED_TERM_ABSENT),
    ],
)
def test_country(values: list[str], operator: Op, country: str | None, outcome: O, code: C) -> None:
    check(D.COUNTRY, values, operator, replace(BASE, company_country=country), outcome, code)


# --- Company identity ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "operator", "outcome", "code"),
    [
        (["Fixture Corp SAS"], Op.ANY_OF, O.SATISFIED, C.MATCH),  # legal form ignored
        (["https://www.fixture-corp.example.invalid/careers"], Op.ANY_OF, O.SATISFIED, C.MATCH),
        (["Other Corp"], Op.ANY_OF, O.NOT_MATCHED, C.NO_MATCH_FOUND),  # a wish list cannot exclude
        (["Fixture Corp"], Op.NONE_OF, O.INCOMPATIBLE, C.EXCLUDED_VALUE_PRESENT),
        (["fixture-corp.example.invalid"], Op.NONE_OF, O.INCOMPATIBLE, C.EXCLUDED_VALUE_PRESENT),
        (["Other Corp"], Op.NONE_OF, O.SATISFIED, C.EXCLUDED_TERM_ABSENT),
    ],
)
def test_company(values: list[str], operator: Op, outcome: O, code: C) -> None:
    check(D.COMPANY, values, operator, BASE, outcome, code)


# --- Free text: a match proves compatibility, a miss proves nothing --------------------


@pytest.mark.parametrize(
    ("values", "operator", "sector", "outcome", "code"),
    [
        (["software"], Op.ANY_OF, "Synthetic software", O.SATISFIED, C.MATCH),
        (["fintech"], Op.ANY_OF, "Synthetic software", O.NOT_MATCHED, C.NO_MATCH_FOUND),
        (["fintech"], Op.ANY_OF, None, O.UNKNOWN, C.DATA_MISSING),
        (["gambling"], Op.NONE_OF, "Online gambling", O.INCOMPATIBLE, C.EXCLUDED_VALUE_PRESENT),
        (["gambling"], Op.NONE_OF, "Synthetic software", O.SATISFIED, C.EXCLUDED_TERM_ABSENT),
        (["gambling"], Op.NONE_OF, None, O.UNKNOWN, C.DATA_MISSING),
    ],
)
def test_sector(values: list[str], operator: Op, sector: str | None, outcome: O, code: C) -> None:
    check(D.SECTOR, values, operator, replace(BASE, company_sector=sector), outcome, code)


@pytest.mark.parametrize(
    ("values", "operator", "location", "outcome", "code"),
    [
        (["Faketown"], Op.ANY_OF, "faketown", O.SATISFIED, C.MATCH),
        (["Paris"], Op.ANY_OF, "Paris 15e", O.SATISFIED, C.MATCH),
        (["Île-de-France"], Op.ANY_OF, "ile de france", O.SATISFIED, C.MATCH),  # accents
        # A suburb is not "not Paris": no gazetteer, so a miss can never be a violation.
        (["Paris"], Op.ANY_OF, "Massy", O.NOT_MATCHED, C.NO_MATCH_FOUND),
        (["Paris"], Op.ANY_OF, None, O.UNKNOWN, C.DATA_MISSING),
        (["Lyon"], Op.NONE_OF, "Lyon", O.INCOMPATIBLE, C.EXCLUDED_VALUE_PRESENT),
        (["Lyon"], Op.NONE_OF, "Faketown", O.SATISFIED, C.EXCLUDED_TERM_ABSENT),
    ],
)
def test_location_of_an_offer(
    values: list[str], operator: Op, location: str | None, outcome: O, code: C
) -> None:
    check(D.LOCATION, values, operator, replace(BASE, offer_location=location), outcome, code)


def test_an_offer_without_location_is_unknown_not_the_company_location() -> None:
    """The company's seat is not necessarily where the job is: no silent fallback."""
    view = replace(BASE, offer_location=None, company_location="Faketown")

    check(D.LOCATION, ["Faketown"], Op.ANY_OF, view, O.UNKNOWN, C.DATA_MISSING)


def test_location_of_a_spontaneous_target_is_the_companys() -> None:
    check(D.LOCATION, ["Faketown"], Op.ANY_OF, SPONTANEOUS, O.SATISFIED, C.MATCH)
    check(
        D.LOCATION,
        ["Faketown"],
        Op.ANY_OF,
        replace(SPONTANEOUS, company_location=None),
        O.UNKNOWN,
        C.DATA_MISSING,
    )


@pytest.mark.parametrize(
    ("values", "operator", "title", "outcome", "code"),
    [
        (["data"], Op.ANY_OF, "Data Analyst Intern", O.SATISFIED, C.MATCH),
        (["data"], Op.ANY_OF, "Web Developer", O.NOT_MATCHED, C.NO_MATCH_FOUND),
        (["sales"], Op.NONE_OF, "Sales Representative", O.INCOMPATIBLE, C.EXCLUDED_VALUE_PRESENT),
        (["sales"], Op.NONE_OF, "Data Analyst", O.SATISFIED, C.EXCLUDED_TERM_ABSENT),
    ],
)
def test_role_is_read_in_the_offer_title(
    values: list[str], operator: Op, title: str, outcome: O, code: C
) -> None:
    check(D.ROLE, values, operator, replace(BASE, offer_title=title), outcome, code)


def test_role_needs_an_offer() -> None:
    check(D.ROLE, ["data"], Op.ANY_OF, SPONTANEOUS, O.UNKNOWN, C.NO_OFFER)
    check(D.ROLE, ["sales"], Op.NONE_OF, SPONTANEOUS, O.UNKNOWN, C.NO_OFFER)


def test_matching_is_by_whole_word_and_accent_insensitive() -> None:
    view = replace(BASE, offer_title="Programmer Analyste Données")

    check(
        D.ROLE, ["r"], Op.ANY_OF, view, O.NOT_MATCHED, C.NO_MATCH_FOUND
    )  # not inside "Programmer"
    check(D.ROLE, ["donnees"], Op.ANY_OF, view, O.SATISFIED, C.MATCH)
    check(D.ROLE, ["DONNÉES"], Op.ANY_OF, view, O.SATISFIED, C.MATCH)
    check(D.ROLE, ["analyste données"], Op.ANY_OF, view, O.SATISFIED, C.MATCH)  # a phrase


# --- Keywords: absence only means something when the whole text is available -----------


@pytest.mark.parametrize(
    ("values", "operator", "description", "outcome", "code"),
    [
        (["python"], Op.ANY_OF, "Uses Python daily", O.SATISFIED, C.MATCH),
        (["rust"], Op.ANY_OF, "Uses Python daily", O.NOT_MATCHED, C.NO_MATCH_FOUND),
        (["rust"], Op.ANY_OF, None, O.UNKNOWN, C.DESCRIPTION_NOT_PROVIDED),
        (["gambling"], Op.NONE_OF, "Uses Python daily", O.SATISFIED, C.EXCLUDED_TERM_ABSENT),
        (["gambling"], Op.NONE_OF, "A gambling platform", O.INCOMPATIBLE, C.EXCLUDED_VALUE_PRESENT),
        (["gambling"], Op.NONE_OF, None, O.UNKNOWN, C.DESCRIPTION_NOT_PROVIDED),
    ],
)
def test_keyword_in_an_offer(
    values: list[str], operator: Op, description: str | None, outcome: O, code: C
) -> None:
    view = replace(BASE, offer_description=description, company_sector=None)

    check(D.KEYWORD, values, operator, view, outcome, code)


def test_keyword_found_in_the_title_even_without_description() -> None:
    view = replace(BASE, offer_description=None, offer_title="Python Developer")

    check(D.KEYWORD, ["python"], Op.ANY_OF, view, O.SATISFIED, C.MATCH)
    check(D.KEYWORD, ["python"], Op.NONE_OF, view, O.INCOMPATIBLE, C.EXCLUDED_VALUE_PRESENT)


def test_keyword_on_a_spontaneous_target_uses_the_company_sector_only() -> None:
    check(D.KEYWORD, ["software"], Op.ANY_OF, SPONTANEOUS, O.SATISFIED, C.MATCH)
    check(D.KEYWORD, ["fintech"], Op.ANY_OF, SPONTANEOUS, O.UNKNOWN, C.NO_OFFER)
    check(D.KEYWORD, ["fintech"], Op.NONE_OF, SPONTANEOUS, O.UNKNOWN, C.NO_OFFER)
    bare = replace(SPONTANEOUS, company_sector=None)
    check(D.KEYWORD, ["software"], Op.ANY_OF, bare, O.UNKNOWN, C.NO_OFFER)


def test_observed_values_are_short_and_never_the_description() -> None:
    result = evaluate(spec(D.KEYWORD, ["python"]), BASE)

    assert result.observed == "python"
    text = evaluate(spec(D.SECTOR, ["software"]), replace(BASE, company_sector="x " * 400))
    assert text.observed is not None and len(text.observed) <= 255


# --- remote_mode: a closed vocabulary read on the offer -------------------------------


@pytest.mark.parametrize(
    ("values", "operator", "mode", "outcome", "code"),
    [
        (["remote"], Op.ANY_OF, "remote", O.SATISFIED, C.MATCH),
        (["remote", "hybrid"], Op.ANY_OF, "hybrid", O.SATISFIED, C.MATCH),
        (["remote"], Op.ANY_OF, "onsite", O.INCOMPATIBLE, C.NOT_IN_ALLOWED_SET),  # stated: known
        (["remote"], Op.ANY_OF, None, O.UNKNOWN, C.DATA_MISSING),  # not stated: never assumed
        (["onsite"], Op.NONE_OF, "onsite", O.INCOMPATIBLE, C.EXCLUDED_VALUE_PRESENT),
        (["onsite"], Op.NONE_OF, "remote", O.SATISFIED, C.EXCLUDED_TERM_ABSENT),
        (["onsite"], Op.NONE_OF, None, O.UNKNOWN, C.DATA_MISSING),
    ],
)
def test_remote_mode(
    values: list[str], operator: Op, mode: str | None, outcome: O, code: C
) -> None:
    check(D.REMOTE_MODE, values, operator, replace(BASE, offer_remote_mode=mode), outcome, code)


@pytest.mark.parametrize("operator", [Op.ANY_OF, Op.NONE_OF])
def test_remote_mode_of_a_spontaneous_target_is_unknown_not_a_violation(operator: Op) -> None:
    check(D.REMOTE_MODE, ["remote"], operator, SPONTANEOUS, O.UNKNOWN, C.NO_OFFER)


@pytest.mark.parametrize(
    ("level", "status"),
    [
        (L.REQUIRED, S.NEEDS_INFORMATION),  # never "excluded" for lack of data
        (L.PREFERRED, S.CANDIDATE),
        (L.FLEXIBLE, S.CANDIDATE),
    ],
)
def test_a_missing_remote_mode_effect_depends_only_on_the_level(level: L, status: S) -> None:
    for view in (BASE, SPONTANEOUS):  # offer without the data, and no offer at all
        result = evaluate(spec(D.REMOTE_MODE, ["remote"], level=level), view)

        assert result.outcome is O.UNKNOWN
        assert ev.decide_status([(level, result.outcome)]) is status


# --- constraint: a declared hard constraint that no data can evaluate ------------------


@pytest.mark.parametrize("operator", [Op.ANY_OF, Op.NONE_OF])
@pytest.mark.parametrize("view", [BASE, SPONTANEOUS], ids=["offer", "spontaneous"])
def test_a_held_constraint_is_always_unknown_never_a_violation(
    operator: Op, view: TargetView
) -> None:
    result = evaluate(spec(D.CONSTRAINT, ["salary"], operator), view)

    assert (result.outcome, result.code, result.observed) == (O.UNKNOWN, C.NOT_EVALUABLE, None)


@pytest.mark.parametrize(
    ("level", "status"),
    [
        (L.REQUIRED, S.NEEDS_INFORMATION),  # required + unknown -> needs_information
        (L.PREFERRED, S.CANDIDATE),
        (L.FLEXIBLE, S.CANDIDATE),
    ],
)
def test_a_held_constraint_needs_information_when_required_and_never_excludes(
    level: L, status: S
) -> None:
    result = evaluate(spec(D.CONSTRAINT, ["salary"], level=level), BASE)

    assert ev.decide_status([(level, result.outcome)]) is status
    assert status is not S.EXCLUDED


def test_a_held_constraint_never_hides_a_known_incompatibility() -> None:
    """The unresolved constraint does not protect a target that is known to be incompatible."""
    status = ev.decide_status(
        [(L.REQUIRED, O.UNKNOWN), (L.REQUIRED, O.INCOMPATIBLE)],
    )

    assert status is S.EXCLUDED


# --- Level effects: only a known incompatibility on a required criterion excludes ------


@pytest.mark.parametrize(
    ("results", "status"),
    [
        ([], S.CANDIDATE),
        ([(L.REQUIRED, O.SATISFIED)], S.CANDIDATE),
        ([(L.REQUIRED, O.INCOMPATIBLE)], S.EXCLUDED),
        ([(L.REQUIRED, O.SATISFIED), (L.REQUIRED, O.INCOMPATIBLE)], S.EXCLUDED),
        ([(L.REQUIRED, O.UNKNOWN)], S.NEEDS_INFORMATION),
        ([(L.REQUIRED, O.NOT_MATCHED)], S.NEEDS_INFORMATION),
        # A known incompatibility wins over unresolved ones.
        ([(L.REQUIRED, O.UNKNOWN), (L.REQUIRED, O.INCOMPATIBLE)], S.EXCLUDED),
        ([(L.REQUIRED, O.SATISFIED), (L.REQUIRED, O.UNKNOWN)], S.NEEDS_INFORMATION),
        # Preferred and flexible never change the status, whatever their outcome.
        ([(L.PREFERRED, O.INCOMPATIBLE)], S.CANDIDATE),
        ([(L.PREFERRED, O.UNKNOWN), (L.PREFERRED, O.NOT_MATCHED)], S.CANDIDATE),
        ([(L.FLEXIBLE, O.INCOMPATIBLE), (L.FLEXIBLE, O.UNKNOWN)], S.CANDIDATE),
        ([(L.REQUIRED, O.SATISFIED), (L.PREFERRED, O.INCOMPATIBLE)], S.CANDIDATE),
        ([(L.REQUIRED, O.UNKNOWN), (L.PREFERRED, O.SATISFIED)], S.NEEDS_INFORMATION),
    ],
)
def test_status_decision(results: list[tuple[L, O]], status: S) -> None:
    assert ev.decide_status(results) is status


def test_reasons_are_codes_and_references_in_a_stable_order() -> None:
    results = [
        (L.PREFERRED, O.SATISFIED),  # 0
        (L.REQUIRED, O.SATISFIED),  # 1
        (L.REQUIRED, O.UNKNOWN),  # 2
        (L.REQUIRED, O.INCOMPATIBLE),  # 3
        (L.FLEXIBLE, O.SATISFIED),  # 4
        (L.PREFERRED, O.INCOMPATIBLE),  # 5: no reason (visible in the results)
    ]

    reasons = ev.build_reasons(results, has_offer=True)

    assert [(r.code, r.result_index) for r in reasons] == [
        (R.EXCLUDED_BY_REQUIRED, 3),
        (R.OPEN_QUESTION_REQUIRED, 2),
        (R.REQUIRED_SATISFIED, 1),
        (R.PREFERRED_SATISFIED, 0),
        (R.FLEXIBLE_MATCHED, 4),
    ]
    assert ev.build_reasons(results, has_offer=True) == reasons  # deterministic


def test_no_offer_is_an_informative_reason_not_a_negative_one() -> None:
    reasons = ev.build_reasons([(L.REQUIRED, O.SATISFIED)], has_offer=False)

    assert (reasons[-1].code, reasons[-1].result_index) == (R.NO_OFFER_PUBLISHED, None)
    assert ev.decide_status([(L.REQUIRED, O.SATISFIED)]) is S.CANDIDATE


# --- Fingerprint -----------------------------------------------------------------------


def test_fingerprint_is_deterministic_and_order_independent() -> None:
    a = spec(D.SECTOR, ["software"])
    b = CriterionSpec(2, D.ROLE, Op.ANY_OF, ("data",), L.PREFERRED)

    assert ev.fingerprint(7, [a, b], BASE) == ev.fingerprint(7, [b, a], BASE)
    assert len(ev.fingerprint(7, [a], BASE)) == 64


@pytest.mark.parametrize(
    "changed",
    [
        replace(BASE, company_sector="Something else"),
        replace(BASE, offer_description="Different text"),
        replace(BASE, offer_location="Otherville"),
        replace(BASE, contract_type="internship"),
        replace(BASE, company_country="DE"),
        replace(BASE, has_offer=False),
    ],
)
def test_fingerprint_changes_when_relevant_data_changes(changed: TargetView) -> None:
    criteria = [spec(D.SECTOR, ["software"])]

    assert ev.fingerprint(1, criteria, changed) != ev.fingerprint(1, criteria, BASE)


def test_fingerprint_changes_with_criteria_profile_and_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = ev.fingerprint(1, [spec(D.SECTOR, ["software"])], BASE)

    assert ev.fingerprint(2, [spec(D.SECTOR, ["software"])], BASE) != base
    assert ev.fingerprint(1, [spec(D.SECTOR, ["fintech"])], BASE) != base
    assert ev.fingerprint(1, [spec(D.SECTOR, ["software"], level=L.PREFERRED)], BASE) != base
    assert ev.fingerprint(1, [spec(D.SECTOR, ["software"]), spec(D.ROLE, ["x"])], BASE) != base
    monkeypatch.setattr(ev, "EVALUATOR_VERSION", "criteria-next")
    assert ev.fingerprint(1, [spec(D.SECTOR, ["software"])], BASE) != base


# --- Step 7: accepted external research supplements free-text dimensions, never replaces them --


UNKNOWN_SECTOR = replace(BASE, company_sector=None)
UNKNOWN_LOCATION = replace(SPONTANEOUS, company_location=None)  # no offer: company's own location
NO_DESCRIPTION = replace(BASE, offer_description=None)


def test_research_text_resolves_an_unknown_sector() -> None:
    check(D.SECTOR, ["fintech"], Op.ANY_OF, UNKNOWN_SECTOR, O.UNKNOWN, C.DATA_MISSING)

    researched = replace(UNKNOWN_SECTOR, company_research_text="A fintech company in Paris.")

    check(D.SECTOR, ["fintech"], Op.ANY_OF, researched, O.SATISFIED, C.MATCH)


def test_research_text_supplements_but_never_replaces_the_known_sector() -> None:
    researched = replace(BASE, company_research_text="Also does fintech consulting.")

    result = evaluate(spec(D.SECTOR, ["fintech"]), researched)

    assert result.outcome is O.SATISFIED  # found in the research text
    result_known = evaluate(spec(D.SECTOR, ["synthetic software"]), researched)
    assert result_known.outcome is O.SATISFIED  # the known field still matches on its own


def test_research_text_can_still_leave_a_sector_not_matched_never_excluded() -> None:
    researched = replace(UNKNOWN_SECTOR, company_research_text="A logistics company.")

    result = evaluate(spec(D.SECTOR, ["fintech"]), researched)

    assert result.outcome is O.NOT_MATCHED  # answered, just not a match: never excludes
    assert ev.decide_status([(L.REQUIRED, result.outcome)]) is not S.EXCLUDED


def test_research_text_resolves_company_location_but_never_the_offers_own_location() -> None:
    check(D.LOCATION, ["Faketown"], Op.ANY_OF, UNKNOWN_LOCATION, O.UNKNOWN, C.DATA_MISSING)

    researched = replace(UNKNOWN_LOCATION, company_research_text="Headquartered in Faketown.")
    check(D.LOCATION, ["Faketown"], Op.ANY_OF, researched, O.SATISFIED, C.MATCH)

    # An offer's OWN location is a different question a company-research text cannot answer.
    offer_unknown = replace(BASE, offer_location=None, company_research_text="Faketown HQ.")
    check(D.LOCATION, ["Faketown"], Op.ANY_OF, offer_unknown, O.UNKNOWN, C.DATA_MISSING)


def test_research_text_can_complete_a_keyword_search_when_the_offer_has_no_description() -> None:
    check(D.KEYWORD, ["python"], Op.ANY_OF, NO_DESCRIPTION, O.UNKNOWN, C.DESCRIPTION_NOT_PROVIDED)

    researched = replace(NO_DESCRIPTION, company_research_text="The stack is Python and SQL.")
    check(D.KEYWORD, ["python"], Op.ANY_OF, researched, O.SATISFIED, C.MATCH)

    no_match = replace(NO_DESCRIPTION, company_research_text="The stack is Java only.")
    check(D.KEYWORD, ["python"], Op.ANY_OF, no_match, O.NOT_MATCHED, C.NO_MATCH_FOUND)


def test_research_text_is_never_read_for_dimensions_it_cannot_answer() -> None:
    # Closed vocabularies keep needing one clean value; free text never substitutes for it.
    researched = replace(BASE, company_country=None, company_research_text="Based in France.")

    check(D.COUNTRY, ["FR"], Op.ANY_OF, researched, O.UNKNOWN, C.DATA_MISSING)


@pytest.mark.parametrize(
    ("dimension", "values", "unknown_view", "code"),
    [
        # Contains "apprenticeship" verbatim: if this ever leaked in, the criterion below
        # would wrongly read SATISFIED instead of UNKNOWN.
        (D.CONTRACT_TYPE, ["apprenticeship"], replace(BASE, contract_type=None), C.DATA_MISSING),
        (D.ROLE, ["analyst"], SPONTANEOUS, C.NO_OFFER),
        (D.REMOTE_MODE, ["remote"], SPONTANEOUS, C.NO_OFFER),
    ],
)
def test_research_text_never_satisfies_an_offer_or_contract_specific_criterion(
    dimension: D, values: list[str], unknown_view: TargetView, code: C
) -> None:
    """A company-level observation must never resolve a criterion about THIS offer or contract:
    `company_research_text` is deliberately not read at all by these three dimensions."""
    check(dimension, values, Op.ANY_OF, unknown_view, O.UNKNOWN, code)

    leaking = replace(
        unknown_view,
        company_research_text="An apprenticeship, analyst, fully remote role - allegedly.",
    )
    check(dimension, values, Op.ANY_OF, leaking, O.UNKNOWN, code)  # unchanged: still no leak


def test_research_text_feeds_the_fingerprint() -> None:
    plain = ev.fingerprint(1, [spec(D.SECTOR, ["fintech"])], UNKNOWN_SECTOR)
    researched = replace(UNKNOWN_SECTOR, company_research_text="A fintech company.")

    assert ev.fingerprint(1, [spec(D.SECTOR, ["fintech"])], researched) != plain
