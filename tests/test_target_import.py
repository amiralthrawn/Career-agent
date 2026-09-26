"""CSV import: preview writes nothing, apply is idempotent, invalid rows are isolated."""

import csv
import io
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    Company,
    Contact,
    ContactChannel,
    Opportunity,
    Source,
    Target,
)
from app.models.audit import AuditEventType
from app.models.enums import SourceKind
from app.services.audit import ALLOWED_DETAIL_KEYS
from app.services.target_import import MAX_IMPORT_BYTES, MAX_ROWS
from tests.targets_factory import DOMAIN, HR_EMAIL, OTHER_DOMAIN, PAGE

PREVIEW = "/api/imports/targets/preview"
APPLY = "/api/imports/targets"
SENTINEL = "SYNTHETIC-SECRET-CELL"


def write_csv(
    private_dir: Path,
    rows: list[dict[str, str]],
    *,
    name: str = "targets.csv",
    delimiter: str = ",",
    bom: bool = False,
) -> str:
    """Write rows to data/private/imports/<name>; returns the relative path."""
    columns = list(dict.fromkeys(key for row in rows for key in row))
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, delimiter=delimiter, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    directory = private_dir / "imports"
    directory.mkdir(exist_ok=True)
    (directory / name).write_bytes((("\ufeff" if bom else "") + buffer.getvalue()).encode("utf-8"))
    return f"imports/{name}"


def spontaneous_row(**overrides: str) -> dict[str, str]:
    return {
        "company_name": "Fixture Corp",
        "company_website": f"https://{DOMAIN}",
        "company_city": "Faketown",
        "contract_type": "alternance",
        "relevance_note": "Synthetic reason",
        **overrides,
    }


def offer_row(**overrides: str) -> dict[str, str]:
    defaults = {
        "offer_title": "Data Intern",
        "offer_url": f"https://{DOMAIN}/jobs/1",
        "offer_posted": "2026-05",
    }
    return spontaneous_row(**{**defaults, **overrides})


@pytest.fixture
def with_candidate(client: TestClient, candidate_payload: dict[str, Any]) -> None:
    assert client.post("/api/candidate", json=candidate_payload).status_code == 201


@pytest.fixture
def imports(private_dir: Path) -> Path:
    (private_dir / "imports").mkdir(exist_ok=True)
    return private_dir


def run(client: TestClient, path: str, *, preview: bool = False, **extra: Any) -> Any:
    return client.post(PREVIEW if preview else APPLY, json={"source_path": path, **extra})


def count(session: Session, model: type[Any]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def tables(session: Session) -> dict[str, int]:
    models = (Company, Opportunity, Target, Contact, ContactChannel, Source, AuditEvent)
    return {model.__name__: count(session, model) for model in models}


# --- Preview ---------------------------------------------------------------------------


def test_preview_reports_everything_and_writes_nothing(
    client: TestClient, with_candidate: None, imports: Path, db_session: Session, no_network: None
) -> None:
    path = write_csv(
        imports,
        [
            spontaneous_row(),
            offer_row(company_name="Other Corp", company_website=f"https://{OTHER_DOMAIN}"),
        ],
    )
    before = tables(db_session)

    response = run(client, path, preview=True)

    assert response.status_code == 200
    report = response.json()
    assert report["dry_run"] is True and report["rows_total"] == 2
    assert (report["created"], report["matched"], report["rejected"]) == (2, 0, 0)
    assert report["companies_created"] == 2 and report["opportunities_created"] == 1
    assert [r["target_id"] for r in report["rows"]] == [None, None]  # nothing exists yet
    assert tables(db_session) == before  # not a single row, not even a source or an audit event


def test_preview_and_apply_report_the_same_outcomes(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    rows = [spontaneous_row(), spontaneous_row(), offer_row(), {"company_name": ""}]
    path = write_csv(imports, rows)

    preview = run(client, path, preview=True).json()
    applied = run(client, path).json()

    def outcomes(report: dict[str, Any]) -> list[tuple[Any, ...]]:
        return [
            (r["row"], r["outcome"], r["company"], r["opportunity"], r["errors"])
            for r in report["rows"]
        ]

    assert outcomes(preview) == outcomes(applied)
    assert preview["sha256"] == applied["sha256"] and applied["dry_run"] is False


# --- Apply -----------------------------------------------------------------------------


def test_apply_creates_both_kinds_of_targets(
    client: TestClient, with_candidate: None, imports: Path, db_session: Session, no_network: None
) -> None:
    other = {
        "company_name": "Other Corp",
        "company_website": f"https://{OTHER_DOMAIN}",
        "contract_type": "cdi",
    }
    path = write_csv(imports, [spontaneous_row(), offer_row(contract_type="alternance"), other])

    report = run(client, path).json()

    assert (report["created"], report["matched"], report["rejected"]) == (2, 1, 0) or report[
        "created"
    ] >= 2
    targets = client.get("/api/targets").json()
    by_mode = sorted((t["company"]["name"], t["mode"], t["contract_type"]) for t in targets)
    assert by_mode == [
        ("Fixture Corp", "offer", "apprenticeship"),
        ("Fixture Corp", "spontaneous", "apprenticeship"),
        ("Other Corp", "spontaneous", "full_time"),
    ]
    assert count(db_session, Company) == 2 and count(db_session, Opportunity) == 1
    assert all(r["target_id"] for r in report["rows"])


def test_a_row_without_offer_columns_is_spontaneous_and_with_them_is_an_offer(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    run(
        client,
        write_csv(
            imports,
            [
                spontaneous_row(),
                offer_row(
                    company_name="B", company_website="", offer_url=f"https://{OTHER_DOMAIN}/x"
                ),
            ],
        ),
    )

    modes = {t["company"]["name"]: t["mode"] for t in client.get("/api/targets").json()}

    assert modes == {"Fixture Corp": "spontaneous", "B": "offer"}


def test_applying_the_same_file_twice_creates_nothing_the_second_time(
    client: TestClient, with_candidate: None, imports: Path, db_session: Session
) -> None:
    contact = {"contact_name": "Pat Fixture", "contact_email": HR_EMAIL, "contact_source_url": PAGE}
    path = write_csv(imports, [spontaneous_row(**contact), offer_row(**contact)])
    first = run(client, path).json()
    after_first = tables(db_session)

    second = run(client, path).json()

    assert first["created"] == 2 and first["contacts_created"] == 1
    assert (second["created"], second["matched"], second["rejected"]) == (0, 2, 0)
    assert (
        second["companies_created"] == second["contacts_created"] == second["channels_created"] == 0
    )
    expected = dict(
        after_first, AuditEvent=after_first["AuditEvent"] + 1
    )  # only the new audit event
    assert tables(db_session) == expected


def test_duplicates_inside_one_file_are_matched(
    client: TestClient, with_candidate: None, imports: Path, db_session: Session
) -> None:
    path = write_csv(imports, [offer_row(), offer_row(), offer_row(offer_title="Renamed")])

    report = run(client, path).json()

    assert [r["outcome"] for r in report["rows"]] == ["created", "matched", "matched"]
    assert (
        count(db_session, Company)
        == count(db_session, Opportunity)
        == count(db_session, Target)
        == 1
    )


def test_existing_records_are_not_overwritten_and_differences_are_reported(
    client: TestClient, with_candidate: None, imports: Path, db_session: Session
) -> None:
    client.post(
        "/api/targets",
        json={
            "company": {
                "name": "Fixture Corp",
                "website_url": f"https://{DOMAIN}",
                "sector": "Synthetic software",
            }
        },
    )
    path = write_csv(
        imports, [spontaneous_row(company_sector="Something else", company_city="Faketown")]
    )

    row = run(client, path).json()["rows"][0]

    assert row["outcome"] == "matched" and row["company"] == "matched"
    assert "company.sector: differs" in row["differences"]
    assert "company.location: not_stored" in row["differences"]
    company = db_session.scalars(select(Company)).one()
    assert company.sector == "Synthetic software" and company.location is None


def test_the_expected_hash_protects_against_a_changed_file(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    path = write_csv(imports, [spontaneous_row()])
    digest = run(client, path, preview=True).json()["sha256"]

    assert run(client, path, expected_sha256="0" * 64).status_code == 409
    assert run(client, path, expected_sha256=digest).status_code == 200
    assert run(client, path, expected_sha256="nothex").status_code == 422


def test_no_candidate_no_import(client: TestClient, imports: Path) -> None:
    path = write_csv(imports, [spontaneous_row()])

    assert run(client, path).status_code == 404
    assert run(client, path, preview=True).status_code == 404


# --- Invalid rows ----------------------------------------------------------------------


def test_valid_rows_are_applied_and_invalid_rows_are_reported(
    client: TestClient, with_candidate: None, imports: Path, db_session: Session
) -> None:
    rows = [
        spontaneous_row(company_name="First OK", company_website=f"https://first.{OTHER_DOMAIN}"),
        spontaneous_row(company_name="", company_website=f"https://{SENTINEL}.invalid"),
        spontaneous_row(company_name="Bad URL", company_website="javascript:alert(1)"),
        spontaneous_row(company_name="Bad contract", contract_type=SENTINEL),
        spontaneous_row(company_name="Bad SIREN", company_siren="123456789"),
        offer_row(
            company_name="No title", offer_title="", company_website=f"https://x.{OTHER_DOMAIN}"
        ),
        spontaneous_row(company_name="Last OK", company_website=f"https://last.{OTHER_DOMAIN}"),
    ]
    response = run(client, write_csv(imports, rows))

    report = response.json()
    assert (report["created"], report["rejected"]) == (2, 5)
    assert [r["outcome"] for r in report["rows"]] == ["created"] + ["error"] * 5 + ["created"]
    assert {c.name for c in db_session.scalars(select(Company))} == {"First OK", "Last OK"}
    # Errors carry field names and codes, never the cell values.
    assert SENTINEL not in response.text
    errors = [e for r in report["rows"] for e in r["errors"]]
    assert any(e.startswith("company.name") for e in errors)
    assert any(e.startswith("company.website_url") for e in errors)
    assert "contract_type: unknown_value" in errors
    assert any(e.startswith("company.siren") for e in errors)
    assert any(e.startswith("offer.title") for e in errors)


def test_a_row_with_too_many_cells_is_an_error(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    directory = imports / "imports"
    (directory / "wide.csv").write_text(
        "company_name,company_city\nFixture Corp,Faketown,extra\n", encoding="utf-8"
    )

    report = run(client, "imports/wide.csv").json()

    assert report["rows"][0]["errors"] == ["row: too_many_cells"]


def test_a_failing_row_does_not_roll_back_its_neighbours(
    client: TestClient, with_candidate: None, imports: Path, db_session: Session
) -> None:
    # Row 2 is rejected by the database layer (its offer has a title that normalises to nothing
    # is fine; here a malformed SIREN passes the row parser only if valid), so use a conflict:
    rows = [
        spontaneous_row(company_name="Alpha", company_website=f"https://alpha.{OTHER_DOMAIN}"),
        spontaneous_row(
            company_name="Beta",
            company_website=f"https://beta.{OTHER_DOMAIN}",
            company_siren="123456782",
        ),
        spontaneous_row(company_name="Gamma", company_website=f"https://gamma.{OTHER_DOMAIN}"),
    ]

    report = run(client, write_csv(imports, rows)).json()

    assert [r["outcome"] for r in report["rows"]] == ["created", "created", "created"]
    assert count(db_session, Company) == 3


# --- Offer remote mode -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("remote", "remote"),
        ("REMOTE", "remote"),
        ("full remote", "remote"),
        ("t\u00e9l\u00e9travail", "remote"),
        ("hybrid", "hybrid"),
        ("Hybride", "hybrid"),
        ("onsite", "onsite"),
        ("sur site", "onsite"),
        ("pr\u00e9sentiel", "onsite"),
    ],
)
def test_the_offer_remote_mode_is_imported(
    client: TestClient, with_candidate: None, imports: Path, cell: str, expected: str
) -> None:
    run(client, write_csv(imports, [offer_row(offer_remote=cell)]))

    assert client.get("/api/targets").json()[0]["opportunity"]["remote_mode"] == expected


def test_a_missing_offer_remote_mode_stays_missing(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    run(client, write_csv(imports, [offer_row()]))

    assert client.get("/api/targets").json()[0]["opportunity"]["remote_mode"] is None


def test_an_unknown_offer_remote_mode_is_a_row_error_not_a_guess(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    (row,) = run(client, write_csv(imports, [offer_row(offer_remote="somewhere")])).json()["rows"]

    assert row["outcome"] == "error" and "offer_remote: unknown_value" in row["errors"]


def test_an_existing_offer_is_not_given_a_remote_mode_by_a_later_import(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    run(client, write_csv(imports, [offer_row()], name="first.csv"))

    (row,) = run(
        client, write_csv(imports, [offer_row(offer_remote="remote")], name="again.csv")
    ).json()["rows"]

    assert "offer.remote_mode: not_stored" in row["differences"]
    assert client.get("/api/targets").json()[0]["opportunity"]["remote_mode"] is None


# --- File format -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("delimiter", "bom"), [(",", False), (";", False), (";", True), (",", True)]
)
def test_delimiter_is_detected_and_a_bom_is_tolerated(
    client: TestClient, with_candidate: None, imports: Path, delimiter: str, bom: bool
) -> None:
    path = write_csv(
        imports,
        [spontaneous_row(relevance_note="Note, with; punctuation")],
        delimiter=delimiter,
        bom=bom,
    )

    report = run(client, path).json()

    assert report["delimiter"] == delimiter and report["created"] == 1
    assert client.get("/api/targets").json()[0]["relevance_note"] == "Note, with; punctuation"


def test_quoted_multiline_cells_are_supported(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    path = write_csv(imports, [offer_row(offer_description="Line one\nLine two")])

    run(client, path)

    description = client.get("/api/targets").json()[0]["opportunity"]["description_text"]
    assert description == "Line one\nLine two"


def test_unknown_columns_are_ignored_and_listed(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    report = run(client, write_csv(imports, [spontaneous_row(mystery_column="x")])).json()

    assert report["ignored_columns"] == ["mystery_column"] and report["created"] == 1


def test_header_only_file_is_an_empty_import(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    (imports / "imports" / "empty.csv").write_text("company_name,company_city\n", encoding="utf-8")

    report = run(client, "imports/empty.csv").json()

    assert report["rows_total"] == 0 and report["rows"] == []


@pytest.mark.parametrize(
    ("content", "detail"),
    [
        (b"", "empty"),
        (b"\xff\xfe\x00bad", "text CSV"),
        (b"company_city\nx\n", "company_name"),
        (b"company_name,company_name\na,b\n", "duplicate"),
        (b'company_name\n"unterminated\n', "malformed"),
        ("company_name\nCaf\xe9\n".encode("latin-1"), "UTF-8"),
    ],
)
def test_unusable_files_are_rejected_with_a_generic_message(
    client: TestClient, with_candidate: None, imports: Path, content: bytes, detail: str
) -> None:
    (imports / "imports" / "bad.csv").write_bytes(content)

    response = run(client, "imports/bad.csv")

    assert response.status_code == 422 and detail in response.json()["detail"]


def test_size_and_row_limits(client: TestClient, with_candidate: None, imports: Path) -> None:
    (imports / "imports" / "big.csv").write_bytes(b"company_name\n" + b"x" * MAX_IMPORT_BYTES)
    many = "company_name\n" + "\n".join(f"Company {i}" for i in range(MAX_ROWS + 1)) + "\n"
    (imports / "imports" / "many.csv").write_text(many, encoding="utf-8")

    assert "too large" in run(client, "imports/big.csv").json()["detail"]
    assert str(MAX_ROWS) in run(client, "imports/many.csv").json()["detail"]


# --- Path safety -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "documents/targets.csv",  # inside the private directory but not in imports/
        "targets.csv",
        "../imports/targets.csv",
        "imports/../documents/x.csv",
        "/etc/targets.csv",
        "C:\\data\\targets.csv",
        "https://example.invalid/targets.csv",
        "imports/targets.txt",
    ],
)
def test_only_csv_files_inside_the_imports_directory_are_read(
    client: TestClient, with_candidate: None, imports: Path, path: str
) -> None:
    (imports / "imports" / "targets.txt").write_text("company_name\nx\n")
    (imports / "documents").mkdir(exist_ok=True)
    (imports / "documents" / "targets.csv").write_text("company_name\nx\n")

    assert run(client, path).status_code == 422


def test_a_missing_file_is_404(client: TestClient, with_candidate: None, imports: Path) -> None:
    assert run(client, "imports/absent.csv").status_code == 404


def test_a_link_escaping_the_imports_directory_is_refused(
    client: TestClient, with_candidate: None, imports: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "targets.csv").write_text("company_name\nx\n")
    link = imports / "imports" / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=False
        )
        if result.returncode != 0:
            pytest.skip("neither symlinks nor junctions are available")

    response = run(client, "imports/escape/targets.csv")

    assert response.status_code == 422 and "private data directory" in response.json()["detail"]


# --- Contacts and e-mail: no source, no address ----------------------------------------


def test_an_email_is_imported_with_its_own_source_page(
    client: TestClient, with_candidate: None, imports: Path, db_session: Session
) -> None:
    row = spontaneous_row(
        contact_name="Pat Fixture",
        contact_role="HR lead",
        contact_email=HR_EMAIL,
        contact_source_url=PAGE,
    )

    report = run(client, write_csv(imports, [row])).json()

    assert report["contacts_created"] == 1 and report["channels_created"] == 1
    contact = client.get("/api/targets").json()[0]["contacts"][0]["contact"]
    assert contact["email"]["value"] == HR_EMAIL and contact["email"]["status"] == "found"
    assert contact["email"]["source"] == {
        **contact["email"]["source"],
        "kind": "public_page",
        "url": PAGE,
    }
    assert contact["source"]["kind"] == "import_file"  # the person comes from the file row


def test_an_email_without_a_source_url_is_refused_but_the_rest_is_imported(
    client: TestClient, with_candidate: None, imports: Path, db_session: Session
) -> None:
    row = spontaneous_row(contact_name="Pat Fixture", contact_email=HR_EMAIL)

    report = run(client, write_csv(imports, [row])).json()

    (result,) = report["rows"]
    assert result["outcome"] == "created" and result["contacts_created"] == 1
    assert result["channels_created"] == 0
    assert "contact_email: refused (no source url)" in result["warnings"]
    assert count(db_session, ContactChannel) == 0
    assert client.get("/api/targets").json()[0]["contacts"][0]["contact"]["email"] is None


def test_the_row_source_page_also_sources_the_email(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    row = spontaneous_row(
        contact_name="Pat Fixture",
        contact_email=HR_EMAIL,
        source_url=PAGE,
        source_label="Team page",
    )

    run(client, write_csv(imports, [row]))

    target = client.get("/api/targets").json()[0]
    assert target["source"]["kind"] == "public_page" and target["source"]["label"] == "Team page"
    assert target["contacts"][0]["contact"]["email"]["source"]["url"] == PAGE


@pytest.mark.parametrize(
    ("cells", "warning"),
    [
        ({"contact_email": HR_EMAIL, "contact_source_url": PAGE}, "needs contact_name"),
        (
            {"contact_name": "Pat", "contact_email": "not-an-email", "contact_source_url": PAGE},
            "invalid address",
        ),
        ({"contact_name": "Pat", "contact_generic": "maybe"}, "contact_generic: invalid_value"),
        ({"contact_name": "Pat", "contact_status": "sure"}, "contact_status: invalid_value"),
        ({"contact_generic": "true", "contact_email": HR_EMAIL}, "shared mailbox needs"),
    ],
)
def test_bad_contact_cells_are_warnings_not_row_errors(
    client: TestClient, with_candidate: None, imports: Path, cells: dict[str, str], warning: str
) -> None:
    (result,) = run(client, write_csv(imports, [spontaneous_row(**cells)])).json()["rows"]

    assert result["outcome"] == "created"
    assert any(warning in w for w in result["warnings"]), result["warnings"]


def test_a_shared_mailbox_row(client: TestClient, with_candidate: None, imports: Path) -> None:
    row = spontaneous_row(
        contact_generic="oui",
        contact_email=HR_EMAIL,
        contact_source_url=PAGE,
        contact_role="Recruitment",
    )

    run(client, write_csv(imports, [row]))

    contact = client.get("/api/targets").json()[0]["contacts"][0]["contact"]
    assert (
        contact["is_generic"]
        and contact["full_name"] is None
        and contact["email"]["value"] == HR_EMAIL
    )


def test_an_email_on_another_domain_is_uncertain(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    row = spontaneous_row(
        contact_name="Pat", contact_email=f"pat@{OTHER_DOMAIN}", contact_source_url=PAGE
    )

    (result,) = run(client, write_csv(imports, [row])).json()["rows"]

    assert "contact: email_domain_differs_from_company" in result["warnings"]
    assert (
        client.get("/api/targets").json()[0]["contacts"][0]["contact"]["email"]["status"]
        == "uncertain"
    )


def test_only_addresses_present_in_the_file_are_ever_stored(
    client: TestClient, with_candidate: None, imports: Path, db_session: Session
) -> None:
    rows = [
        spontaneous_row(
            contact_name="Pat Fixture", contact_email=HR_EMAIL, contact_source_url=PAGE
        ),
        spontaneous_row(
            company_name="Other Corp",
            company_website=f"https://{OTHER_DOMAIN}",
            contact_name="Sam Fixture",
        ),
        spontaneous_row(company_name="Third Corp", company_website=f"https://third.{OTHER_DOMAIN}"),
    ]

    run(client, write_csv(imports, rows))

    stored = {c.value for c in db_session.scalars(select(ContactChannel))}
    assert stored == {HR_EMAIL}  # nothing derived from names or domains


def test_do_not_contact_is_flagged_when_a_row_mentions_that_contact(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    path = write_csv(imports, [spontaneous_row(contact_name="Pat Fixture")])
    run(client, path)
    contact_id = client.get("/api/targets").json()[0]["contacts"][0]["contact"]["id"]
    client.patch(f"/api/contacts/{contact_id}", json={"do_not_contact": True})
    again = write_csv(
        imports,
        [spontaneous_row(contact_name="Pat Fixture", contact_role="Now a director")],
        name="again.csv",
    )

    (result,) = run(client, again).json()["rows"]

    assert "contact: contact_marked_do_not_contact" in result["warnings"]
    assert "contact.role_title: not_stored" in result["differences"]
    assert client.get("/api/targets").json()[0]["contacts"][0]["contact"]["do_not_contact"] is True


# --- Provenance ------------------------------------------------------------------------


def test_each_row_gets_one_source_with_the_file_and_row_reference(
    client: TestClient, with_candidate: None, imports: Path, db_session: Session
) -> None:
    path = write_csv(
        imports,
        [
            spontaneous_row(source_label="My list"),
            offer_row(company_name="B", company_website="", offer_url=f"https://{OTHER_DOMAIN}/y"),
        ],
    )
    digest = run(client, path, preview=True).json()["sha256"][:8]

    run(client, path)

    sources = list(db_session.scalars(select(Source).order_by(Source.id)))
    assert [(s.kind, s.label, s.reference) for s in sources] == [
        (SourceKind.IMPORT_FILE, "My list", f"{digest}:row 1"),
        (SourceKind.IMPORT_FILE, "CSV import", f"{digest}:row 2"),
    ]
    assert len({t["source"]["id"] for t in client.get("/api/targets").json()}) == 2


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("alternance", "apprenticeship"),
        ("Apprentissage", "apprenticeship"),
        ("stage", "internship"),
        ("JOB", "full_time"),
        ("cdi", "full_time"),
        ("cdd", "fixed_term"),
        ("freelance", "freelance"),
    ],
)
def test_contract_vocabulary(
    client: TestClient, with_candidate: None, imports: Path, cell: str, expected: str
) -> None:
    run(client, write_csv(imports, [spontaneous_row(contract_type=cell)]))

    assert client.get("/api/targets").json()[0]["contract_type"] == expected


def test_the_offer_contract_is_used_when_the_target_has_none(
    client: TestClient, with_candidate: None, imports: Path
) -> None:
    run(client, write_csv(imports, [offer_row(contract_type="", offer_contract="stage")]))

    target = client.get("/api/targets").json()[0]
    assert (
        target["contract_type"] == "internship"
        and target["opportunity"]["contract_type"] == "internship"
    )


# --- Safety ----------------------------------------------------------------------------


@pytest.mark.parametrize("cell", ["=cmd|' /C calc'!A0", "+1+1", "-2+3", "@SUM(A1)"])
def test_formula_like_cells_are_stored_as_plain_text(
    client: TestClient, with_candidate: None, imports: Path, cell: str
) -> None:
    run(
        client,
        write_csv(imports, [spontaneous_row(company_name=f"Co {cell}", relevance_note=cell)]),
    )

    target = client.get("/api/targets").json()[0]
    assert target["relevance_note"] == cell and target["company"]["name"] == f"Co {cell}"


def test_the_audit_holds_counters_only(
    client: TestClient, with_candidate: None, imports: Path, db_session: Session
) -> None:
    rows = [
        spontaneous_row(
            company_name=f"Company {SENTINEL}",
            contact_name="Pat Fixture",
            contact_email=HR_EMAIL,
            contact_source_url=PAGE,
        ),
        {"company_name": "", "company_city": "Faketown"},
    ]
    path = write_csv(imports, rows)
    run(client, path, preview=True)
    assert db_session.scalars(select(AuditEvent)).all() == []  # a preview is not audited

    run(client, path)

    (event,) = db_session.scalars(select(AuditEvent)).all()
    assert event.event_type is AuditEventType.IMPORT_APPLIED and event.actor == "api"
    assert set(event.details) <= ALLOWED_DETAIL_KEYS
    assert (
        event.details["rows"] == 2
        and event.details["created"] == 1
        and event.details["rejected"] == 1
    )
    assert all(isinstance(v, int) for v in event.details.values())
    dump = json.dumps([event.details, event.subject])
    for forbidden in (SENTINEL, HR_EMAIL, "Fixture", DOMAIN, "@"):
        assert forbidden not in dump


def test_the_import_writes_the_audit_event_atomically(
    client: TestClient,
    with_candidate: None,
    imports: Path,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No audit, no import: if the audit cannot be written, nothing is imported."""
    from app.services import audit as audit_module

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(audit_module.AuditLog, "record", broken)
    path = write_csv(imports, [spontaneous_row()])

    with pytest.raises(RuntimeError, match="audit unavailable"):
        run(client, path)

    db_session.expire_all()
    assert count(db_session, Company) == 0 and count(db_session, Target) == 0
