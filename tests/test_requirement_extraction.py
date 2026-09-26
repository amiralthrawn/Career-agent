"""Deterministic extraction of requirements (step 3b). Pure: no database, no network.

Every text below is synthetic.
"""

import json
import re

import pytest

from app.models.enums import RequirementImportance, RequirementKind
from app.services.requirement_extraction import (
    ExtractedRequirement,
    RequirementExtractor,
    experience_years,
)
from app.services.skill_taxonomy import TAXONOMY_PATH, SkillTaxonomy, TaxonomyError, load_taxonomy

extractor = RequirementExtractor()

OFFER = """Data analyst apprenticeship at a fictional company.
Python is required. SQL is a plus.
Nice to have: Power BI.
We also use Tableau internally.
2 years of experience in data analysis.
"""


def extract(text: str) -> dict[str, ExtractedRequirement]:
    return {item.key: item for item in extractor.extract(text)}


# --- Extraction -------------------------------------------------------------------------------


def test_extracts_python_sql_and_data_topics() -> None:
    found = extract("You know Python and SQL. Data analysis and data visualisation are daily work.")

    assert {"python", "sql", "data_analysis", "data_visualization"} <= set(found)
    assert all(item.kind is RequirementKind.SKILL for item in found.values())


def test_explicit_required_markers_give_required() -> None:
    found = extract(
        "Python is required.\nSQL est indispensable.\nGit: mandatory.\nMust know Docker."
    )

    assert {key: item.importance for key, item in found.items()} == {
        "python": RequirementImportance.REQUIRED,
        "sql": RequirementImportance.REQUIRED,
        "git": RequirementImportance.REQUIRED,
        "docker": RequirementImportance.REQUIRED,
    }


def test_explicit_nice_to_have_markers_give_nice_to_have() -> None:
    found = extract(
        "SQL is a plus.\nNice to have: Power BI.\nDocker souhaité.\nGit apprécié.\nLinux (bonus)."
    )

    assert {item.importance for item in found.values()} == {RequirementImportance.NICE_TO_HAVE}
    assert set(found) == {"sql", "power_bi", "docker", "git", "linux"}


@pytest.mark.parametrize(
    "text",
    [
        "We use Python every day.",  # no marker at all
        "Python is not required.",  # a NEGATED marker is not a marker
        "No prior knowledge of Python is required.",
        "Python n'est pas obligatoire.",
        "Nous souhaitons recruter un profil Python.",  # a verb, not the marker "souhaité"
        "Plus de 3 ans avec Python.",  # French "plus de" (more than) is not "a plus"
    ],
)
def test_doubt_or_no_marker_gives_unspecified(text: str) -> None:
    assert {item.importance for item in extract(text).values()} == {
        RequirementImportance.UNSPECIFIED
    }


def test_a_marker_in_parentheses_qualifies_only_the_term_before_it() -> None:
    found = extract("Python (required), SQL (nice to have), Docker")

    assert found["python"].importance is RequirementImportance.REQUIRED
    assert found["sql"].importance is RequirementImportance.NICE_TO_HAVE
    assert found["docker"].importance is RequirementImportance.UNSPECIFIED  # nothing says


def imp(text: str) -> dict[str, str]:
    return {key: item.importance.value for key, item in extract(text).items()}


def test_markers_are_read_sentence_by_sentence() -> None:
    assert imp("Python is required. SQL is a plus.") == {
        "python": "required",
        "sql": "nice_to_have",
    }


def test_a_general_sentence_never_makes_a_skill_required() -> None:
    assert imp("We are looking for someone who knows Python. SQL is a plus.") == {
        "python": "unspecified",  # nothing in its sentence states an importance
        "sql": "nice_to_have",
    }
    assert imp("We use Python, SQL and Docker every day. Knowledge of Git is a plus.") == {
        "python": "unspecified",
        "sql": "unspecified",
        "docker": "unspecified",
        "git": "nice_to_have",
    }


def test_contradictory_markers_in_one_sentence_are_read_clause_by_clause() -> None:
    assert imp("Python required, SQL a plus") == {"python": "required", "sql": "nice_to_have"}
    assert imp("Python is required, SQL is a plus and Docker is a plus.") == {
        "python": "required",
        "sql": "nice_to_have",
        "docker": "nice_to_have",
    }


def test_a_clause_without_marker_never_borrows_another_clauses_marker() -> None:
    found = imp("Python, SQL required, Docker a plus")

    assert found == {"python": "unspecified", "sql": "required", "docker": "nice_to_have"}


@pytest.mark.parametrize(
    "text",
    [
        "Python is required and SQL is a plus.",  # mixed inside ONE clause: cannot attribute
        "Python required or SQL a plus, whichever.",
        "Python is not required, SQL is a plus.",  # negation makes the whole sentence a doubt
    ],
)
def test_an_ambiguous_sentence_stays_unspecified(text: str) -> None:
    assert set(imp(text).values()) == {"unspecified"}


def test_a_short_block_takes_its_importance_from_its_heading() -> None:
    text = "Required:\n- Python\n- SQL\n\nNice to have:\n- Docker\n- Git"

    found = extract(text)

    assert {key: item.importance.value for key, item in found.items()} == {
        "python": "required",
        "sql": "required",
        "docker": "nice_to_have",
        "git": "nice_to_have",
    }
    for item in found.values():
        assert item.excerpt in text  # still an exact slice of the source
        assert item.excerpt.startswith(("Required:", "Nice to have:"))  # it quotes the heading
        assert item.label.lower() in item.excerpt.lower()  # the item itself is quoted too


def test_a_heading_governs_neither_a_blank_line_after_it_nor_a_heading_without_marker() -> None:
    assert imp("Required:\n- Python\n\nWe also like Docker") == {
        "python": "required",
        "docker": "unspecified",
    }
    assert imp("Skills:\n- Python") == {"python": "unspecified"}  # a heading with no marker
    assert imp("Required:\n- Python\nOther skills:\n- Docker") == {
        "python": "required",
        "docker": "unspecified",
    }


def test_a_heading_governs_only_a_short_block() -> None:
    filler = "\n".join(f"- {'unrelated words ' * 3}{n}" for n in range(9))
    text = f"Required:\n{filler}\n- Python"  # the item is 10 lines after the heading

    assert imp(text) == {"python": "unspecified"}


def test_a_heading_too_far_to_be_quoted_with_the_item_is_not_used() -> None:
    filler = "\n".join("- " + "x" * 90 for _ in range(6))
    text = f"Required:\n{filler}\n- Python"  # heading to item is over the excerpt limit

    assert imp(text) == {"python": "unspecified"}


def test_a_term_that_contradicts_its_heading_is_a_doubt() -> None:
    assert imp("Nice to have:\n- Python is required") == {"python": "unspecified"}
    assert imp("Nice to have:\n- Python is a plus\n- Docker") == {
        "python": "nice_to_have",
        "docker": "nice_to_have",
    }
    assert imp("Nice to have:\n- Python (required)") == {"python": "unspecified"}


def test_a_negated_heading_gives_no_importance() -> None:
    assert imp("Not required:\n- Python") == {"python": "unspecified"}


def test_a_heading_also_governs_an_experience_duration() -> None:
    found = extract("Required:\n- 3 years of experience")

    assert found["experience_years:3"].importance is RequirementImportance.REQUIRED
    assert found["experience_years:3"].excerpt == "Required:\n- 3 years of experience"


def test_conflicting_mentions_of_one_skill_are_unspecified_and_agreeing_ones_are_kept() -> None:
    conflicting = extract("Python is required.\nPython is a plus for the second role.")
    agreeing = extract("Python is required.\nWe use Python daily.")

    assert conflicting["python"].importance is RequirementImportance.UNSPECIFIED
    assert agreeing["python"].importance is RequirementImportance.REQUIRED
    assert agreeing["python"].excerpt == "Python is required."  # the sentence that says so


@pytest.mark.parametrize(
    ("text", "value", "years"),
    [
        ("2 years of experience", "2 years", 2),
        ("2+ years experience required", "2+ years", 2),
        ("At least 3 years of professional experience", "3 years", 3),
        ("2 ans d'expérience", "2 ans", 2),
        ("3 ans minimum d'expérience", "3 ans", 3),
        ("Expérience de 4 ans", "4 ans", 4),
        ("2 à 3 ans d'expérience", "2 à 3 ans", 2),
    ],
)
def test_explicit_experience_is_kept_as_written(text: str, value: str, years: int) -> None:
    (item,) = [r for r in extractor.extract(text) if r.kind is RequirementKind.EXPERIENCE]

    assert item.value == value and experience_years(item.value) == years
    assert item.qualifier is None


def test_experience_that_names_a_domain_keeps_that_domain_as_written() -> None:
    (item,) = [
        r
        for r in extractor.extract("3 years of experience in data analysis, ideally.")
        if r.kind is RequirementKind.EXPERIENCE
    ]

    assert item.qualifier == "in data analysis"


@pytest.mark.parametrize(
    "text",
    [
        "Experience with teams.",  # no number
        "Founded in 2019, with 12 people and a long experience of our own.",
        "Open 24 months a year with experience.",  # months are not read as years
    ],
)
def test_no_experience_requirement_is_invented(text: str) -> None:
    assert [r for r in extractor.extract(text) if r.kind is RequirementKind.EXPERIENCE] == []


@pytest.mark.parametrize(
    "text",
    [
        "Vitamin C and R&D are not skills.",
        "Go to market with a plan C.",
        "A go-getter attitude.",
        "Excel in a fast-paced team.",  # the verb, not the tool
        "Rust on the old pipes.",
        "MySQL and NoSQL only.",  # neighbours are separate skills, never "SQL"
        "T-SQL and PL/SQL dialects.",
    ],
)
def test_ambiguous_short_terms_are_filtered(text: str) -> None:
    assert not set(extract(text)) & {"c", "r", "go", "excel", "rust", "sql"}


def test_a_term_glued_to_a_longer_word_is_not_detected() -> None:
    assert set(extract("JavaScript only. Javascripted and Pythonic things.")) == {"javascript"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Knowledge of R and Python.", {"r", "python"}),
        ("Python, C, Java.", {"python", "c", "java"}),
        ("Langage C requis.", {"c"}),
        ("Développeur Go.", {"go"}),
        ("Microsoft Excel and Power BI.", {"excel", "power_bi"}),
        ("C++ and C# and Node.js.", {"cpp", "csharp", "nodejs"}),
        ("PostgreSQL, Postgres and SQL Server.", {"postgresql", "sqlserver"}),
    ],
)
def test_short_terms_are_kept_when_the_context_is_unambiguous(
    text: str, expected: set[str]
) -> None:
    assert set(extract(text)) == expected


def test_a_strict_term_needs_its_exact_spelling() -> None:
    assert "go" not in extract("Développeur go et r.")
    assert "r" not in extract("Développeur go et r.")


def test_neighbouring_skills_are_separate_requirements() -> None:
    found = set(extract("Tableau, MySQL and PostgreSQL."))

    assert found == {"tableau", "mysql", "postgresql"}  # no "power_bi", no "sql"


# --- Exact excerpt and determinism ------------------------------------------------------------


def test_every_excerpt_is_an_exact_slice_of_the_source() -> None:
    for item in extractor.extract(OFFER):
        assert item.excerpt in OFFER, item.excerpt
        assert item.excerpt == item.excerpt.strip()


def test_a_long_text_keeps_an_exact_bounded_window() -> None:
    text = "Intro " + ("word " * 300) + "Python is required " + ("more " * 300) + "end"

    (item,) = extractor.extract(text)

    assert item.excerpt in text and len(item.excerpt) <= 500 and "Python" in item.excerpt


def test_extraction_is_deterministic_and_never_reads_the_network(no_network: None) -> None:
    first = extractor.extract(OFFER)
    second = RequirementExtractor().extract(OFFER)

    assert first == second
    assert [item.key for item in first] == [  # order of first appearance
        "python",
        "sql",
        "power_bi",
        "tableau",
        "data_analysis",
        "experience_years:2:in data analysis",
    ]


def test_an_empty_text_gives_no_requirement() -> None:
    assert extractor.extract("") == [] and extractor.extract("   \n\n ") == []


# --- Taxonomy ---------------------------------------------------------------------------------


def test_the_taxonomy_is_versioned_and_consistent() -> None:
    taxonomy = load_taxonomy()

    assert taxonomy.version and len(taxonomy.entries) > 30
    for entry in taxonomy.entries:
        assert set(entry.related) <= {other.key for other in taxonomy.entries}


def test_the_taxonomy_resolves_names_through_aliases_only() -> None:
    taxonomy = load_taxonomy()

    assert taxonomy.canonical_key("PostgreSQL") == taxonomy.canonical_key("postgres")
    assert taxonomy.canonical_key("PowerBI") == taxonomy.canonical_key("Power-BI") == "power_bi"
    assert taxonomy.canonical_key("py") == "python"  # weak alias: names only
    assert taxonomy.canonical_key("Something Unlisted") == "something unlisted"
    assert taxonomy.canonical_key("C++") != taxonomy.canonical_key("C")


def test_proximity_is_declared_but_never_an_alias_nor_coverage() -> None:
    taxonomy = load_taxonomy()

    assert "tableau" in taxonomy.related_keys("power_bi")
    assert taxonomy.canonical_key("Tableau") != taxonomy.canonical_key("Power BI")
    assert "pandas" in taxonomy.related_keys("python")
    assert taxonomy.covering_keys("python") == ()  # nothing covers Python but Python
    assert taxonomy.covering_keys("power_bi") == ()


def test_explicit_coverage_is_one_way_and_distinct_from_alias_and_proximity() -> None:
    taxonomy = load_taxonomy()

    assert taxonomy.canonical_key("SQL") != taxonomy.canonical_key("PostgreSQL")  # not aliases
    assert set(taxonomy.covering_keys("sql")) == {
        "postgresql",
        "mysql",
        "sqlserver",
        "oracle",
        "sqlite",
    }
    assert taxonomy.covering_keys("postgresql") == ()  # SQL does not cover PostgreSQL
    for entry in taxonomy.entries:
        assert not set(entry.implies) & set(entry.related)  # a pair is one relation or the other


def test_an_ambiguous_coverage_declaration_is_refused() -> None:
    base = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))

    def variant(key: str, **changes: object) -> dict[str, object]:
        skills = [{**s, **changes} if s["key"] == key else s for s in base["skills"]]
        return {**base, "skills": skills}

    with pytest.raises(TaxonomyError):
        SkillTaxonomy(variant("sql", implies=["postgresql"]))  # mutual coverage
    with pytest.raises(TaxonomyError):
        SkillTaxonomy(variant("postgresql", related=["sql"]))  # implied AND merely related
    with pytest.raises(TaxonomyError):
        SkillTaxonomy(variant("postgresql", implies=["nowhere"]))
    with pytest.raises(TaxonomyError):
        SkillTaxonomy(variant("python", implies=["python"]))


def test_the_extractor_never_detects_a_weak_alias_in_free_text() -> None:
    assert extract("We like py, js and ml.") == {}


def test_an_inconsistent_taxonomy_is_refused() -> None:
    base = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    duplicate = {**base, "skills": [*base["skills"], {**base["skills"][0], "key": "clone"}]}
    dangling = {**base, "skills": [{**base["skills"][0], "related": ["nowhere"]}]}

    with pytest.raises(TaxonomyError):
        SkillTaxonomy(duplicate)  # an alias owned by two entries
    with pytest.raises(TaxonomyError):
        SkillTaxonomy(dangling)


def test_the_taxonomy_holds_no_personal_data() -> None:
    raw = TAXONOMY_PATH.read_text(encoding="utf-8")

    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", raw)  # no e-mail address
    assert not re.search(r"\+?\d[\d ().-]{8,}\d", raw)  # no phone-like number
    assert "http" not in raw
