# Contact intelligence (step 8)

**Perplexity finds a lead; a human decides it is a contact.** Career-agent never invents a name,
a title or an address, and never sends anything: this step only adds a reviewable, sourced
proposal in front of the existing `Contact`/`ContactChannel` models and the existing
`ContactService.record_contact` sink - it does not build a second contact-creation path.

```text
Qualification -> Target (candidate or needs_information)
   |
Contact Research (per target, per requested role category: recruiter, HR, manager, tech...)
   |
Perplexity (ResearchProvider, step 6, unchanged) -> Observations, sourced
   |
ContactResearchObservation (PENDING - a proposal, not a fact)
   |
Human review: accept (-> ContactService.record_contact, existing, unchanged) / reject
   |
Contact + ContactChannel (company-owned) <-> TargetContact (per-target relevance)
```

## Why a new proposal table, not a new contact model

`Contact`/`ContactChannel` (step 2) already have exactly the vocabulary this step needs:
`RoleCategory` (hr/recruiter/tech/manager/founder/other/unknown), `InfoStatus`
(found/uncertain - "reported"), a separate `verified: bool` flag, and a mandatory `source_id`.
Nothing there needed to change. What was missing was a place to hold a **candidate, unaccepted**
finding - `IngestionProposal` (CV ingestion, step 2) is the exact existing precedent: a
PENDING/ACCEPTED/REJECTED record, mutable in place (no immutability trigger), that only becomes a
real fact through an explicit human `accept`. `ContactResearchObservation`
(`app/models/contact_research.py`) mirrors it field-for-field rather than inventing a parallel
review architecture, and reuses the same `ProposalStatus` enum directly.

It deliberately does **not** mirror `CompanyResearchFact` (step 7): a company fact is
non-personal and safe to auto-accept the moment it is sourced; a contact is personal data about a
named individual, so it stays a proposal - never auto-promoted - until a human confirms it.

```text
ContactResearchObservation: target_id, company_id, requested_role_category, status,
                             claim, source_url, source_title, excerpt, published, retrieved_at,
                             fingerprint, reviewed_data, review_note, decided_at,
                             resulting_contact_id
```

Scoped to `(target_id, company_id)`: a finding is always tied to the candidature that prompted the
search, never mixed into another target's contact list, even when several targets share a company.

## Searching (`app/services/contact_research.py`)

```python
ContactResearchService(session, provider).search(target_id, role_categories) -> list[ContactResearchObservation]
```

One `ResearchQuery` **per requested category** (never a combined multi-category query): a single
query covering several categories at once would leave no way to know, without guessing, which
category a returned observation actually answers - and nothing here guesses. The query is built
the same way step 6/7 already build one: only the company's own data (`ResearchSubject`) and a
fixed focus-area label per category ("recruiter or talent acquisition contact", "hiring manager or
team lead"...); no candidate data, no criteria, no skill ever reaches it, and the fixed objective
text explicitly instructs the provider never to judge the candidature.

Each **sourced** observation (`source_url` present - the same "no source, no fact" rule as
`app.services.research_planning.accept_observations`) is stored as a new, PENDING
`ContactResearchObservation`, keyed by a `fingerprint` of `(source_url, claim)` **per target**: a
repeated search for the same target never stores the same finding twice.

## Provenance and verification (Part 2)

| State | Meaning |
| --- | --- |
| `reported` (`ContactResearchObservation`, `PENDING`) | Perplexity's own claim, with its exact source URL, title, excerpt and retrieval date - not yet reviewed |
| `verified` (`Contact.verified` / `ContactChannel.verified`, both default `False`) | Never set by acceptance alone. Only an existing, separate, manual `PATCH /contacts/{id}` (`ContactUpdate(verified=True)`) can set it - acceptance is deliberately treated the same way `CVIngestionService.accept` treats a CV fact: "known", never "verified" |
| `unknown` | No row at all. Absence is never a negative claim about the company |

A Perplexity response is never, by itself, sufficient to mark anything verified: accepting an
observation only ever produces `status=found, verified=False` on the resulting `Contact`/
`ContactChannel`, exactly like every other externally-sourced record in this project.

## Professional e-mails (Part 3)

No code path here ever invents an address, derives one from a guessed format, or tests one by
SMTP. An e-mail only ever reaches a `ContactChannel` when:

1. Perplexity's `Observation` carried a `source_url` (enforced centrally, not per-adapter), **and**
2. a human, at acceptance time, explicitly supplies `channel_kind=email` and the exact
   `channel_value` in `ContactObservationAccept` - never read out of `claim`/`excerpt`
   automatically, even when that free text happens to contain something e-mail-shaped (see
   `test_acceptance_never_invents_an_email_from_the_claim_text`).

The channel's provenance is the observation's own source (`SourceKind.PUBLIC_PAGE`, its
`source_url`/`source_title`), and it goes through the **existing, unmodified**
`ContactService._add_channel`, which already flags an off-company-domain address as `uncertain`
and refuses to reassign an address another contact owns.

## Deduplication and homonyms (Part 4)

No new merge logic: acceptance calls the **existing** `ContactService.record_contact`, which
already dedups by normalized name **within one company**, then by a matching e-mail among
channels, and never merges across two different companies (the unique index on
`(company_id, name_key)` already scopes it that way). Two different people who happen to share a
name at two different companies are never merged (see
`test_homonyms_at_different_companies_are_never_merged`); nothing in this step widens or narrows
that existing guarantee. `do_not_contact` is read, warned on, and never silently cleared by a new
acceptance for the same person (unchanged `record_contact` behaviour).

`TargetContact` (step 2, unchanged) links the resulting `Contact` to the target that prompted the
search; a contact already linked to another target of the same company is simply reused, never
duplicated (`is_primary` defaults to `false` and is never overridden by acceptance).

## Human-in-the-loop (Part 5)

```python
ContactResearchService(session, provider).accept(observation_id, ContactObservationAccept(...))
ContactResearchService(session, provider).reject(observation_id, ContactObservationReject(...))
```

Same atomic-decision guarantee as `CVIngestionService`/`IngestionProposal`: a conditional
`UPDATE ... WHERE status = 'pending'` (`claim_observation_pending`) means two concurrent decisions
on the same observation cannot both succeed, and a decided observation can never be decided again
(`409 Conflict`). `GET /api/contact-research/observations` (filterable by `target_id`/`company_id`/
`status`) distinguishes `pending`/`accepted`/`rejected` directly through the reused
`ProposalStatus` enum - no new status vocabulary.

## Batch processing (Part 6, `app/services/contact_research_batch.py`)

```python
ContactResearchBatchService(session, provider).run(
    *, target_ids=None, max_targets=None, role_categories=DEFAULT_ROLE_CATEGORIES,
    max_research_calls=0, actor="api",
) -> ContactBatchReport
```

Generalises step 7's batch principles from "per company" to "per **(company, role category)**":

1. Group selected targets by company.
2. For each `(company, role_category)`, skip it entirely - **no provider call at all** - if the
   company already has a `Contact` of that category (a direct query, `list_contacts`, never a
   possibly-stale cached relationship: a contact accepted moments earlier by a different service
   call is never missed). Recorded as `already_known`.
3. Otherwise it is `needed`, affecting every target of that company still waiting on it.
   Deterministic priority: the `(company, category)` affecting the most targets first.
4. Budget-limited (`max_research_calls`, default **0** - "capabilities default off", same
   convention as `RESEARCH_ENABLED`/`LLM_ENABLED`): one provider call per needed `(company,
   category)`, its result fanned out to **every** waiting target via `store_observations` (so each
   target keeps its own, independently-decidable `ContactResearchObservation` rows, never mixed).
5. A `ResearchError` on one `(company, category)` is caught and recorded (`research_failed`)
   without stopping any other company.
6. One audit event (`contact_research_batch.run`, counters only) and a `ContactBatchReport` -
   never persisted, mirrors `BatchReport` (step 7).

No score, no ranking, no automatic contact: the report only counts outcomes.

## Integration (Part 7)

Contact intelligence never touches `decide_status`, `CriterionResult`, or any qualification rule:
it reads a `Target`/`Company` and writes only its own table plus, on acceptance, the existing
`Contact`/`ContactChannel`/`TargetContact` rows. A `PersonalizationBrief` or generated draft may
later choose to mention a contact, but that remains a separate, later, deliberate choice this step
does not make - exactly the same boundary already drawn around `ResearchResult` in step 6.

## API (Part 8)

All under the existing Bearer-token-protected `/api/*` router (`app/api/contact_research.py`):

| Route | Purpose |
| --- | --- |
| `POST /api/targets/{target_id}/contact-research` | search (one call per requested category) |
| `GET /api/contact-research/observations` | list, filterable by `target_id`/`company_id`/`status` |
| `GET /api/contact-research/observations/{id}` | read one |
| `POST /api/contact-research/observations/{id}/accept` | human acceptance -> `Contact` |
| `POST /api/contact-research/observations/{id}/reject` | human rejection |
| `POST /api/contact-research/batch` | Part 6 batch, across several targets/companies |

No send route exists anywhere in this project yet; none was added here.

## Limits (deliberate)

- No UI beyond the existing API: the interface has no dedicated contact-research screen yet - out
  of scope for this step, as instructed.
- `role_categories` search is explicit per call (or the batch's fixed default set); there is no
  automatic decision about which categories are worth searching for a given target beyond "not
  already known".
- A failed search is not remembered as "already tried": the next batch run retries it, same
  limit already accepted in step 7's `research_batch.md`.
- Grouping is per company only, exactly like step 7 - it does not span two differently-named
  companies that happen to share a domain (an existing, separate de-duplication concern).
