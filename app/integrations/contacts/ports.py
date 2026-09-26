"""ContactFinder: a reusable capability, `Company -> professional contacts with their sources`.

Any adapter (a public careers page reader, an official API, a CSV/manual entry...) implements
`ContactFinder`. It never touches the database: it receives a plain `CompanyInfo` and returns a
`ContactSearchResult`. The single `ContactService.record_search_result` is the only code that
stores the outcome, so every adapter gets the same guarantees:

- no contact and no channel without a source (`SourceSpec` is mandatory in the types);
- e-mail addresses are only stored if the adapter returned them: nothing is guessed;
- "searched and found nothing" is distinct from "not searched", and never a negative claim
  about the company.

No adapter exists yet: this step defines the port and the recording service only.
"""

from dataclasses import dataclass
from typing import Protocol

from app.models.enums import ChannelKind, InfoStatus, RoleCategory
from app.models.sources import SourceSpec


@dataclass(frozen=True)
class CompanyInfo:
    """What a finder may know about a company (no database objects)."""

    id: int
    name: str
    domain: str | None
    website_url: str | None
    careers_url: str | None


@dataclass(frozen=True)
class FoundChannel:
    kind: ChannelKind
    value: str
    source: SourceSpec  # where this exact address/number/URL was read
    status: InfoStatus = InfoStatus.FOUND


@dataclass(frozen=True)
class FoundContact:
    source: SourceSpec  # where the person and their role were read
    full_name: str | None = None
    is_generic: bool = False
    role_title: str | None = None
    role_category: RoleCategory = RoleCategory.UNKNOWN
    status: InfoStatus = InfoStatus.FOUND
    channels: tuple[FoundChannel, ...] = ()


@dataclass(frozen=True)
class ContactSearchResult:
    # False when the search could not be carried out (error, quota...): nothing is concluded.
    completed: bool
    contacts: tuple[FoundContact, ...] = ()
    # Labels of the sources actually consulted (required to conclude "nothing found").
    sources_consulted: tuple[str, ...] = ()


class ContactFinder(Protocol):
    def find(self, company: CompanyInfo) -> ContactSearchResult: ...
