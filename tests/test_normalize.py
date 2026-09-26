"""Normalisation used for identification and de-duplication (pure functions)."""

import pytest

from app.core.normalize import (
    email_domain,
    normalize_channel_value,
    normalize_domain,
    normalize_email,
    normalize_name,
    normalize_siren,
    normalize_text,
    normalize_url,
)

# 123456782 is a synthetic number with a valid checksum, not a real company.
VALID_SIREN = "123456782"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://www.Fixture-Corp.example.invalid/careers?x=1", "fixture-corp.example.invalid"),
        ("http://fixture-corp.example.invalid:8080/path", "fixture-corp.example.invalid"),
        ("WWW.Fixture-Corp.EXAMPLE.invalid", "fixture-corp.example.invalid"),
        ("fixture-corp.example.invalid", "fixture-corp.example.invalid"),
        ("https://user:pw@fixture.example.invalid/", "fixture.example.invalid"),
        ("https://sub.fixture.example.invalid", "sub.fixture.example.invalid"),
        ("https://bücher.example.invalid", "xn--bcher-kva.example.invalid"),
    ],
)
def test_domain_normalisation(value: str, expected: str) -> None:
    assert normalize_domain(value) == expected


@pytest.mark.parametrize(
    "value",
    ["", "   ", "localhost", "not a domain", "http://", "192.168.1", "-bad-.invalid", "a..b"],
)
def test_invalid_domains_are_none(value: str) -> None:
    assert normalize_domain(value) is None


def test_name_key_ignores_case_accents_punctuation_and_legal_forms() -> None:
    keys = {
        normalize_name(name)
        for name in (
            "Fixture Corp",
            "FIXTURE CORP SAS",
            "fixture-corp",
            "Fixtüre Corp.",
            "Fixture Corp Inc",
        )
    }

    assert keys == {"fixture corp"}


def test_different_names_have_different_keys() -> None:
    assert normalize_name("Fixture Corp") != normalize_name("Fixture Labs")
    assert normalize_name("SAS") == ""  # nothing usable left


def test_text_normalisation() -> None:
    assert normalize_text("  Île-de-France, Paris ") == "ile de france paris"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://Example.INVALID/jobs/1/", "https://example.invalid/jobs/1"),
        ("https://example.invalid/jobs?id=2#apply", "https://example.invalid/jobs?id=2"),
        ("http://example.invalid", "http://example.invalid"),
    ],
)
def test_url_normalisation(value: str, expected: str) -> None:
    assert normalize_url(value) == expected


@pytest.mark.parametrize(
    "value",
    ["javascript:alert(1)", "ftp://example.invalid/x", "example.invalid", "https://", "//x"],
)
def test_only_http_urls_are_accepted(value: str) -> None:
    assert normalize_url(value) is None


def test_email_is_lowercased_and_never_completed() -> None:
    assert normalize_email("  HR.Fixture@Example.INVALID ") == "hr.fixture@example.invalid"
    assert email_domain("hr.fixture@example.invalid") == "example.invalid"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "no-at-sign",
        "a@b",
        "a@@b.invalid",
        "Name <a@b.invalid>",
        "a@b.invalid, c@d.invalid",
        "a@b.invalid\r\nBcc: x@y.invalid",
        "first last@b.invalid",
        "@b.invalid",
        "a@.invalid",
    ],
)
def test_invalid_emails_are_none(value: str) -> None:
    assert normalize_email(value) is None


def test_siren_needs_nine_digits_and_a_valid_checksum() -> None:
    assert normalize_siren(VALID_SIREN) == VALID_SIREN
    assert normalize_siren("123 456 782") == VALID_SIREN
    assert normalize_siren("123456789") is None  # wrong checksum: probably a typo
    assert normalize_siren("12345678") is None
    assert normalize_siren("12345678a") is None
    assert normalize_siren("") is None


def test_channel_values_are_validated_per_kind() -> None:
    assert normalize_channel_value("email", " A@Example.invalid ") == "a@example.invalid"
    assert normalize_channel_value("email", "nope") is None
    assert normalize_channel_value("phone", "000 000 000") == "000 000 000"
    assert normalize_channel_value("phone", "abc") is None
    assert normalize_channel_value("linkedin_url", "https://Example.invalid/in/x/") == (
        "https://example.invalid/in/x"
    )
    assert normalize_channel_value("contact_form_url", "javascript:x") is None
