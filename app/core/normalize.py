"""Normalisation used to identify and de-duplicate companies, contacts and offers.

Pure functions, no I/O. A normalised value is a *key* for comparison; original values are
kept for display, except URLs and e-mail addresses, which are stored in their normal form.
"""

import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

_LEGAL_FORMS = frozenset(
    {"sas", "sasu", "sarl", "eurl", "sa", "snc", "sci", "inc", "ltd", "llc", "gmbh", "plc"}
)
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_EMAIL_RE = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+$")
_SIREN_RE = re.compile(r"^\d{9}$")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize_text(text: str) -> str:
    """Lowercase, accent-free, punctuation-free form of a phrase."""
    return re.sub(r"[^a-z0-9]+", " ", _strip_accents(text).casefold()).strip()


def normalize_name(text: str) -> str:
    """Key of a company or person name: also ignores legal forms (`Acme SAS` == `Acme`)."""
    words = [word for word in normalize_text(text).split() if word not in _LEGAL_FORMS]
    return " ".join(words)


def normalize_domain(value: str) -> str | None:
    """Host of a URL or bare domain, lowercase, without `www.`, port or path. None if invalid."""
    text = value.strip()
    if not text:
        return None
    parts = urlsplit(text if "://" in text else f"//{text}")
    host = (parts.hostname or "").strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if not _HOST_RE.match(host) or host.rsplit(".", 1)[1].isdigit():  # numeric TLD = IP address
        return None
    return host


def normalize_url(value: str) -> str | None:
    """http(s) URL with a lowercase host, no fragment and no trailing slash. None if invalid."""
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if normalize_domain(parts.hostname) is None:
        return None
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))


def normalize_email(value: str) -> str | None:
    """Lowercase plain address, or None. Never completes or guesses anything."""
    address = value.strip().lower()
    if any(char in address for char in "\r\n\t ,;<>()"):
        return None
    return address if _EMAIL_RE.match(address) else None


def email_domain(address: str) -> str:
    return address.rsplit("@", 1)[1]


def normalize_siren(value: str) -> str | None:
    """A French SIREN: 9 digits with a valid Luhn checksum (catches typos). None if invalid."""
    digits = re.sub(r"[\s.]", "", value)
    if not _SIREN_RE.match(digits):
        return None
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return digits if total % 10 == 0 else None


_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ().-]{5,24}$")


def normalize_channel_value(kind: str, value: str) -> str | None:
    """Validate/normalise a contact channel value. `kind` is a ChannelKind value."""
    text = value.strip()
    if kind == "email":
        return normalize_email(text)
    if kind == "phone":
        return text if _PHONE_RE.match(text) else None
    return normalize_url(text)  # linkedin_url, contact_form_url
