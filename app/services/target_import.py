"""CSV import of targets (preview / apply).

One row = one target. Without any `offer_*` value the target is spontaneous. The preview runs
EXACTLY the same code as the apply, inside a transaction that is rolled back, so it reports
what would really happen (including duplicates inside the file) and writes nothing.

Guarantees:
- local file only, inside `data/private/imports/`, at most 1 MB and 2000 rows;
- valid rows are applied, invalid rows are reported (each row is atomic, in a savepoint);
- existing records are matched, never overwritten (differences are reported by field name);
- an e-mail is only imported with a source URL: no source, no address (the rest of the row is
  still imported);
- the report never contains row content: only field names, counters and codes;
- cells are stored as text (a leading `=`, `+`, `-` or `@` is never interpreted; a future
  spreadsheet export must neutralise them);
- nothing is fetched from the network.
"""

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, DomainError, NotFoundError, UnprocessableError
from app.models.audit import AuditEventType
from app.models.enums import (
    ChannelKind,
    EmploymentType,
    InfoStatus,
    RoleCategory,
    SourceKind,
)
from app.models.sources import SourceSpec
from app.repositories import candidate_brain as brain_repo
from app.schemas.imports import ImportReport, RowReport
from app.schemas.targets import (
    ChannelInput,
    CompanyInput,
    ContactInput,
    OpportunityInput,
    SourceInput,
    TargetCreate,
)
from app.services.audit import AuditLog
from app.services.private_files import read_document_bytes, resolve_private_file
from app.services.targets import TargetOutcome, TargetService

IMPORT_DIRECTORY = "imports"
IMPORT_SUFFIXES = frozenset({".csv"})
MAX_IMPORT_BYTES = 1024 * 1024
MAX_ROWS = 2000
DEFAULT_SOURCE_LABEL = "CSV import"

KNOWN_COLUMNS = frozenset(
    {
        "company_name",
        "company_website",
        "company_city",
        "company_country",
        "company_sector",
        "company_siren",
        "careers_url",
        "contract_type",
        "relevance_note",
        "offer_title",
        "offer_url",
        "offer_location",
        "offer_contract",
        "offer_posted",
        "offer_external_id",
        "offer_description",
        "contact_name",
        "contact_role",
        "contact_role_category",
        "contact_email",
        "contact_generic",
        "contact_status",
        "contact_source_url",
        "source_label",
        "source_url",
    }
)
OFFER_COLUMNS = (
    "offer_title",
    "offer_url",
    "offer_location",
    "offer_contract",
    "offer_posted",
    "offer_external_id",
    "offer_description",
)
CONTACT_COLUMNS = (
    "contact_name",
    "contact_role",
    "contact_role_category",
    "contact_email",
    "contact_generic",
    "contact_status",
    "contact_source_url",
)
CONTRACTS: dict[str, EmploymentType] = {
    "alternance": EmploymentType.APPRENTICESHIP,
    "apprentissage": EmploymentType.APPRENTICESHIP,
    "apprenticeship": EmploymentType.APPRENTICESHIP,
    "stage": EmploymentType.INTERNSHIP,
    "internship": EmploymentType.INTERNSHIP,
    "job": EmploymentType.FULL_TIME,
    "emploi": EmploymentType.FULL_TIME,
    "cdi": EmploymentType.FULL_TIME,
    "full_time": EmploymentType.FULL_TIME,
    "part_time": EmploymentType.PART_TIME,
    "cdd": EmploymentType.FIXED_TERM,
    "fixed_term": EmploymentType.FIXED_TERM,
    "freelance": EmploymentType.FREELANCE,
    "volunteer": EmploymentType.VOLUNTEER,
    "other": EmploymentType.OTHER,
}
TRUE_VALUES = frozenset({"true", "yes", "oui", "1", "vrai"})
FALSE_VALUES = frozenset({"false", "no", "non", "0", "faux"})


@dataclass
class ParsedCsv:
    delimiter: str
    ignored_columns: list[str]
    rows: list[tuple[int, dict[str, str | None]]]


class RowError(Exception):
    """A row cannot be imported. Holds field-level codes only (never cell values)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("invalid row")
        self.errors = errors


def _pydantic_codes(prefix: str, error: ValidationError) -> list[str]:
    return [
        f"{prefix}{'.'.join(str(part) for part in item['loc']) or 'value'}: {item['type']}"
        for item in error.errors()
    ]


def parse_csv(data: bytes) -> ParsedCsv:
    """Decode and split the file. Raises `UnprocessableError` with generic messages only."""
    if b"\x00" in data:
        raise UnprocessableError("The file is not a text CSV file")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UnprocessableError("The CSV file must be UTF-8 encoded") from None
    header = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = ";" if header.count(";") > header.count(",") else ","
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        table = [row for row in reader if any(cell.strip() for cell in row)]
    except csv.Error:
        raise UnprocessableError("The CSV file is malformed") from None
    if not table:
        raise UnprocessableError("The CSV file is empty")
    columns = [column.strip().lower() for column in table[0]]
    if len(set(columns)) != len(columns):
        raise UnprocessableError("The CSV file has duplicate column names")
    if "company_name" not in columns:
        raise UnprocessableError("The CSV file needs a company_name column")
    if len(table) - 1 > MAX_ROWS:
        raise UnprocessableError(f"The CSV file has more than {MAX_ROWS} rows")

    rows: list[tuple[int, dict[str, str | None]]] = []
    for number, cells in enumerate(table[1:], start=1):
        # Extra cells beyond the header are kept under None-like keys and reported per row.
        mapped: dict[str, str | None] = {
            column: (cells[index].strip() or None) if index < len(cells) else None
            for index, column in enumerate(columns)
        }
        if len(cells) > len(columns) and any(cell.strip() for cell in cells[len(columns) :]):
            mapped["__extra_cells__"] = "yes"
        rows.append((number, mapped))
    ignored = [column for column in columns if column not in KNOWN_COLUMNS]
    return ParsedCsv(delimiter, ignored, rows)


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return False
    lowered = value.strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    return None


def build_row(
    cells: dict[str, str | None], row_ref: str
) -> tuple[TargetCreate, SourceSpec, list[str]]:
    """Turn one CSV row into a target request. Raises `RowError`; returns warnings."""
    if cells.get("__extra_cells__"):
        raise RowError(["row: too_many_cells"])

    def get(name: str) -> str | None:
        return cells.get(name)

    errors: list[str] = []
    warnings: list[str] = []
    label = get("source_label") or DEFAULT_SOURCE_LABEL

    # Provenance of the row itself.
    page_url = get("source_url")
    if page_url:
        try:
            source_input = SourceInput(
                kind=SourceKind.PUBLIC_PAGE, label=label, url=page_url, reference=row_ref
            )
        except ValidationError as error:
            raise RowError(_pydantic_codes("source_url.", error)) from None
        spec = SourceSpec(
            SourceKind.PUBLIC_PAGE, label=label, url=source_input.url, reference=row_ref
        )
    else:
        spec = SourceSpec(SourceKind.IMPORT_FILE, label=label, reference=row_ref)

    try:
        company = CompanyInput(
            name=get("company_name") or "",
            website_url=get("company_website"),
            careers_url=get("careers_url"),
            siren=get("company_siren"),
            location=get("company_city"),
            country_code=get("company_country"),
            sector=get("company_sector"),
        )
    except ValidationError as error:
        errors.extend(_pydantic_codes("company.", error))

    contract: EmploymentType | None = None
    if get("contract_type"):
        contract = CONTRACTS.get((get("contract_type") or "").strip().lower())
        if contract is None:
            errors.append("contract_type: unknown_value")

    opportunity: OpportunityInput | None = None
    if any(get(column) for column in OFFER_COLUMNS):
        offer_contract: EmploymentType | None = None
        if get("offer_contract"):
            offer_contract = CONTRACTS.get((get("offer_contract") or "").strip().lower())
            if offer_contract is None:
                errors.append("offer_contract: unknown_value")
        try:
            opportunity = OpportunityInput(
                title=get("offer_title") or "",
                url=get("offer_url"),
                external_id=get("offer_external_id"),
                contract_type=offer_contract,
                location=get("offer_location"),
                posted_on=get("offer_posted"),
                description_text=get("offer_description"),
            )
        except ValidationError as error:
            errors.extend(_pydantic_codes("offer.", error))

    try:
        relevance_note = get("relevance_note")
        contacts = _build_contacts(cells, spec, label, row_ref, warnings)
        if errors:
            raise RowError(errors)
        request = TargetCreate(
            company=company,
            opportunity=opportunity,
            contract_type=contract,
            relevance_note=relevance_note,
            contacts=contacts,
        )
    except ValidationError as error:
        raise RowError(errors + _pydantic_codes("row.", error)) from None
    return request, spec, warnings


def _build_contacts(
    cells: dict[str, str | None],
    spec: SourceSpec,
    label: str,
    row_ref: str,
    warnings: list[str],
) -> list[ContactInput]:
    if not any(cells.get(column) for column in CONTACT_COLUMNS):
        return []
    generic = _parse_bool(cells.get("contact_generic"))
    if generic is None:
        warnings.append("contact: ignored (contact_generic: invalid_value)")
        return []
    status = InfoStatus.FOUND
    if cells.get("contact_status"):
        try:
            status = InfoStatus((cells.get("contact_status") or "").strip().lower())
        except ValueError:
            warnings.append("contact: ignored (contact_status: invalid_value)")
            return []
    role_category = RoleCategory.UNKNOWN
    if cells.get("contact_role_category"):
        try:
            role_category = RoleCategory((cells.get("contact_role_category") or "").lower())
        except ValueError:
            warnings.append("contact: ignored (contact_role_category: invalid_value)")
            return []

    channels: list[ChannelInput] = []
    email = cells.get("contact_email")
    if email:
        page = cells.get("contact_source_url") or (
            spec.url if spec.kind is SourceKind.PUBLIC_PAGE else None
        )
        if not page:
            warnings.append("contact_email: refused (no source url)")
        else:
            try:
                channels.append(
                    ChannelInput(
                        kind=ChannelKind.EMAIL,
                        value=email,
                        status=status,
                        source=SourceInput(
                            kind=SourceKind.PUBLIC_PAGE, label=label, url=page, reference=row_ref
                        ),
                    )
                )
            except ValidationError:
                warnings.append("contact_email: refused (invalid address or source url)")
    if generic and not channels:
        warnings.append("contact: ignored (a shared mailbox needs a valid, sourced address)")
        return []
    try:
        return [
            ContactInput(
                full_name=cells.get("contact_name"),
                is_generic=generic,
                role_title=cells.get("contact_role"),
                role_category=role_category,
                status=status,
                channels=channels,
            )
        ]
    except ValidationError:
        warnings.append("contact: ignored (needs contact_name, or contact_generic=true)")
        return []


class TargetImportService:
    def __init__(self, session: Session, settings: Settings, *, actor: str = "api") -> None:
        self._session = session
        self._settings = settings
        self._actor = actor

    def preview(self, source_path: str) -> ImportReport:
        return self._run(source_path, dry_run=True, expected_sha256=None)

    def apply(self, source_path: str, expected_sha256: str | None = None) -> ImportReport:
        return self._run(source_path, dry_run=False, expected_sha256=expected_sha256)

    def _read(self, source_path: str) -> tuple[bytes, str]:
        relative = Path(source_path)
        if not relative.parts or relative.parts[0] != IMPORT_DIRECTORY:
            raise UnprocessableError(f"Import files must be in data/private/{IMPORT_DIRECTORY}/")
        path = resolve_private_file(self._settings, source_path, IMPORT_SUFFIXES)
        data = read_document_bytes(path, max_bytes=MAX_IMPORT_BYTES)
        return data, hashlib.sha256(data).hexdigest()

    def _run(self, source_path: str, *, dry_run: bool, expected_sha256: str | None) -> ImportReport:
        if brain_repo.get_first_candidate(self._session) is None:
            raise NotFoundError("No candidate has been created yet")
        data, sha256 = self._read(source_path)
        if expected_sha256 and expected_sha256 != sha256:
            raise ConflictError("The file changed since it was previewed")
        parsed = parse_csv(data)

        reports = [
            self._process_row(number, cells, f"{sha256[:8]}:row {number}", dry_run)
            for number, cells in parsed.rows
        ]
        report = ImportReport(
            dry_run=dry_run,
            sha256=sha256,
            delimiter=parsed.delimiter,
            rows_total=len(reports),
            created=sum(1 for r in reports if r.outcome == "created"),
            matched=sum(1 for r in reports if r.outcome == "matched"),
            rejected=sum(1 for r in reports if r.outcome == "error"),
            companies_created=sum(1 for r in reports if r.company == "created"),
            opportunities_created=sum(1 for r in reports if r.opportunity == "created"),
            contacts_created=sum(r.contacts_created for r in reports),
            channels_created=sum(r.channels_created for r in reports),
            ignored_columns=parsed.ignored_columns,
            rows=reports,
        )
        if dry_run:
            self._session.rollback()  # nothing is written by a preview
        else:
            # The audit event commits together with the imported rows: no audit, no import.
            AuditLog(self._session).record(
                AuditEventType.IMPORT_APPLIED,
                actor=self._actor,
                details={
                    "rows": report.rows_total,
                    "created": report.created,
                    "matched": report.matched,
                    "rejected": report.rejected,
                    "bytes": len(data),
                },
            )
        return report

    def _process_row(
        self, number: int, cells: dict[str, str | None], row_ref: str, dry_run: bool
    ) -> RowReport:
        try:
            request, spec, warnings = build_row(cells, row_ref)
        except RowError as error:
            return RowReport(row=number, outcome="error", errors=error.errors)
        try:
            with self._session.begin_nested():  # each row is atomic
                outcome = TargetService(self._session).create_target(
                    request, source=spec, commit=False
                )
        except DomainError as error:
            return RowReport(row=number, outcome="error", errors=[f"row: {error}"])
        except SQLAlchemyError:
            return RowReport(row=number, outcome="error", errors=["row: database_error"])
        return _row_report(number, outcome, warnings, dry_run)


def _row_report(
    number: int, outcome: TargetOutcome, warnings: list[str], dry_run: bool
) -> RowReport:
    differences = [f"company.{item}" for item in outcome.company.differences]
    if outcome.opportunity:
        differences += [f"offer.{item}" for item in outcome.opportunity.differences]
    contact_warnings = list(warnings)
    for resolution in outcome.contacts:
        differences += [f"contact.{item}" for item in resolution.differences]
        contact_warnings += [f"contact: {item}" for item in resolution.warnings]
    return RowReport(
        row=number,
        outcome="created" if outcome.created else "matched",
        target_id=None if dry_run else outcome.target.id,
        company="created" if outcome.company.created else "matched",
        opportunity=(
            None
            if outcome.opportunity is None
            else ("created" if outcome.opportunity.created else "matched")
        ),
        contacts_created=sum(1 for r in outcome.contacts if r.created),
        contacts_matched=sum(1 for r in outcome.contacts if not r.created),
        channels_created=sum(r.channels_created for r in outcome.contacts),
        differences=differences,
        warnings=contact_warnings,
    )
