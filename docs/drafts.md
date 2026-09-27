# Application drafts: the first LLM layer (step 4)

Turns what the Candidate Brain, the qualification and the `PersonalizationBrief` already
established into a **draft** application e-mail. **The LLM is a generator, not a source of
truth.** Nothing here sends anything: a draft is `proposed`, a human `approve`s or `reject`s it,
and only a later, separate step may ever call a real mail provider.

```text
Candidate Brain + Company/Opportunity + Qualification + RequirementMatch + PersonalizationBrief
                                      │
                                      v
                           LLMClient.generate(...)
                                      │
                                      v
                         ApplicationDraft (status=proposed)
                                      │
                                      v
                             human approves / rejects
                                      │
                          (later step only) SendGuard / Gmail
```

**Nothing real is connected.** No OpenRouter call happens unless an operator explicitly sets
`LLM_ENABLED=true`, `OPENROUTER_MODEL` and the `openrouter_api_key` secret; by default every
generation is refused (`422`). No test in this codebase ever contacts OpenRouter or any network
LLM. Out of scope: Gmail, sending, Perplexity, scraping, an autonomous agent loop.

## Principle: the LLM never becomes a source of truth

It never touches the database, never calls a tool, and never decides that a `Candidate`,
`Experience`, `Project`, `Skill`, `Company`, `Opportunity`, `Target`, `RequirementMatch`,
`Qualification` or `Contact` exists or has some value. Concretely:

- `claims`, `selected_evidence` and `warnings` on a draft are built by the **application**,
  directly from the `PersonalizationBrief` (covered requirements and their supporting Brain
  facts), **never parsed out of the model's own text**. They stay true regardless of what the
  model's prose says, because they never come from reading that prose.
- The model receives a bounded, JSON-serialisable **context**: covered requirements with their
  facts (`strengths`), and what it must **never** claim (`do_not_claim`), plus the company/offer
  names and a few company-context fields. It never sees the raw database, an offer's full text,
  the candidate's identity, or anything beyond what the brief already vetted.
- `subject` is assembled by the application from already-validated names (company, offer title);
  the model only writes `body`.
- A literal, best-effort guard (`scan_forbidden_mentions`) flags a `do_not_claim` label found
  verbatim in the generated body as a `forbidden_mention_detected` warning. **This is not a
  semantic guarantee**: a model could still make an unestablished claim in different words. It
  never blocks generation; it only makes the risk visible to the human who must review the draft.
- A skill/project/experience the Brain does not establish is simply absent from the context: nothing
  in the code path can turn it into a claim.

## `LLMClient`

`app/integrations/llm/ports.py` defines the only interface the rest of the application depends
on; no other module references OpenRouter (or any provider) directly.

- `GenerationRequest(task, system, context, model=None, max_output_tokens=600, temperature=0.2)`:
  `system` is a fixed, versioned constant (never per-call free text); `context` is a plain,
  bounded mapping; bounds are enforced (`max_output_tokens` in [1, 2000], `temperature` in
  [0, 1]).
- `GenerationResult(text, model, status, usage, duration_ms)`: `status` is `ok` or `truncated`
  (the model stopped at the token limit).
- `LLMError(code)`: the only way a call fails; carries a closed `LLMErrorCode` (`unavailable`,
  `timeout`, `rate_limited`, `unauthorized`, `invalid_response`, `unsupported`, `other`) and
  **never** a message or any payload.

## `FakeLLMClient` (tests only)

`tests/llm_fakes.py`. Deterministic, in-memory, configurable: a default body builder that only
echoes `context["strengths"]` (so it cannot invent by construction, used for most tests), plus
helpers to script a specific response (`forbidden_mention`, `echo_context`, `constant`) or raise a
given `LLMError`. Every `FakeLLMClient` records the requests it received (`.requests`), so a test
can assert exactly what the model was told.

## OpenRouter adapter — prepared, not wired by default, **manually verified once (step 5)**

`app/integrations/llm/openrouter.py` implements `LLMClient` behind the existing `SecretStore`
(`openrouter_api_key`, unchanged: no new secret mechanism was introduced) and `OPENROUTER_MODEL`
(only sets the *default* model of a request; `GenerationRequest.model`, when given, still takes
precedence - no benchmarking or routing rule). It uses `http.client` (standard library): the
project has no HTTP dependency yet outside step 1's mail sending, and none is added for one call
site kept behind a disabled-by-default switch. The HTTP transport is injected (`connect=`), so
the automated suite exercises every response-handling branch with a fake connection - no test in
`pytest` ever touches a socket.

An explicit timeout (`timeout=`, default 30s) bounds a call; HTTP errors map to stable,
non-sensitive `LLMErrorCode`s (`401`/`403` -> `unauthorized`, `429` -> `rate_limited`, `5xx` ->
`unavailable`, another `4xx` -> `other`); a transport-level timeout, network error, invalid JSON,
a response with no exploitable content (missing `choices`, empty or blank text) all raise
`LLMError(INVALID_RESPONSE)` rather than fabricate a draft. OpenRouter's free router can also
answer **HTTP 200** with an `{"error": ...}` body when every free upstream failed: that body is
never treated as success either.

**Step 5 ran two real, manual smoke tests** (`scripts/manual/openrouter_smoke_test.py`, synthetic
data only) against `openrouter/free` and `deepseek/deepseek-v3.2`, through the exact
`PersonalizationBrief -> LLMClient -> ApplicationDraft` path: both produced a `proposed` draft that
used only the established facts and never mentioned an absent one (see the step 5 report for the
full output). This confirms the request/response handling against the real, live API for that one
scenario - it is not exhaustive (rate limiting, very long outputs and every provider behind the
free router remain unexercised), so treat further model or usage changes as worth re-checking with
the same script rather than assumed to keep working.

`get_llm_client` (`app/api/drafts.py`) is the single place that could construct this class; by
default it returns `None`:

| Condition | Result |
| --- | --- |
| `LLM_ENABLED=false` (default) | `None`: every generation is refused (`422`) |
| `LLM_ENABLED=true` but no `OPENROUTER_MODEL` or no `openrouter_api_key` secret | `None` |
| the secret store itself is unavailable | `None` (fails closed, never raises) |
| all three are set | an `OpenRouterClient` |

### Manual smoke test

`scripts/manual/openrouter_smoke_test.py` is NOT part of the automated suite (it makes a real
network call). It builds a synthetic scenario directly through the real services
(`TargetService`, `RequirementService`, `QualificationService`, `PersonalizationService`,
`DraftService`) against a throw-away, in-memory database, then calls a real `OpenRouterClient`
built from the existing `SecretStore` and `$OPENROUTER_MODEL`. It never sets `LLM_ENABLED` and
never touches `.env`. Run it with the secret already stored
(`python scripts/manage_secrets.py set openrouter_api_key`):

```powershell
$env:OPENROUTER_MODEL = "openrouter/free"        # or any other OpenRouter model id
.venv\Scripts\python.exe scripts\manual\openrouter_smoke_test.py
```

## `ApplicationDraft`

Belongs to a target, tied to the qualification (and, through it, the `RequirementMatch`es) it was
built from. Content is immutable (a database trigger, like `TargetRequirement`); only `status`,
`decided_at`, `decided_by` and `superseded_by_id` may change.

| Field | Meaning |
| --- | --- |
| `kind` | `application_email` (the only kind so far) |
| `status` | `proposed` / `approved` / `rejected` / `superseded` (see below) |
| `model` | the model identifier `LLMClient` reported (never the request's raw model string) |
| `subject`, `body` | the proposed text: `subject` app-assembled, `body` the model's output |
| `claims` | `[{"requirement", "facts": [{"type","id","name","state","role"}]}]`: app-derived, true |
| `selected_evidence` | deduplicated facts referenced by `claims`: exactly what was exposed |
| `warnings` | `[{"code","requirement","text"}]`: gaps / weak / unmeasurable / open questions / a forbidden-mention flag — nothing here may be asserted |
| `usage_prompt_tokens`, `usage_completion_tokens`, `duration_ms` | as `LLMClient` reported them |
| `qualification_id` | the qualification the brief came from (`SET NULL` if it is later removed) |

### Status and versioning

At most one **pending** (`proposed`) draft exists per (target, kind) — a database partial unique
index enforces it. Generating again:

1. supersedes the current pending draft (`status -> superseded`, kept, not deleted;
   `decided_at` stays null: **no human decided anything about it**);
2. inserts the new draft as `proposed`;
3. links the old one to the new (`superseded_by_id`).

An **already decided** draft (`approved` / `rejected`) is never touched by a later generation: it
stays exactly as the human left it. `approve` / `reject` only work on a `proposed` draft
(`422` otherwise) and set `decided_at` + `decided_by` together with `status`.

## Generation

`app/services/draft_generation.py`, `DraftService.generate`:

1. refuse (`422`) if no `LLMClient` is configured;
2. read the `PersonalizationBrief` of the target (reuses `PersonalizationService`: same
   ownership check, same `404` if the target was never qualified);
3. **refuse (`422`) if `qualification.stale`** — the documented contract of
   `docs/requirements.md` ("a personalisation module MUST check `stale` before generating"): this
   is that module, and it enforces it;
4. build `claims` / `warnings` from the brief, build the bounded context, call the `LLMClient`;
5. on `LLMError`: audit the attempt (`reason: "llm_error:<code>"`, no draft created) and refuse
   (`422`) — the error is represented, never swallowed, never turned into a fabricated draft;
6. scan the body for a literal forbidden mention (adds a warning, never blocks);
7. supersede the previous pending draft (if any), insert the new one, audit `draft.generated`
   (`reason: "generated"`, the model id — nothing else).

## Audit

Two event types, whitelisted detail keys only (`reason`, `model` — `model` was added to
`ALLOWED_DETAIL_KEYS` deliberately for this step): `draft.generated` (`reason` = `"generated"` or
`"llm_error:<code>"`) and `draft.decided` (`reason` = `"approved"` or `"rejected"`). Never
recorded: the API key, the prompt, the context, the generated text, or any payload.

## API (all under the local API token)

| Route | Purpose |
| --- | --- |
| `POST /api/targets/{id}/drafts` | generate a draft (`{kind?, model?}`); `201` |
| `GET /api/drafts/{id}` | read one draft |
| `POST /api/drafts/{id}/approve` | `proposed -> approved` (`422` if not proposed) |
| `POST /api/drafts/{id}/reject` | `proposed -> rejected` (`422` if not proposed) |

There is **no send route**: sending stays a later, separate step built on `SendGuard`/`MailSender`
(step 1), which this step does not modify or call.

## Limits (deliberate)

- One `DraftKind` (`application_email`) for now; a cover letter or a message kind would be a new,
  additive value.
- The forbidden-mention guard is literal and best-effort, documented as such; it is not a
  substitute for human review, which stays mandatory before any `approve`.
- `OpenRouterClient`'s success path is unverified (see above): treat it as prepared, not proven.
- No retry, streaming, or cost-tracking policy exists yet beyond recording `usage`/`duration_ms`
  when the client reports them.
