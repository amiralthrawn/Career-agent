"""Non-gating location tier for a target (step 3a display) - see docs/qualification_v1.md.

Section 3 of the spec is explicit: location is a hierarchy of PREFERENCE, never an eliminatory
filter. This module only labels a target for display (`priority` / `idf` / `accepted` /
`remote_abroad` / `unknown`) - never a numeric score, and it never changes `QualificationStatus`.

The offer's own `location` is preferred when there is one; a spontaneous target (no offer) falls
back to the company's `location`/`country_code`. Matching is a plain, versioned, best-effort text
search (like the existing `keyword` criterion) - not a geocoder: an unusual spelling or a location
given only as a postal code may fall through to `unknown` rather than a more specific tier. That
is intentional (absence of a confident match is never turned into a worse tier), and is the kind
of limitation a future version can improve without changing any stored qualification.
"""

from dataclasses import dataclass

from app.core.normalize import normalize_text
from app.models import Target
from app.models.enums import LocationTier, RemoteMode

TIER_VERSION = "v1"

# Paris (75) and Hauts-de-Seine (92): priority 1 (section 3).
PRIORITY_TERMS: tuple[str, ...] = (
    "paris",
    "hauts de seine",
    "boulogne billancourt",
    "nanterre",
    "courbevoie",
    "issy les moulineaux",
    "levallois perret",
    "puteaux",
    "clichy",
    "neuilly sur seine",
    "colombes",
    "asnieres sur seine",
    "rueil malmaison",
)

# The rest of Ile-de-France: priority 2 (section 3).
IDF_TERMS: tuple[str, ...] = (
    "ile de france",
    "idf",
    "seine saint denis",
    "saint denis",
    "val de marne",
    "creteil",
    "yvelines",
    "versailles",
    "essonne",
    "evry",
    "val d oise",
    "cergy",
    "seine et marne",
    "melun",
)


@dataclass(frozen=True)
class LocationTierResult:
    tier: LocationTier
    matched_text: str | None  # the location text tier matching was based on, if any


def _mentions(text: str, terms: tuple[str, ...]) -> bool:
    padded = f" {normalize_text(text)} "
    return any(f" {normalize_text(term)} " in padded for term in terms)


def classify(target: Target) -> LocationTierResult:
    offer = target.opportunity
    company = target.company
    location_text = (offer.location if offer else None) or company.location
    remote = offer.remote_mode if offer else None
    country = (company.country_code or "").upper() if company.country_code else None

    if country and country != "FR":
        # Hors France (section 3's "également ouvert"): only a STATED remote offer qualifies -
        # otherwise there simply isn't enough to place it in any tier.
        if remote is RemoteMode.REMOTE:
            return LocationTierResult(LocationTier.REMOTE_ABROAD, location_text)
        return LocationTierResult(LocationTier.UNKNOWN, location_text)

    if location_text and _mentions(location_text, PRIORITY_TERMS):
        return LocationTierResult(LocationTier.PRIORITY, location_text)
    if location_text and _mentions(location_text, IDF_TERMS):
        return LocationTierResult(LocationTier.IDF, location_text)
    if location_text or remote is not None:
        # Some French (or unstated-country) location, or an explicit remote mode: "accepted"
        # (section 3's "France entière si télétravail raisonnable").
        return LocationTierResult(LocationTier.ACCEPTED, location_text)
    return LocationTierResult(LocationTier.UNKNOWN, None)
