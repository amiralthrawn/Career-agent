# Search profiles and deterministic qualification (step 3a)

A target is **qualified** against a **search profile** made of **criteria**. The result is
decision support (`excluded` / `needs_information` / `candidate`), explainable and
deterministic. **There is no score, no percentage and no weight anywhere.**

Requirements extracted from offers, matching with the Candidate Brain and the personalisation brief
are step 3b: see [requirements.md](requirements.md). Out of scope (steps 3c and 4): web sourcing,
LLMs. Nothing here uses the network.

## Principles

1. **Unknown is not a violation.** Missing data gives `unknown`; only a *known* incompatibility
   can exclude a target.
2. **A miss in free text is not proof.** Wording differs, a suburb is not "not Paris": a free-text
   criterion that finds no match is `not_matched`, which never excludes.
3. **Only a required criterion can exclude.** Preferred and flexible criteria never change the
   status, whatever their outcome.
4. **Excluded targets are kept**, with their reasons. Nothing is deleted or dismissed
   automatically: `Target.status` remains the human's decision.
5. **Immutability.** Criteria keep their content forever; qualifications are append-only.
6. **Reasons are codes and references**, never stored free text.

## Criteria

`SearchProfile` (one active per candidate) holds `SearchCriterion` rows:
`dimension`, `operator` (`any_of` / `none_of`), `values`, `level`, `note`.

| Level | Meaning | Effect |
| --- | --- | --- |
| `required` | must be compatible | a **known** incompatibility -> `excluded`; unresolved -> `needs_information` |
| `preferred` | raises relevance | never excludes; feeds reasons and counters |
| `flexible` | orients the search, may be absent | informative only |

Dimensions (only those that existing data can evaluate):

| Dimension | Compared with | Vocabulary |
| --- | --- | --- |
| `contract_type` | the target's contract, else the offer's | closed (`EmploymentType`) |
| `country` | `Company.country_code` | closed (2-letter codes) |
| `company` | company name (accent/legal-form insensitive) or domain | identity |
| `location` | the offer's location if there is an offer, else the company's | free text |
| `sector` | `Company.sector` | free text |
| `role` | the offer title (needs an offer) | free text |
| `keyword` | offer title + description + company sector | free text |
| `remote_mode` | `Opportunity.remote_mode` (onsite / hybrid / remote; `NULL` = not stated) | closed (`RemoteMode`) |
| `constraint` | nothing: a declared **hard constraint that no data can evaluate yet** | always `unknown` |

`remote_mode` is fully supported even when the data is missing: an offer that does not state how it
is worked, or a spontaneous target (no offer), gives `unknown` (`data_missing` / `no_offer`);
required -> `needs_information`, never `excluded`; preferred or flexible -> informative. A mode that
IS stated and not allowed is a known incompatibility (closed vocabulary).

Not evaluable yet, so not offered as dimensions: salary, company size, experience level (they are
covered by the `constraint` dimension when they come from a hard constraint).

### Outcome of a criterion

| Outcome | Meaning |
| --- | --- |
| `satisfied` | compatible, with evidence |
| `incompatible` | **known** incompatibility: closed vocabulary (contract, country) mismatch, or an excluded value (`none_of`) that is present |
| `not_matched` | free text was available and shows no match: **not** a proof of incompatibility |
| `unknown` | the data needed is missing (`data_missing`), there is no offer (`no_offer`), or the description is not provided |

Each result carries a code (`match`, `excluded_term_absent`, `excluded_value_present`,
`not_in_allowed_set`, `no_match_found`, `data_missing`, `no_offer`, `description_not_provided`,
`not_evaluable`) and
the single compared value (`observed`, short, never the description).

Details worth knowing: an offer without a location is `unknown` (the company's seat is not
necessarily where the job is); a `company` wish list cannot exclude (a miss is `not_matched`), use
`none_of` to block companies; for `none_of` on a keyword, absence only means something when the
whole offer text is available.

### Qualification status

- any required criterion `incompatible` -> **`excluded`**;
- otherwise any required criterion `not_matched` or `unknown` -> **`needs_information`**;
- otherwise -> **`candidate`** (also when there is no required criterion).

Raw counters per level are exposed (`total`, `satisfied`, `incompatible`, `not_matched`,
`unknown`); they are never combined into a single figure.

### Reasons

`required_satisfied`, `excluded_by_required`, `open_question_required`, `preferred_satisfied`,
`flexible_matched`, `no_offer_published`. Each reason stores a **code and a reference to an
existing criterion result** (a real foreign key); the text is rendered at read time from a fixed
template. `no_offer_published` is informative and explicitly **not negative**: no published
offer says nothing about whether the company is hiring.

## Spontaneous targets

They are qualified by the same code. What an offer would provide is `unknown` (`no_offer`), never
a violation; the reason `no_offer_published` is added. A required `role` criterion therefore
gives `needs_information` on a spontaneous target.

## Immutability, idempotence, staleness

- **Criteria** never change: a modification (`replaces`) creates a **new version** and
  deactivates the old one, which keeps `superseded_by_id`. Deleting only deactivates. Enforced by
  the ORM and by database triggers on the content columns.
- **Qualifications** (and their results and reasons) are append-only: UPDATE is rejected by the
  ORM and by database triggers (PostgreSQL and SQLite); a database built by the migration has the
  same protection.
- `inputs_fingerprint` = hash of the criteria, the target/company/offer data actually used, the
  profile and the evaluator version. Same fingerprint as the latest qualification -> nothing is
  recomputed (`200`, `created: false`). Otherwise a new qualification is added (`201`).
- **Stale** is derived, never stored: `GET .../qualification` reports `stale: true` when the
  current inputs no longer match the stored fingerprint. Changing the workflow status or the
  relevance note does not make it stale.

## Seeding a profile from preferences (explicit)

`POST /api/search-profiles/from-preferences` builds a reviewable profile; it never modifies the
declared preferences. Mapping:

| From | To | Level |
| --- | --- | --- |
| `contract_types` | `contract_type` any_of | required (the contract really sought) |
| `target_roles` | `role` | preferred |
| `preferred_locations` | `location` | preferred |
| `target_sectors` | `sector` | preferred |
| `target_companies` | `company` | preferred |
| `target_domains` | `keyword` (a slash separates alternatives: `Data/IA` -> `Data`, `IA`) | flexible |
| `remote_preference` (`remote` / `hybrid` / `onsite`) | `remote_mode` any_of (`no_preference` states no criterion) | preferred |
| constraint `geographic` (`countries`, `excluded_countries`, `locations`, `excluded_locations`) | `country` / `location` | required if `is_hard`, else preferred |
| constraint `contract` (`contract_types`, `excluded_contract_types`) | `contract_type` | required if `is_hard`, else preferred |

Levels of preference-derived criteria can be overridden (`levels`); constraints follow `is_hard`.

### A hard constraint is never silently ignored

A `CandidateConstraint(is_hard=true)` cannot end up inactive. Whatever part of it cannot be
evaluated with the data we hold becomes a **required criterion on the `constraint` dimension**
(`values` = the constraint type, `origin_ref` = `constraint:<id>`, fixed note). That criterion is
always `unknown` (`not_evaluable`), so:

- `required + unknown -> needs_information` for every target (offer or spontaneous);
- it **never excludes by itself**; a known incompatibility elsewhere still excludes;
- the evaluable parts of the same constraint (for example `countries`) are still enforced next
  to it;
- it is also listed in `unmapped` (`hard_constraint_unevaluable` or
  `hard_constraint_partially_held`), for transparency.

It is resolved only by an explicit, versioned human action: re-level it (`replaces`, for example
to `preferred`) or deactivate it. Both leave a readable history and make existing qualifications
stale.

A **soft** constraint (`is_hard=false`) that cannot be evaluated is only listed in `unmapped`
(`unsupported_constraint`, `partially_mapped`, `invalid_value`); it holds nothing. Soft
*preferences* without data (company size, salaries) are listed as `no_evaluable_data`.

## API (all under the local API token)

| Route | Purpose |
| --- | --- |
| `GET/POST /api/search-profiles` | list / create a profile (with optional criteria) |
| `POST /api/search-profiles/from-preferences` | explicit seeding |
| `POST /api/search-profiles/{id}/criteria` | add a criterion, or a new version with `replaces` |
| `DELETE /api/search-profiles/{id}/criteria/{criterion_id}` | deactivate (never delete) |
| `POST /api/targets/{id}/qualify` | qualify (idempotent), optional `profile_id` |
| `GET /api/targets/{id}/qualification` | latest qualification, with `stale` |
| `POST /api/qualifications/run` | qualify every non-dismissed target whose inputs changed; audited (`qualification.run`, counters only) |
| `GET /api/targets?qualification=excluded\|needs_information\|candidate\|none` | filter on the latest qualification for the active profile |

## Limits (deliberate)

- Free-text matching is by whole words after normalisation; there is no gazetteer and no
  synonym table (the 3b taxonomy serves requirements only, see requirements.md).
- `TargetView` also carries an optional `company_research_text` (step 7): accepted, sourced
  external observations about the company, read as MORE free text by the same rules above -
  never a new kind of proof, never itself a verdict. See research_batch.md.
- A profile with no active criterion cannot qualify anything (`422`).
- While a held hard constraint stays required, no target can reach `candidate`: that is the
  intended pressure to decide, not a bug.
- Switching the active profile is done by creating a profile with `is_active: true`.
