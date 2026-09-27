# External AI providers: Provider Context / Contract, and who owns the truth (step 6)

```text
                         CAREER-AGENT
                              |
             +----------------+----------------+
             |                |                |
             v                v                v
        PERPLEXITY       OPENROUTER          GMAIL
       external research   text generation   execution (future)
             |                |                |
             v                v                v
       observations        drafts           actions
       + provenance       + claims        approved only
             |                |                |
             +----------------+----------------+
                              |
                       CAREER-AGENT
              owns data, models, rules, qualifications,
                 decisions, evidence, workflows,
                      human validation
```

**External providers provide capabilities; Career-agent owns business decisions and truth
management.** A provider is never handed an isolated task and never becomes a decision point:
Perplexity researches, OpenRouter writes prose, Gmail will one day transport an approved message -
none of them decides whether a target is good, whether a skill is real, or whether to apply.

## Provider output vs Career-agent truth

**`Perplexity reported X` is never automatically `Career-agent verified X`.** A provider returns
external information with its provenance (a `ResearchResult`, sourced `Observation`s); Career-agent
decides, separately and later, whether and how that information is interpreted and stored. No
provider port in this codebase can express a business decision directly - there is no
`good_target`, `match_score` or `apply` field anywhere in `ResearchResult`, `GenerationResult` or
`ApplicationDraft`, and no method on a provider port writes to the database, changes a `Target`'s
status, touches a `Qualification`, or sends anything.

The same discipline already existed for generation (`docs/drafts.md`): `ApplicationDraft.claims`
are built by the *application* from the `PersonalizationBrief`, never parsed out of the model's
prose. Step 6 extends it explicitly to research: an `Observation` is external and sourced, not
Career-agent evidence, until a human or a later, separate step decides to treat it as such.

## AI Provider Context / Contract

Every external provider receives more than an isolated task: it receives its own position in the
system. `app/integrations/provider_context.py` defines this once, reused by every integration:

```python
@dataclass(frozen=True)
class ProviderContract:
    name: str  # short, stable id, e.g. "perplexity.company_research"
    purpose: str
    responsibilities: tuple[str, ...]
    boundaries: tuple[str, ...]
    upstream_context: tuple[str, ...]  # what Career-agent hands it
    downstream_role: tuple[str, ...]  # what Career-agent does next with what comes back
```

`render_contract(contract)` turns one into a compact, deterministic text block (same contract, same
text, always) - structured, stable and reusable, never a bespoke prompt per call. Two contracts are
declared today:

| Contract | Declared in | Role |
| --- | --- | --- |
| `perplexity.company_research` | `app.integrations.research.perplexity` | external research |
| `generation.application_draft` | `app.services.draft_generation` | draft prose generation |

A call's actual instructions are always TWO layers, kept separate in code even where a provider's
wire format has only one text field to receive them:

- **SYSTEM / PROVIDER CONTEXT**: `render_contract(CONTRACT)` - fixed, versioned, role-wide;
- **TASK CONTEXT**: built fresh per call, from data already validated for that one call.

Perplexity's Agent API happens to have two separate fields for exactly this
(`instructions`/`input`), so the two layers stay genuinely separate on the wire too. OpenRouter's
`GenerationRequest` has a single `system` string; `draft_generation._system_prompt()` composes
`render_contract(DRAFT_GENERATION_CONTRACT) + "\n\n" + TASK_INSTRUCTIONS` - two layers in the code,
concatenated only where the transport requires it.

Adding a new provider means writing its contract next to its adapter (a `ProviderContract`
literal) and building its task context - never inventing a new context mechanism.

## Perplexity: `company_research`

```text
Career-agent -> ResearchQuery -> Perplexity -> ResearchResult -> Career-agent
```

`app/integrations/research/ports.py` defines the port:

- `ResearchSubject(company_name, website?, location?, sector?)` - **no candidate data ever
  belongs here**;
- `ResearchQuery(objective, subject, focus_areas=(), max_results=10)` - **Career-agent sets the
  objective**; a provider never chooses what matters;
- `Observation(claim, source_url?, source_title?, excerpt?, published?, metadata)` - one sourced,
  external claim;
- `ResearchResult(provider, model, query, status, observations, summary, retrieved_at, usage,
  duration_ms)` - `summary` is the provider's own narrative synthesis, kept **separate** from
  `observations`: it is prose, not a per-fact sourced claim, and must never be read as if each
  sentence had its own citation;
- `ResearchError(code)` / `ResearchErrorCode` (`unauthorized`, `rate_limited`, `timeout`,
  `unavailable`, `invalid_response`, `unsupported`, `other`) - the only way a call fails; only the
  code is ever kept.

### Not reused from `app.integrations.sourcing`

`SearchQuery`/`SearchHit`/`SourcedItem`/`SearchRun` (step 3c) model a job or company **listing**
found by a web search or job board - normalised deterministically into a `Company`/`Opportunity`
candidate, with de-duplication and qualification wired in. Company research is free-text research
about an **already-known** company (its activity, tech environment, recent news): there is no
offer-shaped output, no `HitExtractor`-style parsing, and forcing one shape onto both would leak
either the offer-sourcing rules into research or the reverse. `app.integrations.research` is a
small, separate, analogous abstraction instead - same conventions (a `Protocol` port, immutable
dataclasses, a closed error-code enum, an injectable HTTP transport), not a forced reuse.

### `PerplexityClient`

`app/integrations/research/perplexity.py`. The API key is read from the existing `SecretStore`
(`app.core.secrets`, name `PERPLEXITY_API_KEY`) at call time, never at construction, never logged,
never in an exception. Uses `http.client` (standard library, shared transport shape with the
OpenRouter adapter via `app.integrations.http_transport`): no new HTTP dependency.

**Endpoint, determined by reading Perplexity's current documentation during this step
(2026-09-27):** the previous "Sonar Chat Completions" endpoint (`/chat/completions`, a `model` +
`messages` array, OpenAI-style) was retired that same day in favour of the **Agent API**
(`POST https://api.perplexity.ai/v1/agent`): `input` (the task) + `instructions` (the contract) +
`preset`/`model`, an `output` array of typed items, citations as a `search_results` output item
with `url`/`title`/`snippet`/`date` per result. This adapter targets the Agent API. This was
verified once, against public documentation, on one date - re-check before relying on it far into
the future; provider APIs change.

Observations are built **only** from the `search_results` output item: a source and its excerpt
always travel together, exactly as the search tool surfaced them. The model's free narrative
(`summary`) is never decomposed into per-fact claims - pairing a plausible-looking URL with a
sentence the model wrote, rather than one the search tool verified, would be exactly the kind of
provider-output-as-truth conflation this step exists to prevent. A search result without a URL is
dropped, never invented a URL for.

`CompanyResearchService` (`app/services/company_research.py`) builds the query from an existing
`Company` record (never candidate data) and returns the provider's result **unchanged** - no
field of it is read to make a decision, nothing is persisted. `default_research_provider` mirrors
`app.api.drafts.get_llm_client` exactly: `None` unless `RESEARCH_ENABLED=true`,
`PERPLEXITY_PRESET` and the `perplexity_api_key` secret are all present, so nothing depends on
Perplexity in production until an operator deliberately turns it on.

**No API route exists for this step.** The flow above stops at "Career-agent" (a service call),
not at an HTTP response: this step builds the research capability and its boundary, not a new
surface to expose it. Adding a route is a small, separate addition for whichever future step needs
one.

**Step 7** (`docs/research_batch.md`) is the first real caller of this capability: it decides,
deterministically, WHICH company and WHICH dimension is worth a `company_research` call - never
"research everything unknown" - so that qualifying many opportunities does not mean one Perplexity
call per opportunity.

### Manual smoke test

`scripts/manual/perplexity_smoke_test.py` (NOT part of the automated suite: it makes a real
network call). Builds a real, public, non-sensitive company (no candidate data, no CV, no e-mail)
through the existing `CompanyService`, then calls a real `PerplexityClient` built from the
existing `SecretStore` and `$PERPLEXITY_PRESET`. Confirmed once, in this step, end to end:

```powershell
$env:PERPLEXITY_PRESET = "low"
.venv\Scripts\python.exe scripts\manual\perplexity_smoke_test.py
```

## OpenRouter: unchanged behaviour, contract-aware internals

Per step 6's restriction, the OpenRouter integration itself was **not** re-implemented. Only the
context mechanism was introduced, without touching `LLMClient`, `GenerationRequest`,
`GenerationResult`, `ApplicationDraft` or any existing test:

- `app.integrations.llm.ports.TokenUsage` now re-exports the shared
  `app.integrations.provider_context.TokenUsage` (one usage shape for every provider, not two);
  `app.integrations.llm.openrouter` and `app.integrations.research.perplexity` share the tiny
  HTTPS transport `Protocol`s (`app.integrations.http_transport`) instead of duplicating them;
- `app.services.draft_generation` now composes `DRAFT_GENERATION_CONTRACT` (declared next to the
  task it serves, not inside the OpenRouter adapter - the contract describes Career-agent's
  *generation* role, independent of which `LLMClient` backend fulfills it) with its existing
  task instructions, instead of one undifferentiated prompt constant.

OpenRouter remains exactly a **generation provider**: it still never decides which skills are
real, never touches the database, never sends anything - only `system_prompt`'s *composition*
changed, not what the model is told to do or what Career-agent does with what comes back.

## Limits (deliberate)

- No API route for company research yet (see above).
- No persistence: a `ResearchResult` is returned to the caller and not stored anywhere; turning
  it into Career-agent evidence (linking an `Observation` to a `Company` field, an `Evidence` row,
  or a future company-research audit trail) is a deliberate, separate step, not done here.
- `RESEARCH_ENABLED` stays `false` by default: nothing depends on Perplexity in production yet.
- The Agent API's richer capabilities (tools beyond `web_search`, multi-turn `previous_response_id`,
  structured `response_format`, `max_steps` beyond the default) are not used: this step is
  deliberately the smallest useful `company_research` case, not a general Perplexity client.
