# CV ingestion (V0.1)

## Purpose

Turn the candidate's private CV into **proposals** that a human reviews, and only then into
Candidate Brain facts backed by Evidence. Everything runs **locally**: no API key, no LLM, no
network call, no third-party service.

```text
private CV (data/private/)
  -> local text extraction        (app/services/document_text.py)
  -> structured proposals         (app/services/cv_parser.py, deterministic)
  -> PENDING proposals in the DB  (tables document_ingestions, ingestion_proposals)
  -> human decision: accept / reject
  -> fact + Evidence + EvidenceLink, in one transaction   (app/services/cv_ingestion.py)
```

Supported format in V0.1: **`.docx`** (the CV placed in `data/private/documents/`).
PDF is not supported yet: extraction goes through one function per format, so it can be added
without touching the rest of the chain.

## Strict rules

1. An extracted item is **never** a fact. Ingestion creates only `pending` proposals; no fact,
   no Evidence and no link exist until a human accepts.
2. Each proposal keeps its **exact source passage** (`source_excerpt`) and its
   **uncertainties**.
3. A rejected proposal creates nothing, and its decision is final.
4. An accepted proposal creates the fact (or reuses the identical existing one), the document's
   Evidence and the EvidenceLink, atomically.
5. **Human acceptance is not verification.** The CV Evidence is created with `verified = false`
   and `confidence = medium`, so facts end up `known`, never `verified`. `verified` requires an
   independent check, done separately (not implemented yet).
6. Nothing is invented. A value that is not clearly stated stays `null`: no level, no degree, no
   company is guessed, and **a date is never made more precise than the CV states** (see
   "Dates" below). Skills come **only** from a skills section, never inferred from
   projects or jobs. A parenthetical such as `(advanced)` is flagged and **not** turned into a
   level.
7. **Absence is not negation.** A CV without a skills section produces no skill proposals and
   says nothing about the candidate's skills (see `candidate-brain.md`, section 6).
8. The CV text never appears in logs or error messages (the ingestion code does not log; error
   messages are constant strings). The original file is opened read-only and never modified,
   moved or deleted.

## Flow

1. **Ingest** - `POST /api/candidate/ingestions/cv` with `{"source_path": "documents/<file>.docx"}`.
   The path is relative to the private data directory. The file is read once (size and
   SHA-256), parsed, and the proposals are stored. The same file (same SHA-256) cannot be
   ingested twice (`409`); a modified CV is a new ingestion.
2. **Review** - `GET /api/candidate/proposals?status=pending`. Each proposal shows `data`,
   `source_excerpt` and `uncertainties`.
3. **Decide**
   - `POST /api/candidate/proposals/{id}/accept`
   - `POST /api/candidate/proposals/{id}/reject`

### Accepting

Body (both fields optional):

```json
{ "corrections": { "company": "..." }, "acknowledge_uncertainties": true }
```

- If the proposal lists uncertainties, `acknowledge_uncertainties` must be `true`, otherwise
  `409`. This forces a conscious review.
- `corrections` overrides extracted fields (only fields of the target fact schema; unknown
  field names are rejected). The final values are validated like any manual entry
  (`422` on failure, and nothing is changed). The proposal keeps `data` as extracted and stores
  the values actually used in `reviewed_data`.
- **Duplicates.** If an equivalent fact already exists in the Brain (same normalised natural
  key: skill/language/project/certification name; education institution + degree + start;
  experience company + title + start), no second fact is created: the existing one is reused
  and the Evidence is linked to it (`linked_existing = true`). Within one document, duplicate
  proposals are collapsed at ingestion.
- **Double acceptance.** Only a `pending` proposal can be decided. The transition uses a
  conditional `UPDATE ... WHERE status = 'pending'` inside the transaction, so two concurrent
  requests cannot both succeed. Accepting or rejecting an already decided proposal returns `409`.

### Evidence created on acceptance

One Evidence per ingested document, created lazily at the first acceptance and shared by all
its facts: `source_type = cv`, `source_uri` = relative path, `source_metadata` = SHA-256,
parser version, ingestion id. Each EvidenceLink stores the exact CV passage in its `note`.

## What the parser extracts

Sections are recognised from their title (French/English, accent- and case-insensitive):
education, experience, projects, skills, certifications, languages. Other known titles
(profile, interests...) are ignored. Following a list section (skills, languages,
certifications), a short isolated line with no punctuation is treated as an unknown title and
ends the section, so unknown sections are not misread as skills or languages.

| Kind | Extracted when clearly present | Never inferred |
| --- | --- | --- |
| education | institution, degree, period, "in progress" wording | field of study (only with an explicit label) |
| experience | title, company, period, contract type from explicit words (stage, alternance...) | employment type otherwise, description beyond the entry's own lines |
| project | name, URLs (repository vs other), period, description lines | domain |
| skill | names listed in the skills section, category label | level |
| certification | name, issuer (flagged), date | expiration |
| language | name, level if CEFR (A1-C2) or "native" | any other level wording (flagged) |

**Dates keep the precision of the CV.** A date is stored as an ISO 8601 reduced-precision
string, and the API reports its precision:

| CV says | Stored | `*_precision` |
| --- | --- | --- |
| `2024` | `2024` | `year` |
| `juin 2024`, `06/2024` | `2024-06` | `month` |
| `15/06/2024` | `2024-06-15` | `day` |
| nothing usable | `null` | `null` |

A year is **never** turned into `2024-01-01`, and a month is never given a day, even with an
uncertainty flag. Facts expose `start_date_precision` / `end_date_precision` (certifications:
`issue_date_precision` / `expiration_date_precision`). A numeric date that could be day/month
or month/day (`03/04/2024`) is read as day/month/year and flagged. A single date is not
assigned to start or end: it stays visible in the proposal's uncertainties (with its precision) and
the human supplies `start_date` or `end_date` when accepting. An impossible date is dropped and flagged, never repaired.
"Present" leaves the end date empty. When end and start are compared (end must not be before
start), only the precision they share is used: `2024` and `2024-06` overlap, so neither is
before the other.

Storage: the date columns are `VARCHAR(10)` holding `YYYY`, `YYYY-MM` or `YYYY-MM-DD`, validated
at the API, at the parser and at the database-type level (migration `0003`).

**Missing required fields** (e.g. company) stay `null` and are reported in `uncertainties`;
the human supplies them through `corrections` when accepting.

The parser is heuristic and depends on the layout of the CV. It proposes; it never decides.
Expect some entries to need correction or rejection, especially with unusual layouts.

## Security and privacy

- `source_path` must be relative, without `..`, not a URL; it is resolved and must stay inside
  `data/private/` (symbolic links and Windows junctions pointing outside are refused).
- Only `.docx`, at most 5 MB. The archive is checked (entry count, uncompressed size), and DTDs
  and XML entities are refused. Invalid, corrupt or empty files return `422` with a generic
  message.
- The database stores, for each ingestion, the relative path, SHA-256, size and counters, and
  for each proposal the extracted fields and excerpt. It does not store the whole document text.
  These rows contain personal data: the database is private, like `data/private/`.
- `data/private/` is git-ignored, so the CV and anything derived from it there cannot be
  committed. Tests use only synthetic `.docx` files generated in temporary directories.

## Reviewing proposals locally (without exposing them)

`scripts/preview_cv_proposals.py` lets the human examine what was extracted, **read-only**:

```powershell
python scripts/preview_cv_proposals.py documents/<cv>.docx    # parse in memory, no database
python scripts/preview_cv_proposals.py --ingestion-id 1        # proposals already stored
```

- It writes a self-contained HTML report to `data/private/reviews/` (git-ignored) with, for
  each proposal: its type, the structured data (dates with their precision), the uncertainties,
  the exact source excerpt and the status. Open it in a browser.
- The terminal shows **only counters and the report location**: no CV content, no file name, no
  absolute path. Errors are generic. Nothing is logged.
- Preview mode needs no database and stores nothing. Stored mode only reads. Neither ever
  accepts, rejects, or creates a fact, an evidence or a link.
- The report has no script, no external resource, a restrictive Content-Security-Policy, and
  every value is HTML-escaped. It contains personal data and must stay in `data/private/`.

## Not in this version

PDF, GitHub or LinkedIn ingestion, LLM-assisted extraction, sending applications, editing or
undoing a decision, independent verification of evidence.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/candidate/ingestions/cv` | ingest a CV, create pending proposals |
| GET | `/api/candidate/ingestions` | list ingestions |
| GET | `/api/candidate/ingestions/{id}` | one ingestion with its proposals |
| GET | `/api/candidate/proposals` | list proposals (`status`, `kind`, `ingestion_id` filters) |
| GET | `/api/candidate/proposals/{id}` | one proposal |
| POST | `/api/candidate/proposals/{id}/accept` | human validation |
| POST | `/api/candidate/proposals/{id}/reject` | human rejection |

Errors: `404` (no candidate / file / id), `409` (already ingested, already decided,
uncertainties not acknowledged), `422` (invalid path, file or values).
