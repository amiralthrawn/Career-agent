""" "Criteria v1" (step 3a): the deterministic qualification engine, reused unchanged, applied to
a concrete search profile. Groups mirror the spec's own numbering: Contrat, Poste, Localisation,
Etudes, Remuneration, Competences, Secteur, Disponibilite, Versionnement, Raisons (42 scenarios;
CLI and API/Securite live in the two sibling test modules, for 54 total).
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.opportunity_qualification_factory import add_target, run


@pytest.fixture(autouse=True)
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


def decision(client: TestClient, target_id: int) -> dict[str, Any]:
    response = run(client, target_id)
    assert response.status_code in (200, 201), response.text
    result: dict[str, Any] = response.json()
    return result


def profile_criteria(client: TestClient) -> list[dict[str, Any]]:
    """Force "Criteria v1" to exist (it is created lazily, on first qualification), then read
    its criteria back (dimensions only matter)."""
    bootstrap = add_target(client, domain="bootstrap.example.invalid", title="Backend Developer")
    decision(client, bootstrap["id"])
    profiles = client.get("/api/search-profiles").json()
    v1 = next(p for p in profiles if p["name"] == "Criteria v1")
    return [c for c in v1["criteria"] if c["active"]]


# === 1. Contrat (5) ===========================================================================


def test_contrat_apprenticeship_is_qualified(client: TestClient) -> None:
    target = add_target(client, contract_type="apprenticeship", title="Backend Developer")
    assert decision(client, target["id"])["decision"] == "qualified"


def test_contrat_professionalization_is_also_alternance_and_qualified(client: TestClient) -> None:
    target = add_target(client, contract_type="professionalization", title="Backend Developer")
    assert decision(client, target["id"])["decision"] == "qualified"


def test_contrat_cdi_is_not_qualified(client: TestClient) -> None:
    target = add_target(client, contract_type="full_time", title="Backend Developer")
    assert decision(client, target["id"])["decision"] == "not_qualified"


def test_contrat_cdd_is_not_qualified(client: TestClient) -> None:
    target = add_target(client, contract_type="fixed_term", title="Backend Developer")
    assert decision(client, target["id"])["decision"] == "not_qualified"


def test_contrat_absent_is_uncertain_never_excluded(client: TestClient) -> None:
    """Absence != negation: a clear IT title with NO stated contract is uncertain, not excluded."""
    target = add_target(client, contract_type=None, title="Software Engineer")
    body = decision(client, target["id"])
    assert body["decision"] == "uncertain"


# === 2. Poste (11) ============================================================================


@pytest.mark.parametrize(
    ("title", "family"),
    [
        ("Backend Developer Alternance", "development"),
        ("Data Analyst Alternance", "data_ai"),
        ("Security Analyst Alternance", "cybersecurity"),
        ("Devops Engineer Alternance", "infra_cloud_devops"),
        ("QA Engineer Alternance", "qa_testing"),
        ("IT Project Manager Alternance", "it_technical"),
        ("Platform Engineering Alternance", "adjacent_technical"),
    ],
)
def test_poste_each_targeted_family_is_qualified(
    client: TestClient, title: str, family: str
) -> None:
    # No description: isolates the title's own family term (the default description happens to
    # mention "data analyst", which would otherwise win for every other family too).
    target = add_target(client, title=title, description=None)
    body = decision(client, target["id"])
    assert body["decision"] == "qualified"
    assert body["role_family"] == family


def test_poste_ambiguous_title_is_uncertain_not_excluded(client: TestClient) -> None:
    target = add_target(
        client,
        title="Coordinateur Regional Alternance",
        description="Vous accompagnerez les equipes locales au quotidien.",
    )
    body = decision(client, target["id"])
    assert body["decision"] == "uncertain"
    assert body["role_family"] == "unknown"


def test_poste_clearly_non_it_title_is_uncertain_not_automatically_excluded(
    client: TestClient,
) -> None:
    """No blacklist (section 11): even an obviously non-IT title only raises a question."""
    target = add_target(
        client,
        title="Vendeur en boutique Alternance",
        description="Vous conseillerez les clients en magasin.",
    )
    body = decision(client, target["id"])
    assert body["decision"] == "uncertain"


def test_poste_spontaneous_target_with_no_offer_is_uncertain(client: TestClient) -> None:
    target = add_target(client, offer=False)
    body = decision(client, target["id"])
    assert body["decision"] == "uncertain"
    assert body["role_family"] == "unknown"


def test_poste_a_family_term_found_only_in_the_description_still_qualifies(
    client: TestClient,
) -> None:
    target = add_target(
        client,
        title="Alternant polyvalent",
        description="Vous rejoindrez une equipe de cloud engineer pour operer notre plateforme.",
    )
    body = decision(client, target["id"])
    assert body["decision"] == "qualified"
    assert body["role_family"] == "infra_cloud_devops"


# === 3. Localisation (6) ======================================================================


def test_localisation_paris_is_priority(client: TestClient) -> None:
    target = add_target(client, location="Paris")
    assert decision(client, target["id"])["location_tier"] == "priority"


def test_localisation_hauts_de_seine_commune_is_priority(client: TestClient) -> None:
    target = add_target(client, location="Boulogne-Billancourt")
    assert decision(client, target["id"])["location_tier"] == "priority"


def test_localisation_other_idf_is_idf(client: TestClient) -> None:
    target = add_target(client, location="Creteil")
    assert decision(client, target["id"])["location_tier"] == "idf"


def test_localisation_other_france_is_accepted_never_rejected(client: TestClient) -> None:
    target = add_target(client, location="Lyon")
    body = decision(client, target["id"])
    assert body["location_tier"] == "accepted"
    assert body["decision"] == "qualified"  # location never gates the decision


def test_localisation_remote_with_no_place_stated_is_accepted(client: TestClient) -> None:
    target = add_target(client, location=None, company_location=None, remote_mode="remote")
    assert decision(client, target["id"])["location_tier"] == "accepted"


def test_localisation_outside_france_never_gates_the_decision(client: TestClient) -> None:
    remote = add_target(
        client, domain="us-remote.example.invalid", country_code="US", remote_mode="remote"
    )
    onsite = add_target(
        client, domain="us-onsite.example.invalid", country_code="US", remote_mode=None
    )
    remote_body = decision(client, remote["id"])
    onsite_body = decision(client, onsite["id"])
    assert remote_body["location_tier"] == "remote_abroad"
    assert onsite_body["location_tier"] == "unknown"
    assert remote_body["decision"] == "qualified"
    assert onsite_body["decision"] == "qualified"  # still never a filter


# === 4. Etudes (2) ============================================================================


def test_etudes_no_education_dimension_exists_in_criteria_v1(client: TestClient) -> None:
    dimensions = {c["dimension"] for c in profile_criteria(client)}
    assert dimensions == {"contract_type", "keyword"}


def test_etudes_a_stated_education_requirement_never_affects_the_decision(
    client: TestClient,
) -> None:
    target = add_target(
        client,
        title="Backend Developer Alternance",
        description="Bac+5 exige, ecole d'ingenieur uniquement.",
    )
    assert decision(client, target["id"])["decision"] == "qualified"


# === 5. Remuneration (2) ======================================================================


def test_remuneration_no_salary_dimension_can_exist(client: TestClient) -> None:
    """`CriterionDimension` has no salary member at all: architecturally impossible to filter on."""
    from app.models.enums import CriterionDimension

    assert not any("salary" in member.value for member in CriterionDimension)


def test_remuneration_a_low_or_absent_salary_never_affects_the_decision(
    client: TestClient,
) -> None:
    target = add_target(
        client,
        title="Backend Developer Alternance",
        description="Gratification legale minimale, aucune prime.",
    )
    assert decision(client, target["id"])["decision"] == "qualified"


# === 6. Competences (3) =======================================================================


def test_competences_missing_every_named_skill_still_qualifies(client: TestClient) -> None:
    """The offer's own example: no skill of the description is held by the candidate."""
    target = add_target(
        client,
        title="Backend Developer Alternance",
        description="Python, SQL, AWS, Docker, Kubernetes, 5 ans d'experience requis.",
    )
    assert decision(client, target["id"])["decision"] == "qualified"


def test_competences_no_skill_keyword_is_needed_to_qualify(client: TestClient) -> None:
    target = add_target(client, title="Backend Developer Alternance", description=None)
    assert decision(client, target["id"])["decision"] == "qualified"


def test_competences_presence_or_absence_of_skills_gives_the_same_decision(
    client: TestClient,
) -> None:
    with_skills = add_target(
        client,
        domain="with-skills.example.invalid",
        title="Backend Developer Alternance",
        description="Rust, Go, Terraform requis.",
    )
    without_skills = add_target(
        client, domain="without-skills.example.invalid", title="Backend Developer Alternance"
    )
    assert (
        decision(client, with_skills["id"])["decision"]
        == decision(client, without_skills["id"])["decision"]
        == "qualified"
    )


# === 7. Secteur (5) ===========================================================================


def test_secteur_sante_plus_data_analyst_is_qualified(client: TestClient) -> None:
    target = add_target(client, title="Data Analyst Alternance", name="Sante Fixture")
    assert decision(client, target["id"])["decision"] == "qualified"


def test_secteur_industrie_plus_software_engineer_is_qualified(client: TestClient) -> None:
    target = add_target(client, title="Software Engineer Alternance", name="Industrie Fixture")
    assert decision(client, target["id"])["decision"] == "qualified"


def test_secteur_retail_plus_ai_engineer_is_qualified(client: TestClient) -> None:
    target = add_target(client, title="AI Engineer Alternance", name="Retail Fixture")
    assert decision(client, target["id"])["decision"] == "qualified"


def test_secteur_no_sector_dimension_in_criteria_v1(client: TestClient) -> None:
    dimensions = {c["dimension"] for c in profile_criteria(client)}
    assert "sector" not in dimensions


def test_secteur_never_differentiates_the_decision(client: TestClient) -> None:
    a = add_target(client, domain="sector-a.example.invalid", title="Data Analyst Alternance")
    b = add_target(client, domain="sector-b.example.invalid", title="Data Analyst Alternance")
    assert decision(client, a["id"])["decision"] == decision(client, b["id"])["decision"]


# === 8. Disponibilite (3) =====================================================================


def test_disponibilite_no_availability_dimension_in_criteria_v1(client: TestClient) -> None:
    dimensions = {c["dimension"] for c in profile_criteria(client)}
    assert dimensions == {"contract_type", "keyword"}


def test_disponibilite_a_far_future_posted_date_never_affects_the_decision(
    client: TestClient,
) -> None:
    target = add_target(client, title="Backend Developer Alternance")
    assert decision(client, target["id"])["decision"] == "qualified"


def test_disponibilite_absence_of_any_date_is_never_a_rejection(client: TestClient) -> None:
    target = add_target(client, title="Backend Developer Alternance", description=None)
    assert decision(client, target["id"])["decision"] == "qualified"


# === 9. Versionnement (2) =====================================================================


def test_versionnement_the_v1_profile_is_created_once_and_reused(client: TestClient) -> None:
    first = add_target(client, domain="v1-a.example.invalid", title="Backend Developer Alternance")
    second = add_target(client, domain="v1-b.example.invalid", title="Data Analyst Alternance")
    decision(client, first["id"])
    decision(client, second["id"])
    profiles = [p for p in client.get("/api/search-profiles").json() if p["name"] == "Criteria v1"]
    assert len(profiles) == 1


def test_versionnement_unchanged_inputs_are_not_recomputed(client: TestClient) -> None:
    target = add_target(client, title="Backend Developer Alternance")
    first = decision(client, target["id"])
    second = decision(client, target["id"])
    assert first["created_at"] == second["created_at"]
    assert first["decision"] == second["decision"] == "qualified"


# === 10. Raisons (3) ==========================================================================


def test_raisons_one_entry_per_criterion_with_no_score(client: TestClient) -> None:
    target = add_target(client, title="Backend Developer Alternance")
    body = decision(client, target["id"])
    reasons = body["reasons"]
    assert {r["criterion"] for r in reasons} == {"contract_type", "keyword"}
    for reason in reasons:
        assert set(reason) == {"criterion", "result", "expected", "actual"}
        assert not isinstance(reason.get("score"), (int, float))


def test_raisons_excluded_reason_shows_the_actual_incompatible_value(client: TestClient) -> None:
    target = add_target(client, contract_type="full_time", title="Backend Developer Alternance")
    body = decision(client, target["id"])
    contract_reason = next(r for r in body["reasons"] if r["criterion"] == "contract_type")
    assert contract_reason["result"] == "incompatible"
    assert contract_reason["actual"] == "full_time"
    assert "apprenticeship" in contract_reason["expected"]


def test_raisons_uncertain_reason_shows_not_matched(client: TestClient) -> None:
    target = add_target(
        client,
        title="Coordinateur Regional Alternance",
        description="Vous accompagnerez les equipes locales au quotidien.",
    )
    body = decision(client, target["id"])
    keyword_reason = next(r for r in body["reasons"] if r["criterion"] == "keyword")
    assert keyword_reason["result"] == "not_matched"
