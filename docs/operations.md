# Running Career-agent locally (operational guide)

This is the practical guide to actually running the pipeline end to end, on your machine, with no
frontend yet. It assumes `docs/architecture.md` for the bigger picture; this page is about
commands.

## 1. Start the API

```powershell
$env:DATABASE_URL = "sqlite:///./data/career-agent.db"   # or a Postgres URL
.venv\Scripts\python.exe scripts\run_api.py
```

The API is the source of truth for every mutating action; the CLI (below) is a thin layer over
the same services. Swagger UI: `http://127.0.0.1:8000/docs`. Every `/api/*` route needs the local
API token (`Authorization: Bearer <API_TOKEN>` - see `scripts/manage_secrets.py init-api-token`).

## 2. Use the CLI

```powershell
.venv\Scripts\python.exe -m app.cli <command>
# or, after `pip install -e .`:
career-agent <command>
```

It needs the SAME `DATABASE_URL` as the API (they read the same database; run them against the
same one, not two different files). Command groups:

| Command | Purpose |
| --- | --- |
| `status` | Operational overview: last sourcing run, targets by decision, drafts/applications by status, recent activity |
| `qualification run\|show <target_id>` | Qualify against "Criteria v1" / show the current decision |
| `opportunities qualified\|uncertain` | List targets by decision |
| `sourcing search --mode offers\|companies [--profile ID] [--provider perplexity] [--max-results N]` | Start a real search run |
| `sourcing runs [--profile ID]` / `sourcing show <run_id>` | List / inspect past runs |
| `targets list` / `targets show <target_id>` | Inspect targets |
| `requirements show <target_id>` | A target's extracted/manual requirements |
| `brief show <target_id>` | The `PersonalizationBrief` |
| `drafts list [--status ...]` / `drafts show <id>` / `drafts generate <target_id>` | Application drafts |
| `applications list` / `applications show <id>` | Application packages and their history |
| `events [--target ID] [--application ID]` | The operational journal (`ApplicationEvent`) |

Preparing, approving/rejecting and sending a package are API actions
(`POST /api/applications/...`), by design: the CLI is for driving searches/drafts and for
inspection, never a second place a send can be triggered from.

## 3. Run a real Perplexity search

Requires `RESEARCH_ENABLED=true`, `PERPLEXITY_PRESET` set, and the `perplexity_api_key` secret
stored (see "Enabling real integrations" below).

```powershell
career-agent sourcing search --mode offers --max-results 10
career-agent sourcing search --mode companies --max-results 10   # spontaneous-application leads
```

Without that configuration, `sourcing search` still runs but is refused with "Unknown sourcing
provider" (`422`) - nothing is silently skipped. See `docs/sourcing.md` for what Perplexity can
and cannot reliably identify (it is conservative by design: most generic search-result pages are
correctly rejected for lack of a stated company name, never guessed).

## 4. Consult the results

```powershell
career-agent sourcing runs
career-agent sourcing show <run_id>       # counters, errors, per-item outcome
career-agent targets list
career-agent targets show <target_id>
career-agent qualification show <target_id>
```

## 5. Generate a draft

Requires `LLM_ENABLED=true`, `OPENROUTER_MODEL` set, and the `openrouter_api_key` secret stored.

```powershell
career-agent brief show <target_id>          # what the draft will be built from
career-agent drafts generate <target_id>
career-agent drafts show <draft_id>
```

Without LLM configuration, `drafts generate` refuses cleanly (no draft is fabricated).

## 6. Validate or reject a draft

```powershell
curl -X POST http://127.0.0.1:8000/api/drafts/<id>/approve -H "Authorization: Bearer $env:API_TOKEN"
curl -X POST http://127.0.0.1:8000/api/drafts/<id>/reject  -H "Authorization: Bearer $env:API_TOKEN"
```

A standalone draft's own approval is independent of the package flow below (see step 7): the
package's own review is what actually gates sending.

## 7. Prepare an ApplicationPackage

```powershell
curl -X POST http://127.0.0.1:8000/api/applications/<target_id>/prepare -H "Authorization: Bearer $env:API_TOKEN" -d '{}'
```

With an LLM configured, this generates the draft AS PART of preparing the package (one call,
status becomes `pending_validation`); without one, the package stays `draft` (evidence selected,
no text yet). Then:

```powershell
curl -X POST http://127.0.0.1:8000/api/applications/<package_id>/approve -H "Authorization: Bearer $env:API_TOKEN"
career-agent applications show <package_id>
```

## 8. Do a Gmail dry-run

The safe default: `SEND_MODE=dry_run` (or unset - the real default is `disabled`, which blocks
everything; set `dry_run` to actually rehearse). Then:

```powershell
curl -X POST http://127.0.0.1:8000/api/applications/<package_id>/send -H "Authorization: Bearer $env:API_TOKEN"
```

A real `.eml` file appears under `data/private/outbox/` - open it in any mail client to inspect
the exact message (headers, CV attachment) that would have been sent. No network call is made in
this mode, ever.

## 8b. Run a bounded daily campaign (chained sourcing + enrichment)

```powershell
career-agent sourcing campaign start --mode all --target 500 --max-calls 20 --max-duration-minutes 60
```

Chains as many searches as needed (up to `--max-calls`), enriching every new target it finds
(requirements, re-qualification, contact proposals, and a draft/package if an LLM is configured),
until the target is reached or a safety limit stops it. Runs in the foreground of the terminal you
start it in - it is not a background service. From another terminal, while it runs:

```powershell
career-agent sourcing campaign show <id>     # live progress + funnel
career-agent sourcing campaign list
```

See "13. A note on scale" below before running large daily targets.

## 9. Enabling real integrations

All three stay off by default and are enabled the same way (a config flag plus a stored secret -
never a secret in `.env`, which is git-ignored but still local plaintext; secrets live in the OS
credential vault via `scripts/manage_secrets.py`):

| Capability | Flag(s) | Secret |
| --- | --- | --- |
| Perplexity sourcing | `RESEARCH_ENABLED=true`, `PERPLEXITY_PRESET=<preset>` | `perplexity_api_key` |
| OpenRouter drafting | `LLM_ENABLED=true`, `OPENROUTER_MODEL=<model>` | `openrouter_api_key` |
| Gmail sending | `SEND_MODE=manual` (bootstrap, allow-listed) or `auto` (fully approved) | `gmail_client_id`, `gmail_client_secret`, `gmail_refresh_token` |

```powershell
python scripts/manage_secrets.py set perplexity_api_key
python scripts/manage_secrets.py set openrouter_api_key
```

Gmail's own OAuth connection is a separate, guided process - see `docs/send_batches.md` and
`scripts/manual/gmail_oauth_setup.py`. `python scripts/manage_secrets.py status` shows what is
already stored, never the values themselves.

A manual, controlled run of the real pipeline (real Perplexity/OpenRouter if enabled, Gmail only
if `SEND_MODE` allows it AND `--allow-real-send` is explicitly passed) is in
`scripts/manual/run_e2e_real.py` - see its own docstring; it defaults to stopping before any real
send.

## 10. Where to consult the history

- `career-agent status` - the single-screen overview.
- `career-agent events [--target ID] [--application ID]` - every recorded `ApplicationEvent`
  (prepared/approved/sent/rejected/...), newest first.
- `career-agent applications show <id>` - one package's own event history.
- `career-agent sourcing campaign show <id>` - a campaign's progress and funnel.
- The `audit_events` table (via a DB client, or `AuditLog`) - every service action's audit trail
  (counts and codes only, never personal data or secrets).

## 13. A note on scale (before running a large daily target)

- Each round costs a REAL Perplexity call (`--max-calls` bounds this - it is a real-money limit,
  not an arbitrary one). "500 sources/day" is a target, not a guarantee: as searches repeat
  similar criteria, more and more results are already-known (deduplicated), so the campaign is
  likely to stop on `stopped_no_new_results` well before `--target` on a narrow profile - widen
  the profile's criteria (more roles/locations) to sustain a higher daily volume.
- Contact proposals and drafts are best-effort and bounded per round (`MAX_CONTACT_CALLS_PER_ROUND`
  in `app/services/campaign.py`): a campaign PROPOSES contacts (pending your review, exactly like
  the existing contact-research flow) and can PREPARE a package's draft, but never approves or
  sends anything - `career-agent sourcing campaign show <id>`'s funnel distinguishes proposed
  contacts from ones you have actually accepted with a usable e-mail.
- Nothing here schedules itself: running this daily is your own choice (re-run the command each
  morning, or automate it with the Windows Task Scheduler) - Career-agent itself never wakes up on
  its own.
