# Targets: companies, offers, contacts (step 2)

**Target = the unit of the application pipeline.** A target is a company the candidate may apply
to, with an *optional* job offer:

- **with an offer** -> application to an existing offer;
- **without an offer** -> spontaneous application.

There is no parallel model. A spontaneous target is a target whose `opportunity_id` is `NULL`,
and everything downstream (analysis, contact, personalisation, validation, sending, tracking)
depends only on `target_id`.

```text
Candidate (search criteria = CandidatePreference / CandidateConstraint, already in the Brain)
   -> sourcing -> TARGET -> Company (required)
                    |         '- Contact -> ContactChannel (e-mail, phone, ...)
                    '- Opportunity (optional)
```

Not in this step: scraping, network calls, LLM, scoring, sending. `relevance_note` is a short
explanatory text written by the user (or provided by the import); nothing computes it.

## Schema

`Company`, `Opportunity`, `Contact`, `ContactChannel` and `Source` are global reference data (a
third party is stored once). Only `Target` belongs to the candidate.

| Table | Main fields |
| --- | --- |
| `sources` | `kind` (manual / import_file / official_api / public_page), `label`, `url`, `reference`, `retrieved_at` |
| `companies` | `name`, `name_key`, `domain` (unique), `website_url`, `careers_url`, `siren` (unique), `location`, `country_code`, `sector`, `contact_research`, `contact_research_at`, `source_id` |
| `opportunities` | `company_id`, `title`, `url`, `external_id`, `contract_type`, `location`, `remote_mode`, `posted_on` (partial date), `description_text`, `status` (open / closed / **unknown**), `source_id` |
| `targets` | `candidate_id`, `company_id`, `opportunity_id` (nullable), `contract_type`, `status` (new / shortlisted / dismissed), `dismissed_reason`, `relevance_note`, `source_id` |
| `contacts` | `company_id`, `full_name` (nullable), `is_generic`, `role_title`, `role_category`, `status` (found / uncertain), `verified`, `do_not_contact`, `source_id` |
| `contact_channels` | `contact_id`, `kind` (email / phone / linkedin_url / contact_form_url), `value`, `status`, `verified`, `source_id` |
| `target_contacts` | `target_id`, `contact_id`, `company_id`, `is_primary` |

Integrity enforced **by the database**: a composite foreign key `(opportunity_id, company_id)` makes
it impossible to link an offer of another company; partial unique indexes give "one spontaneous
target per company" and "one target per offer" (a company can have both); `target_contacts` only
links contacts of the target's company and allows one primary contact per target; a contact
needs a name or `is_generic`; `sources` checks that a file import has a reference and that an API
or public page has a url or a reference; a company with targets cannot be deleted.

`mode` (`offer` / `spontaneous`) is **derived** from `opportunity_id`, never stored, so it cannot
contradict it. Vocabulary: ALTERNANCE = `contract_type: apprenticeship`, JOB = `full_time` /
`fixed_term`, SPONTANEOUS = `mode`. ("Spontaneous alternance" is a valid combination.)

## Provenance and states of information

- Every company, offer, target, contact and channel points to a `Source` (one primary source per
  record). An AI is never a source: the page, API or file it read is.
- **No source, no address.** A channel needs a real origin: a public page or official API (with
  url/reference), or a manual entry whose `reference` says where it comes from. A file row alone
  is not enough, and neither is an unexplained manual entry.
- **Found / uncertain / absent.** `found` = written in the cited source; `uncertain` = found but
  doubtful (an e-mail on a domain other than the company's is stored as `uncertain`);
  `verified` = confirmed by a human, separate. **Absent = no row** (`email: null`), never a
  status and never a guess.
- **Nothing is guessed.** No `first.last@domain`, no SMTP probing: only values that were handed to
  the service are stored (a test checks that no address exists that the input did not contain).
- `Company.contact_research`: `not_started` / `found` / `not_found`. `not_found` only means "the
  sources consulted gave no contact", never "the company has none"; it needs the list of sources
  consulted, and it never downgrades a company that already has contacts.
- `do_not_contact` is never silently overridden (a re-import warns instead). The `SendGuard` will
  honour it when the Gmail provider is wired.

## De-duplication (conservative)

An existing record is **matched, never modified**: differences are reported by field name
(`differs` / `not_stored`), so the provenance of a value is never mixed. Wrong merges are worse
than visible duplicates.

| Record | Match, in order |
| --- | --- |
| Company | website domain -> SIREN (valid checksum) -> same normalised name **and** same city (both given and equal, or both absent), unless the domains contradict |
| Offer | URL -> external id -> title + location (only when the offer has neither URL nor id) |
| Target | one per (candidate, offer), one spontaneous per (candidate, company) |
| Contact | same normalised name in the company, or an address it already has |
| Channel | same (contact, kind, value); an address owned by another contact is not reassigned |

Normalisation (`app/core/normalize.py`): names ignore case, accents, punctuation and legal forms
(`SAS`, `SARL`, `Inc`, ...); domains drop scheme, `www.`, port and path; URLs drop fragment and
trailing slash; e-mails are lower-cased and never completed.

## ContactFinder (reusable capability)

`Company -> contacts with their sources`. `app/integrations/contacts/ports.py` defines the port
(`ContactFinder.find(CompanyInfo) -> ContactSearchResult`). An adapter never touches the database
and cannot omit a source (`SourceSpec` is mandatory in `FoundContact` / `FoundChannel`).
`ContactService.record_search_result` / `research` is the single sink: it stores contacts and
channels with their sources, applies the rules above, and updates `contact_research`. **No adapter
exists yet**; future ones (a public careers-page reader, an official API...) plug into the same
service and get the same guarantees.

## CSV import

Place a UTF-8 CSV in `data/private/imports/` (git-ignored). Comma or semicolon separator is
detected; a BOM is tolerated. Limits: 1 MB, 2000 rows. **One row = one target**; several contacts
for one company = several rows. Unknown columns are ignored and listed in the report.

| Group | Columns |
| --- | --- |
| Company | `company_name` (**required**), `company_website`, `company_city`, `company_country` (2 letters), `company_sector`, `company_siren`, `careers_url` |
| Target | `contract_type` (alternance / apprentissage / stage / job / cdi / cdd / freelance / ...), `relevance_note` |
| Offer | `offer_title`, `offer_url`, `offer_location`, `offer_remote` (remote / hybride / sur site...), `offer_contract`, `offer_posted` (`YYYY`, `YYYY-MM`, `YYYY-MM-DD`), `offer_external_id`, `offer_description` |
| Contact | `contact_name`, `contact_role`, `contact_role_category`, `contact_email`, `contact_generic` (true/false/oui/non), `contact_status` (found/uncertain), `contact_source_url` |
| Provenance | `source_label`, `source_url` |

Rules:

- **No `offer_*` value -> spontaneous target**; any `offer_*` value -> the offer needs a title.
- `contact_email` is only imported with a source URL (`contact_source_url`, else the row's
  `source_url`). Without one the address is refused (warning) and the rest of the row is still
  imported.
- Each row gets one `Source`: `public_page` if `source_url` is given, otherwise `import_file` with
  the reference `<file hash>:row <n>`.
- **Preview then apply.** `POST /api/imports/targets/preview` runs *exactly the same code* as
  the apply inside a transaction that is rolled back, so it reports what would really happen
  (duplicates inside the file included) and writes nothing (no source, no audit event). `apply` is
  idempotent: re-applying the same file creates nothing. `expected_sha256` refuses a file that
  changed since the preview.
- **Partial import.** Valid rows are applied; each row is atomic (a savepoint); invalid rows are
  listed with field-level codes. The report never contains cell values.
- Cells are stored as text: a leading `=`, `+`, `-` or `@` is never interpreted. A future
  spreadsheet export must neutralise them.
- The audit event `import.applied` (counters only) is committed in the same transaction as the
  imported rows: no audit, no import.

## API (all under the local API token)

| Route | Purpose |
| --- | --- |
| `POST /api/imports/targets/preview`, `POST /api/imports/targets` | preview / apply an import |
| `POST /api/targets` | create a target (company + optional offer + contacts); idempotent (`200`, `created: false` when it exists) |
| `GET /api/targets` | filters `mode`, `contract_type`, `status`, `company_id`, `has_email`, `limit`, `offset` |
| `GET /api/targets/{id}` | full view: company, offer, contacts, channels, each with its source and state |
| `PATCH /api/targets/{id}` | `status`, `dismissed_reason`, `relevance_note` |
| `POST /api/targets/{id}/contacts` | attach an existing contact or create one |
| `GET /api/companies`, `GET /api/companies/{id}` | consult (with `contact_research`) |
| `GET/POST /api/companies/{id}/contacts` | contacts of a company |
| `PATCH /api/contacts/{id}` | `status`, `verified`, `do_not_contact` |

Both flows return the same `TargetRead` shape (`opportunity` is `null` for a spontaneous
target): this is the contract of the rest of the pipeline.

## Limits (deliberate, for the MVP)

- One primary source per record: a later source that confirms a value is reported, not linked.
- Existing records are never enriched or corrected by an import.
- No adapter finds contacts yet; contacts come from the user or a CSV.
- Personal data of third parties (contacts) is stored locally with its source; a retention or
  purge policy is still to be defined.
