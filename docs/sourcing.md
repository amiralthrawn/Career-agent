# Sourcing: discovering companies and offers (step 3c)

Sourcing turns what a **data provider** found into **Targets**, through the services that already
exist. It creates the same `Target` model for both flows and reuses the 3a qualification:

- `offers` mode: **published offers** -> Company + Opportunity + Target (with an offer);
- `companies` mode: **companies for spontaneous applications** -> Company + spontaneous Target
  (`opportunity_id IS NULL`). No Opportunity is created and nothing says the company is hiring.

**One real provider is connected: Perplexity, as a `WebSearchProvider` (`web` id `"perplexity"`).**
It is off by default and shares the exact same capability gate as company/contact research
(`RESEARCH_ENABLED`, `PERPLEXITY_PRESET`, the `perplexity_api_key` secret - see
`app.services.sourcing.default_web_search_provider`): nothing new to configure. No `OfferSource`
exists yet (a structured job-board API, e.g. France Travail, is a separate, future integration -
see "A real `OfferSource`" below). No OpenRouter, no LLM, no scraping, no GitHub, no Gmail is
involved here. Out of scope: step 4 (writing, sending), scoring or ranking targets.

```text
SearchProfile ─> SearchQuery ─> provider ─┬─ WebSearchProvider ─> SearchHit ─> HitExtractor ─┐
 (active criteria)                        └─ OfferSource ────────> SourcedItem ──────────────┤
                                                                                              v
   SearchRun + SearchRunItem <── SourcingService ── TargetService.create_target (dedup, provenance)
                                        └────────── QualificationService.qualify (3a + 3b)
```

## Two kinds of provider (two kinds of evidence)

| Port | Returns | Meaning |
| --- | --- | --- |
| `WebSearchProvider.search(SearchQuery)` | `WebSearchResult` of raw `SearchHit` (title, URL, snippet, provider id, optional dates, optional structured attributes) | a **lead**: `HitExtractor` decides deterministically whether it identifies a company / an offer |
| `OfferSource.fetch(SearchQuery)` | `OfferFetchResult` of `SourcedItem` | already normalised offers from a structured source (official API, job board feed) |

A provider **never** creates a Company, Opportunity, Target or qualification, and never touches the
database. It receives a plain `SearchQuery` (the profile's search terms by dimension, the requested
mode, the limit, the single contract the profile asks for): no candidate data.

**Failure is explicit.** A provider raises `ProviderError(code)` (only the *code* is kept, never the
message) or returns `completed=False`. It is never turned into an empty, successful search.

## HitExtractor (deterministic, no AI)

`SearchHit -> Extraction(items, rejections)`. Nothing is inferred. A company or offer exists ONLY
if the hit's structured `attributes` state it: `company_name` (REQUIRED, both modes),
`company_website`, `company_careers_url`, `company_location`, `company_country`, `company_sector`,
and (offers mode) `offer_title` (REQUIRED), `offer_location`, `offer_contract` / `offer_remote`
(exact enum values), `offer_posted`, `offer_external_id`. `HitExtractor` **never** parses a hit's
`title` or `url` to guess an identity - not even an unambiguous-looking `<offer> at <company>`
pattern: only a provider that explicitly states `company_name`/`offer_title` as a structured
attribute is trusted (a job board API's own fields, or an adapter that explicitly says so - see
Perplexity below). A hit with no stated `company_name` is rejected in BOTH modes, symmetrically.

Never derived: the company website from the hit URL (a job board is not the company), the offer
description from the snippet, contract / location / remote mode from the title or snippet, the
publication date from `published`, or "the company is hiring". A hit that does not identify a
company (or, in `offers` mode, an offer) is **rejected with a reason code**:
`missing_company_name`, `missing_offer_title`, `missing_source_url`, `invalid_source_url`,
`invalid_field` (with the field *names*, never the values), `invalid_provenance`, `offer_required`.
In `companies` mode offer attributes are ignored: the result only says the company exists, and it
becomes a spontaneous Target (`opportunity_id IS NULL`) - never a claim that it is hiring.

The provenance of an extracted item is the page the hit points to (`public_page`, URL, provider id).

## Perplexity as a `WebSearchProvider` (`app/integrations/sourcing/perplexity.py`)

A raw web search result (title/URL/snippet) never states a company's or an offer's identity by
itself, so this adapter never invents `attributes` from formatting. It DOES ask the model, as part
of its own answer, to explicitly point out - only for a result it can specifically justify from
that one page - the literal company name and/or offer title, using a small, line-based convention
parsed deterministically and defensively:

```
COMPANY: <result URL> => <company name exactly as written on that page>
OFFER: <result URL> => <offer title exactly as written on that page>
```

A line that does not match this exact shape, or whose URL does not match one of that call's own
search results, is silently dropped - never an error, never a guess by the adapter itself. This is
Career-agent trusting an EXPLICIT provider statement (the same epistemic status as a job board's
own structured field), never an inference `HitExtractor` or this adapter performs on its own.

**Observed in practice** (`scripts/manual/perplexity_sourcing_smoke_test.py`, run manually against
the real API): about 1 in 5 generic search results carries a stated identity the model is willing
to commit to - aggregator/listing pages (a job board's search results page, a LinkedIn jobs list)
correctly get none, since they are not about one specific company; a single offer's own detail page
or a company's own careers page usually does. This adapter is intentionally conservative, not a
high-recall scraper: it is most useful in `companies` mode (discovery for spontaneous applications)
and on individual offer pages, not on search-result aggregators.

## A real `OfferSource` (not built)

A structured job-board API (e.g. France Travail, Adzuna) would implement `OfferSource.fetch` and
return already-normalised `SourcedItem`s with `company_name`/`offer_title` as real, source-stated
fields - no identity ambiguity at all, unlike a general web search. This is a separate, future
integration; nothing in the domain would need to change to add one.

## SourcingService

Order of operations, each before the next:

1. validate the request, load the profile (active by default), build the query, resolve the
   provider. **Nothing is called before all of this succeeds** (`404` no profile, `422` nothing
   searchable / unknown provider / an offer source in companies mode / bad limits);
2. record a `running` `SearchRun` and **commit** it (a crash leaves a trace);
3. call the provider; `ProviderError` -> a `failed` run;
4. extract and validate provenance (only `official_api` / `public_page` with a locator; a `manual`,
   `import_file` or unlocatable source is rejected as `invalid_provenance`);
5. ingest each item through `TargetService.create_target` (**the same** matching rules as the API
   and the CSV import: existing records are matched, never duplicated, modified or enriched) and
   qualify it with `QualificationService.qualify` (3a criteria, 3b matches when requirements exist);
6. record one `SearchRunItem` per result, finish the run, audit.

Each item is atomic (a savepoint): a rejected or failing item never cancels the items already
ingested. The run is committed once, with its audit event. An **unexpected** exception rolls back
everything the run ingested, marks the run `failed` (`unexpected_error`) and is **re-raised**.
Sourcing never extracts requirements by itself (3b extraction stays an explicit call), never
scores or ranks, never writes or sends anything.

Target rules stay those of step 2: a spontaneous target and an offer target of one company are
distinct; two offers give two targets; one target per offer; one spontaneous target per company.
The Company matching is unchanged too (same name **and** same location, or same domain / SIREN): a
result with a different location does not merge into an existing company.
A spontaneous target carries the contract the profile asks for **only if it asks for exactly one**;
an offer target takes the offer's own contract. Criteria that need an offer stay `unknown` for a
spontaneous target (3a rule): `needs_information`, never excluded, never invented. A skill gap
never excludes a target; the stale rules of qualification and of the brief are unchanged.

## `SearchRun` and `SearchRunItem`

`SearchRun` is an audit record, not an event bus: profile, `mode`, `provider`, `provider_kind`,
`query` (search terms by dimension), `status`, `started_at` / `finished_at`, raw counters
(`results_raw`, `targets_created`, `targets_existing`, `companies_created`, `opportunities_created`,
`qualifications_created`, `items_rejected`, `item_errors`), `errors` (codes only) and
`sources_consulted` (short labels declared by the provider).

| Status | Meaning |
| --- | --- |
| `running` | recorded before the provider is called; the database ties it to `finished_at IS NULL` |
| `completed` | the provider completed and no valid item failed (rejections are counted separately: they are data-quality outcomes) |
| `completed_with_errors` | part is usable: `provider_incomplete`, `provider_exceeded_limit` (extra results ignored), or `item_errors` |
| `failed` | `provider_error` (with its code), an incomplete AND empty result, or `unexpected_error`: nothing concluded |

`SearchRunItem` says what became of each result: `target_created` / `target_existing` (with
`target_id` and `qualification_id`), `rejected` (a reason code, field names, the public URL for
review) or `error`. **Never stored**: provider payloads, headers, credentials, unknown attributes;
only a bounded excerpt (500 characters) of the relevant source text. The audit event
(`sourcing.run`) holds counters only.

## `Company.offers_research`

`not_started` / `found` / `not_found`, like `contact_research`, plus `offers_research_at`.
`found` = an offer was recorded. `not_found` only means "a completed search **made for this
company** on named sources showed no offer": it needs a targeted search and the sources consulted,
and is **never** set by a failed, incomplete or unsupported search. A discovered company (spontaneous
sourcing) does not change it. The profile-wide runs of `SourcingService` are not company-targeted
searches, so they only ever record `found`; `record_offers_research` implements the full rule for a
future per-company search.

## API (local API token required)

| Route | Purpose |
| --- | --- |
| `POST /api/search-runs` | `{mode, provider, profile_id?, max_results (1-50, default 20)}`: run synchronously, returns the run and its items (`201`; a provider failure is a run with `status: failed`, not an error response) |
| `GET /api/search-runs` | recent runs (`profile_id`, `limit`, `offset`) |
| `GET /api/search-runs/{id}` | one run with its counters, error codes and items |

The providers come from the injectable `get_providers` dependency (empty by default). All the
parameters are validated before any provider call.

## Campaigns (`app/services/campaign.py`, `app/models/campaign.py`)

A `Campaign` chains as many `SourcingService.run()` rounds as needed toward a daily target,
inside ONE bounded, human-started process (`career-agent sourcing campaign start`) - the one
deliberate, explicitly-authorized exception to this project's "no autonomous agent" rule (see
the module's own docstring). It is never a background daemon or scheduler: it runs to completion
in the foreground process a human starts, and stops itself at the FIRST of several safety limits
(`max_calls`, `max_duration_minutes`, `max_consecutive_empty`) or a provider failure - or earlier,
once the daily target is reached. Each round also runs the existing requirements/qualification/
contact-research/draft-preparation services (best-effort, per new target) - never their own
second engine, and NEVER an automatic package approval or send: those stay separate, human-
triggered actions (`POST /api/applications/{id}/approve`, `.../send`). See docs/operations.md.

## Limits (deliberate)

- No provider exists: adding one means implementing a port and registering it; the domain,
  the guarantees and the tests do not change.
- The excerpt of a result is kept on the run item only; it is not the offer text and feeds no
  requirement extraction.
- Runs are synchronous and bounded (50 results); there is no queue, scheduler or broker.
- A run is one transaction: an unexpected failure leaves no partial ingestion, only a failed run.
