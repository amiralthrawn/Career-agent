"""Provenance helpers: turn a `SourceSpec` into a stored `Source`, once per unit of work."""

from sqlalchemy.orm import Session

from app.core.errors import UnprocessableError
from app.models.enums import SourceKind
from app.models.sources import Source, SourceSpec
from app.repositories.targets import stage
from app.schemas.targets import SourceInput

MANUAL_LABEL = "Manual entry"


def manual_spec(label: str = MANUAL_LABEL, reference: str | None = None) -> SourceSpec:
    return SourceSpec(kind=SourceKind.MANUAL, label=label, reference=reference)


def spec_from_input(source: SourceInput | None) -> SourceSpec:
    if source is None:
        return manual_spec()
    return SourceSpec(
        kind=source.kind, label=source.label, url=source.url, reference=source.reference
    )


def validate_spec(spec: SourceSpec) -> None:
    """Same rules as the database checks, raised as a clear error before the insert."""
    if spec.kind is SourceKind.IMPORT_FILE and not spec.reference:
        raise UnprocessableError("An import source needs a reference (file and row)")
    if spec.kind in (SourceKind.OFFICIAL_API, SourceKind.PUBLIC_PAGE) and not (
        spec.url or spec.reference
    ):
        raise UnprocessableError("An api or public page source needs a url or a reference")


def require_channel_spec(spec: SourceSpec) -> None:
    """A contact channel (e-mail, phone...) needs a real origin.

    Accepted: a public page or official API (with url/reference), or a manual entry that says
    where the value comes from (`reference`). Refused: an import file row alone, and a manual
    entry with no explanation. In short: no source, no address.
    """
    validate_spec(spec)
    if spec.kind is SourceKind.IMPORT_FILE or (
        spec.kind is SourceKind.MANUAL and not spec.reference
    ):
        raise UnprocessableError(
            "A contact channel needs a source: a public page or API (url/reference), or a manual "
            "source with a reference saying where it comes from"
        )


class Provenance:
    """Creates each distinct source once per unit of work, and only when it is used."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._created: dict[SourceSpec, Source] = {}

    def source(self, spec: SourceSpec) -> Source:
        if spec not in self._created:
            validate_spec(spec)
            self._created[spec] = stage(
                self._session,
                Source(kind=spec.kind, label=spec.label, url=spec.url, reference=spec.reference),
            )
        return self._created[spec]
