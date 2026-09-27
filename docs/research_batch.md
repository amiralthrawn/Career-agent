# Requalification and batch research (step 7)

**Perplexity reduces uncertainty; Career-agent makes the qualification decision.** For 500
opportunities, Career-agent does not ask Perplexity to qualify 500 opportunities: it qualifies
them itself first (step 3a, unchanged), works out precisely which unknowns could still change the
outcome, asks Perplexity only for those - grouped by company, budget-limited - and then
re-qualifies with the SAME, unchanged deterministic rules.

```text
Sourcing
   |
Initial Qualification (step 3a, unchanged)
   |
Information Needs (why is this "needs_information"? which of it could change?)
   |
Research Planner (grouped by company, one query per company)
   |
Perplexity (ResearchProvider, step 6, unchanged)
   |
External Observations (sourced, provenance kept)
   |
Enrichment (accepted as free text, never a rewrite of Company's own fields)
   |
Requalification (the SAME qualify() call, now with more free text to search)
   |
PersonalizationBrief (step 3b, unchanged) -> OpenRouter (step 4/5, unchanged)
```

## Why this scales: only a few companies, never all opportunities

- A criterion is only worth researching if resolving it could actually change the status. Per
  `decide_status` (step 3a, unchanged), only a `required` criterion's outcome ever flips
  `needs_information` to `candidate`/`excluded`; a `preferred`/`flexible` gap is real and reported,
  but never justifies a mandatory search.
- `unknown ≠ false`, unchanged: only `UNKNOWN` is a genuine data gap. `NOT_MATCHED` means the data
  WAS available and simply did not match (it already answered the question, without excluding):
  there is nothing left for research to add.
- Only dimensions the deterministic matcher already searches as **free text** are researchable at
  all: `sector`, a *company's own* `location`, and `keyword`/activity. A closed vocabulary
  (`country`, `contract_type`, `remote_mode`) needs one clean, exact value, which this step
  deliberately never extracts from prose (see "Why country/contract_type/remote_mode are not
  researched" below). A declared hard `constraint` is not a company-data question; an offer-level
  gap (`role`, the offer's own `location`) is not something company research answers either.
- Needs for the **same company** are grouped into **one** research query, whatever the number of
  targets or criteria involved: many job postings often share one employer, and several unknown
  dimensions of one company are answered by the same research call.
- A hard **budget** (`max_research_calls`) bounds the batch regardless of how many companies would
  otherwise qualify for research; a company with no budget left is recorded as `skipped_budget`,
  never silently dropped.

## Information needs (`app/services/research_planning.py`)

`InformationNeed(target_id, criterion_id, dimension, reason, priority)`: adapted to the existing
architecture rather than invented from scratch - it is built directly from a qualification's own,
already-stored `CriterionResult` rows (dimension, level, outcome) and the target's `has_offer`
flag; nothing new is persisted for it.

```python
information_needs(target, qualification) -> list[InformationNeed]
```

For each stored result: skip anything not `UNKNOWN` (a `NOT_MATCHED` or `SATISFIED` criterion
needs no research); skip a dimension research cannot answer (see the table below); otherwise
record a need, `priority = BLOCKING` for a `required` criterion (it gates the status) or
`INFORMATIONAL` for `preferred`/`flexible` (real, reported, never mandatory).

| Dimension | Researchable? | Why |
| --- | --- | --- |
| `sector` | yes | free-text search against `Company.sector` |
| `location` | only when there is **no offer** | the offer's own location is not a company fact |
| `keyword` | yes | free-text search against offer text + sector; useful when that text is missing |
| `country` | no | closed vocabulary (ISO code): needs one clean value, never extracted from prose |
| `contract_type`, `remote_mode` | no | offer-specific, not a general company fact |
| `role` | no | the offer's own title; company research cannot state a job posting that does not exist |
| `constraint` | no | a declared hard candidate-side constraint, not a company-data question |
| `company` | no | never `UNKNOWN` in the first place (a name/domain match either hits or doesn't) |

## Research planning (`ResearchPlan`, `plan_for_company`)

One `ResearchPlan` per **company**, covering every distinct researchable dimension across every
target of that company with a `BLOCKING` need. The query never carries the candidate's criterion
**values** (never "is this company in Finance?"), only the **dimension** being asked about (a
fixed label: "primary business activity and sector", "company location / headquarters",
"technology, products and relevant keywords") - Perplexity is asked for facts, never for a
verdict, and never learns what the candidate is looking for.

## Perplexity, reused unchanged (step 6)

`ResearchProvider`, `ResearchQuery`, `ResearchResult`, `Observation`, `ProviderContract`: all
reused exactly as step 6 left them; the Perplexity adapter itself was **not modified**. The
orchestration is:

```text
QualificationService -> ResearchPlanner -> ResearchQuery -> ResearchProvider -> ResearchResult
```

and never `Perplexity -> QualificationService`: the provider is only ever called from
`ResearchBatchService`, with a query `ResearchPlanner` built; it never sees a `Qualification`,
a `SearchCriterion`, or any internal qualification rule.

## Enrichment: `CompanyResearchFact` (`app/models/company_research.py`)

An existing company is never modified by a later source (unchanged rule, step 2). A returned
`Observation` is therefore never written into `Company.sector` or any other of its own fields; it
is stored, verbatim and immutable, in a new, small table:

```text
CompanyResearchFact: company_id, claim, source_url, source_title, excerpt, published,
                     retrieved_at, created_at
```

**This is a deliberately minimal gap-fill, not a new evidence architecture.** The existing
`Evidence`/`EvidenceLink` model is candidate-owned (it backs `Skill`/`Project`/`Experience`, never
a `Company`); reusing it for a company-level fact would be a real mismatch, not a natural
extension. A dedicated table, mirroring the same append-only provenance convention as every other
source record in this project, is the smaller change.

**Nothing here extracts a clean value from prose** (no "sector = Finance"). Instead,
`TargetView.company_research_text` (a new, optional field on the existing, unchanged
`criteria_evaluation` view) concatenates every accepted claim/excerpt for a company, and the
**existing, unmodified** deterministic matcher (`_open_text`, `_keyword`) simply has more free
text to search for the term it already looks for - exactly as it already searches
`Company.sector`. The rules that decide `satisfied` / `not_matched` / `unknown` are untouched;
only what they can read grew by one optional field. An `Observation` without a source is never
generated (`app.integrations.research.perplexity` already drops those, step 6, unchanged) and an
accepted fact keeps its `source_url`/`source_title`/`excerpt` forever - never presented as if it
were `Company.sector` itself.

## Requalification: no new mechanism needed

Once `TargetView` can read `company_research_text`, requalification is not a new versioning
system: it is **the existing `QualificationService.qualify` call, made again**. Its fingerprint
already hashes the whole view (`asdict(view)`), so a target whose company gained a
`CompanyResearchFact` gets a *different* fingerprint on the next `qualify()` call, and a genuinely
*new*, immutable `Qualification` row is created - the old one is untouched and stays queryable, as
the append-only convention (step 3a) already guarantees. A target whose company gained nothing
gets the *same* fingerprint, and `qualify()` reuses the existing qualification (idempotent, as
before). No new "requalification" code path exists beyond calling `qualify()` a second time.

## Batch orchestration (`app/services/research_batch.py`)

```python
ResearchBatchService(session, provider).run(
    profile_id=None, *, target_ids=None, max_targets=None,
    max_research_calls=0, actor="api",
) -> BatchReport
```

1. Qualify every selected target (the existing, unchanged `qualify()`); collect each one's
   `InformationNeed`s when its status is `needs_information`.
2. Group `BLOCKING` needs by company; build one `ResearchPlan` per company with at least one.
3. Sort plans by how many *targets* they would affect (most first, company id as a tie-break) -
   an operational scheduling order, not a candidate-facing score.
4. Research each plan while `max_research_calls` (default **0**: nothing is researched unless
   explicitly budgeted, the same "capabilities default off" convention as `LLM_ENABLED`/
   `RESEARCH_ENABLED`) and a configured provider both allow it; a `ResearchError` on one company
   is caught and recorded (`research_failed`) **without** stopping the others.
5. Requalify only the targets whose company actually gained a fact; every other target keeps its
   initial result.
6. Return a `BatchReport` (never persisted - mirrors `QualificationService.run`'s existing
   `RunReport`) and record one audit event (`research_batch.run`, counters only).

| `ResearchOutcome` | Meaning |
| --- | --- |
| `not_needed` | no `BLOCKING` need for this target (already `candidate`/`excluded`, or only `preferred` gaps) |
| `researched` | its company's plan succeeded; the target was requalified |
| `research_failed` | its company's plan was attempted and failed - **explicitly recorded**, never silently turned into "unknown became false" |
| `skipped_budget` | a plan existed but no provider was configured, or the budget ran out first |

No score, no rank, no `match_score`/`confidence`/`AI_score` anywhere in `BatchItem`/`BatchReport`:
statuses stay exactly `excluded` / `needs_information` / `candidate`, with the same structured
reasons as step 3a.

## API

None added for this step. `ResearchBatchService` is a plain service class, called directly (as
`CompanyResearchService` already is, step 6); nothing in the brief needed an HTTP route to be
testable, and no production route exists yet for it.

## Limits (deliberate)

- No API route (see above).
- `country`/`contract_type`/`remote_mode` stay unresearched: extracting one clean value from free
  text is exactly the kind of invented precision this project has always refused (see the
  `never-fabricate-precision-from-extracted-data` principle applied throughout the Candidate
  Brain); only free-text dimensions benefit from research in this step.
- A failed research attempt is not remembered as "already tried": the next batch run plans it
  again. Backing off a company that keeps failing is a reasonable next step, not built here.
- Grouping is per company only; it does not span companies (two unrelated companies are never
  merged into one query), and it does not yet consider a shared *domain* across differently-named
  companies (a rare de-duplication edge case already handled by the step 2 company-matching rules
  before a target is even created).
