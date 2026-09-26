"""Parser tests on fully fictional CV text: propose, never invent."""

from typing import Any

from app.models.enums import EvidenceTargetType as Kind
from app.services import cv_parser
from app.services.cv_parser import ProposalDraft, parse_cv
from tests.docx_factory import SYNTHETIC_CV_LINES

CV_TEXT = "\n".join(SYNTHETIC_CV_LINES)


def by_kind(drafts: list[ProposalDraft], kind: Kind) -> list[ProposalDraft]:
    return [draft for draft in drafts if draft.kind is kind]


def parse(text: str = CV_TEXT) -> list[ProposalDraft]:
    return parse_cv(text).drafts


def test_every_section_produces_proposals_with_their_excerpt() -> None:
    drafts = parse()

    assert {draft.kind for draft in drafts} == set(Kind)
    assert all(draft.source_excerpt.strip() for draft in drafts)
    # The excerpt is the exact passage of the CV.
    dashboard = by_kind(drafts, Kind.PROJECT)[0]
    assert dashboard.source_excerpt in CV_TEXT
    assert "Fixture Dashboard" in dashboard.source_excerpt


def test_education_fields_and_month_precision_dates() -> None:
    master, licence = by_kind(parse(), Kind.EDUCATION)

    assert master.data["institution"] == "Fixture University"
    assert master.data["degree"] == "Master Fixture Science"
    # "sept. 2021 - juin 2023": months are kept as months, no day is invented.
    assert master.data["start_date"] == "2021-09"
    assert master.data["end_date"] == "2023-06"
    assert master.uncertainties == []
    assert licence.data["institution"] == "Test Institute of Fixtures"
    assert licence.data["degree"] == "Licence Fixtures"
    # "2018 - 2021": years are kept as years, no month or day is invented.
    assert (licence.data["start_date"], licence.data["end_date"]) == ("2018", "2021")


def test_full_dates_are_kept_and_employment_type_needs_explicit_words() -> None:
    (experience,) = by_kind(parse(), Kind.EXPERIENCE)

    assert experience.data["start_date"] == "2022-06-15"
    assert experience.data["end_date"] == "2022-09-15"
    assert experience.data["title"] == "Stagiaire Data Fixture"
    assert experience.data["company"] == "Fixture Corp"
    assert experience.data["employment_type"] == "internship"
    assert experience.uncertainties == []


def test_skill_levels_are_never_inferred_from_qualifiers() -> None:
    skills = {d.data["name"]: d for d in by_kind(parse(), Kind.SKILL)}

    assert set(skills) == {"Python", "SQL", "FixtureLang", "Git"}
    assert all(skill.data["level"] is None for skill in skills.values())
    assert skills["Python"].data["category"] == "Langages"
    assert cv_parser.PARENTHESES_IGNORED in skills["Python"].uncertainties
    assert skills["SQL"].uncertainties == []


def test_language_levels_only_when_explicit() -> None:
    languages = {d.data["language"]: d for d in by_kind(parse(), Kind.LANGUAGE)}

    assert languages["Français"].data["level"] == "native"
    assert languages["Anglais"].data["level"] == "b2"
    assert languages["Espagnol"].data["level"] is None
    assert cv_parser.LEVEL_UNMAPPED in languages["Espagnol"].uncertainties


def test_test_scores_are_not_languages() -> None:
    drafts = parse("LANGUES\nAnglais : C1\nTOEIC : 900\n")

    assert [d.data["language"] for d in drafts] == ["Anglais"]


def test_certification_and_project_urls() -> None:
    (certification,) = by_kind(parse(), Kind.CERTIFICATION)
    (project,) = by_kind(parse(), Kind.PROJECT)

    assert certification.data["name"] == "Fixture Certified Practitioner"
    assert certification.data["issuer"] == "Fixture Authority"
    assert certification.data["issue_date"] == "2023"  # year only stays a year
    assert project.data["repository_url"] == "https://github.com/example-fixture/fixture-dashboard"
    assert project.data["url"] is None


def test_missing_required_fields_are_reported_not_invented() -> None:
    drafts = parse("EXPERIENCES\n2023 - 2024\n* Synthetic task\n")

    assert len(drafts) == 1
    data: dict[str, Any] = drafts[0].data
    assert data["company"] is None and data["title"] is None
    assert cv_parser.missing_field("company") in drafts[0].uncertainties
    assert cv_parser.missing_field("title") in drafts[0].uncertainties


def test_single_date_is_not_assigned_to_start_or_end() -> None:
    (education,) = parse("FORMATION\nMaster Fixture, Test Institute of Fixtures 2022\n")

    assert education.data["start_date"] is None and education.data["end_date"] is None
    (note,) = [u for u in education.uncertainties if u.startswith(cv_parser.SINGLE_DATE)]
    assert "Stated: 2022 (year)" in note  # the date stays visible, with its real precision


def test_ongoing_period_leaves_end_date_empty() -> None:
    (experience,) = parse("EXPERIENCE\nFixture Developer - Example Labs 06/2023 - Present\n")

    assert experience.data["start_date"] == "2023-06"
    assert experience.data["end_date"] is None


def test_english_and_french_month_names() -> None:
    (english,) = parse("EXPERIENCE\nFixture Developer - Example Labs March 2022 - May 2023\n")
    (french,) = parse("EXPERIENCE\nFixture Developer - Example Labs février 2022 - août 2023\n")

    assert (english.data["start_date"], english.data["end_date"]) == ("2022-03", "2023-05")
    assert (french.data["start_date"], french.data["end_date"]) == ("2022-02", "2023-08")


def test_absent_sections_produce_no_proposals_and_no_negation() -> None:
    """A CV without a skills section says nothing about skills - it does not 'lack' them."""
    drafts = parse("FORMATION\nMaster Fixture, Test Institute of Fixtures\n")

    assert {draft.kind for draft in drafts} == {Kind.EDUCATION}
    assert by_kind(drafts, Kind.SKILL) == []


def test_duplicates_inside_a_document_are_collapsed() -> None:
    result = parse_cv("COMPETENCES\nPython, SQL\nCOMPETENCES\npython, Git\n")

    assert sorted(d.data["name"].lower() for d in result.drafts) == ["git", "python", "sql"]
    assert result.stats["duplicates_skipped"] == 1


def test_unknown_or_ignored_sections_and_prose_are_not_turned_into_facts() -> None:
    result = parse_cv(
        "PROFIL\nPython expert with ten years of synthetic experience.\nLOISIRS\nFootball, Chess\n"
    )

    assert result.drafts == []


def test_content_after_an_unrecognised_title_is_not_read_as_a_list_section() -> None:
    """An unknown trailing section must not leak into languages, skills or certifications."""
    drafts = parse(
        "LANGUES\nAnglais : C1\n\nCentres d'attention\nFootball, Chess\n"
        "COMPETENCES\nPython\n\nDivers\nJardinage, Lecture\n"
    )

    assert [(d.kind, d.data.get("language") or d.data.get("name")) for d in drafts] == [
        (Kind.LANGUAGE, "Anglais"),
        (Kind.SKILL, "Python"),
    ]


def test_blank_lines_alone_do_not_end_a_list_section() -> None:
    drafts = parse("COMPETENCES\nLangages : Python, SQL\n\nOutils : Git\n")

    assert sorted(d.data["name"] for d in drafts) == ["Git", "Python", "SQL"]


def test_parser_is_deterministic() -> None:
    assert parse_cv(CV_TEXT) == parse_cv(CV_TEXT)
