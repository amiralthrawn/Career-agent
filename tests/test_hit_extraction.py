"""HitExtractor: deterministic, no inference, structured rejections (pure, no network)."""

import pytest

from app.integrations.sourcing.ports import SearchHit
from app.models.enums import (
    EmploymentType,
    ItemReason,
    RemoteMode,
    SourceKind,
    SourcingMode,
)
from app.services.hit_extraction import MAX_EXCERPT_CHARS, HitExtractor
from tests.sourcing_fakes import SENTINEL, WEB, company_hit, hit

extractor = HitExtractor()
OFFERS, COMPANIES = SourcingMode.OFFERS, SourcingMode.COMPANIES


def reasons(result: object) -> list[ItemReason]:
    return [r.reason for r in result.rejections]  # type: ignore[attr-defined]


def bare(title: str, **attributes: str) -> SearchHit:
    """A hit with EXACTLY these attributes - bypasses the `hit()` fixture's own convenience
    parsing (see sourcing_fakes.py), so a test can prove what the production extractor itself
    does with a title alone."""
    return SearchHit(
        provider=WEB,
        title=title,
        url="https://search.example.invalid/offers/1",
        snippet=None,
        attributes=attributes,
    )


# --- Offers mode ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    ["Data Analyst Intern at Fixture Corp", "Analyste de données chez Fixture Corp"],
)
def test_a_title_alone_is_never_enough_even_when_unambiguous(title: str) -> None:
    """The extractor never parses "<offer> at/chez <company>": only a stated attribute counts,
    however clean and unambiguous the title looks."""
    result = extractor.extract(bare(title), OFFERS)

    assert result.items == () and reasons(result) == [ItemReason.MISSING_COMPANY_NAME]


def test_a_stated_identity_is_trusted_whatever_the_titles_wording() -> None:
    """Company AND offer identity come ONLY from `attributes`, never from the title's wording."""
    result = extractor.extract(
        bare("Click here to apply now", company_name="Fixture Corp", offer_title="Data Analyst"),
        OFFERS,
    )

    (item,) = result.items
    assert item.company.name == "Fixture Corp"
    assert item.opportunity is not None and item.opportunity.title == "Data Analyst"


def test_a_stated_company_without_a_stated_offer_title_is_rejected_in_offers_mode() -> None:
    """`offer_title` is exactly as required as `company_name`: never borrowed from the title or
    from the company's own name."""
    result = extractor.extract(
        bare("Fixture Corp - careers page", company_name="Fixture Corp"), OFFERS
    )

    assert result.items == () and reasons(result) == [ItemReason.MISSING_OFFER_TITLE]


def test_structured_attributes_are_used_as_read() -> None:
    result = extractor.extract(
        hit(
            "Anything at all",
            company_name="Fixture Corp",
            company_website="https://www.fixture-corp.example.invalid/",
            company_location="Faketown",
            company_country="fr",
            company_sector="Synthetic software",
            offer_title="Data Analyst Intern",
            offer_location="Faketown",
            offer_contract="apprenticeship",
            offer_remote="hybrid",
            offer_posted="2026-09",
            offer_external_id="REF-1",
        ),
        OFFERS,
    )

    (item,) = result.items
    assert item.company.website_url == "https://www.fixture-corp.example.invalid"  # normalised URL
    assert (item.company.location, item.company.country_code) == ("Faketown", "FR")
    opportunity = item.opportunity
    assert opportunity is not None
    assert opportunity.contract_type is EmploymentType.APPRENTICESHIP
    assert opportunity.remote_mode is RemoteMode.HYBRID
    assert (opportunity.posted_on, opportunity.external_id) == ("2026-09", "REF-1")


def test_the_provenance_is_the_page_the_hit_points_to() -> None:
    (item,) = extractor.extract(hit(), OFFERS).items

    assert item.source.kind is SourceKind.PUBLIC_PAGE
    assert item.source.url == "https://search.example.invalid/offers/1"
    assert item.source.reference == "fake-web" and "fake-web" in item.source.label


@pytest.mark.parametrize(
    "title",
    [
        "Data Analyst Intern",  # no company stated
        "Data Analyst Intern - Fixture Corp",  # a dash is not a statement
        "Data Analyst Intern | Fixture Corp",
        "Fixture Corp hiring now",
        "Analyst at Scale at Fixture Corp",  # two separators: which one?
        "Data Analyst at Fixture Corp | JobBoard",  # a site name glued to the company
        "Data Analyst at Fixture Corp (Faketown)",
        "at Fixture Corp",  # no offer title
        "Data Analyst at ",  # no company
    ],
)
def test_an_ambiguous_or_missing_identity_is_rejected_not_guessed(title: str) -> None:
    result = extractor.extract(bare(title), OFFERS)

    assert result.items == ()
    assert reasons(result) and reasons(result)[0] in (
        ItemReason.MISSING_COMPANY_NAME,
        ItemReason.MISSING_OFFER_TITLE,
    )


def test_a_company_without_a_stated_offer_is_rejected_in_offers_mode() -> None:
    result = extractor.extract(company_hit(), OFFERS)

    assert result.items == () and reasons(result) == [ItemReason.MISSING_OFFER_TITLE]


def test_nothing_is_derived_from_the_url_snippet_or_title() -> None:
    (item,) = extractor.extract(
        hit(
            "Data Analyst Intern at Fixture Corp",
            snippet="Alternance à Paris, télétravail, CDI, 2026",
            published="2026-01-01",
        ),
        OFFERS,
    ).items

    opportunity = item.opportunity
    assert opportunity is not None
    assert opportunity.contract_type is None and opportunity.location is None
    assert opportunity.remote_mode is None and opportunity.posted_on is None
    assert opportunity.description_text is None  # a snippet is not the offer text
    assert item.company.website_url is None  # a job page is not the company's website
    assert item.company.location is None and item.company.sector is None


@pytest.mark.parametrize(
    ("attribute", "value", "field"),
    [
        ("company_website", "not a url", "company.website_url"),
        ("company_country", "France", "company.country_code"),
        ("offer_contract", "CDI", "opportunity"),  # closed vocabulary: exact values only
        ("offer_remote", "télétravail", "opportunity"),
        ("offer_posted", "September", "opportunity.posted_on"),
    ],
)
def test_an_invalid_field_rejects_the_item_and_names_the_field_never_the_value(
    attribute: str, value: str, field: str
) -> None:
    result = extractor.extract(hit(**{attribute: value}), OFFERS)

    assert result.items == () and reasons(result) == [ItemReason.INVALID_FIELD]
    assert result.rejections[0].fields and field in result.rejections[0].fields[0]
    assert value not in repr(result.rejections)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("", ItemReason.MISSING_SOURCE_URL),
        ("   ", ItemReason.MISSING_SOURCE_URL),
        ("ftp://search.example.invalid/x", ItemReason.INVALID_SOURCE_URL),
        ("not a url", ItemReason.INVALID_SOURCE_URL),
    ],
)
def test_a_hit_without_a_usable_url_has_no_provenance_and_is_rejected(
    url: str, reason: ItemReason
) -> None:
    result = extractor.extract(hit(url=url), OFFERS)

    assert result.items == () and reasons(result) == [reason]
    assert extractor.extract(hit(url=url), COMPANIES).items == ()
    assert reasons(extractor.extract(company_hit_with_url(url), COMPANIES)) == [reason]


def company_hit_with_url(url: str):  # type: ignore[no-untyped-def]
    return hit("x", url=url, company_name="Fixture Corp")


def test_a_hit_without_provider_identity_is_rejected() -> None:
    assert reasons(extractor.extract(hit(provider=" "), OFFERS)) == [ItemReason.INVALID_PROVENANCE]


# --- Companies mode ---------------------------------------------------------------------------


def test_a_stated_company_gives_an_item_without_any_opportunity() -> None:
    result = extractor.extract(company_hit(company_sector="Synthetic software"), COMPANIES)

    (item,) = result.items
    assert item.company.name == "Fixture Corp" and item.company.sector == "Synthetic software"
    assert item.opportunity is None  # no false offer
    assert item.source.kind is SourceKind.PUBLIC_PAGE


def test_offer_attributes_never_create_an_opportunity_in_companies_mode() -> None:
    (item,) = extractor.extract(
        company_hit(offer_title="Data Analyst Intern", offer_location="Faketown"), COMPANIES
    ).items

    assert item.opportunity is None


def test_companies_mode_never_reads_a_title_either() -> None:
    result = extractor.extract(bare("Data Analyst Intern at Fixture Corp"), COMPANIES)

    assert result.items == () and reasons(result) == [ItemReason.MISSING_COMPANY_NAME]


# --- General ----------------------------------------------------------------------------------


def test_the_excerpt_is_bounded_and_unknown_attributes_are_ignored() -> None:
    (item,) = extractor.extract(
        hit(snippet="x" * 2000, secret_header=SENTINEL, authorization=f"Bearer {SENTINEL}"), OFFERS
    ).items

    assert item.excerpt is not None and len(item.excerpt) == MAX_EXCERPT_CHARS
    assert SENTINEL not in repr(item)


def test_extraction_is_deterministic() -> None:
    assert extractor.extract(hit(), OFFERS) == HitExtractor().extract(hit(), OFFERS)
    assert extractor.extract(hit("nothing"), OFFERS) == extractor.extract(hit("nothing"), OFFERS)


def test_the_extractor_imports_no_network_or_ai_library() -> None:
    from pathlib import Path

    source = Path("app/services/hit_extraction.py").read_text(encoding="utf-8")

    for forbidden in ("import requests", "import httpx", "import socket", "urllib", "openai"):
        assert forbidden not in source
