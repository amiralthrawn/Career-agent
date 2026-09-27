# Architecture (foundation step)

```
app/
├── main.py      FastAPI application factory (create_app)
├── api/          HTTP layer: routers only, no business logic
├── schemas/      Pydantic models for request/response payloads
├── services/     Business logic, called by the API layer
├── repositories/ Data access (queries, persistence), called by services
├── models/       SQLAlchemy models (Candidate Brain, see candidate-brain.md)
├── core/         Configuration, DB engine/session, domain errors, API security, secrets
├── integrations/ Adapters to external systems (mail ports, MIME, dry-run; ContactFinder port)
└── agents/      Reserved for future business agents (empty)
migrations/      Alembic environment (URL taken from app settings)
data/private/    Personal candidate data - git-ignored, never committed
```

## Pipeline

sourcing (CSV, or provider ports with no real provider yet: step 3c, see sourcing.md) -> Target -> **qualification against a search profile** (step 3a) -> requirements and
matching with the Brain and the personalisation brief (3b, see requirements.md) -> **application
draft generation, LLM as generator only** (step 4, see drafts.md) -> human validation -> sending
(not built) -> tracking (not built).

External research (Perplexity, `company_research`) is a separate, parallel capability, not a
pipeline stage: Career-agent may call it about a `Company` at any point; its result is external,
sourced observations, never applied as a decision by itself (step 6, see providers.md).

Step 7 closes the loop between the two: qualification -> information needs -> a research plan
(grouped by company, budget-limited) -> Perplexity -> accepted observations -> the SAME
deterministic qualification, run again. See research_batch.md.

Step 8 adds contact intelligence, downstream of qualification and parallel to draft generation:
qualification -> **contact research** (per target, per requested role category) -> Perplexity ->
a reviewable, sourced proposal (`ContactResearchObservation`) -> human accept/reject -> the
existing `Contact`/`ContactChannel`/`TargetContact` models. It never changes a qualification rule;
a generated draft may later use an accepted contact, but only as more grounded context, never as
an invented claim. See contacts_research.md.

Step 9 assembles the three: qualification + RequirementMatch -> company research (7) -> GitHub
evidence (new, `app/integrations/github/`) -> accepted contact (8) -> `ApplicationPackage`,
wrapping the existing `ApplicationDraft` generation (4) with this evidence as an additive,
optional context - never a second draft system, never a new claim mechanism. Human validation
(`draft -> pending_validation -> approved/rejected`) stays mandatory; still no send route exists
anywhere. See application_workflow.md.

## Dependency direction

`api → services → repositories → models` (schemas and core are shared). Routes translate HTTP to service calls and
back; they contain no business rules.

## Configuration

All settings live in `app/core/config.py` (`Settings`) and come from environment
variables or a local `.env`. `DATABASE_URL` is optional at import time so the API and tests
start without a database; it is required (`require_database_url`) when a DB is actually used.

## Frontend (future)

A Next.js/TypeScript app is expected in a top-level `frontend/` directory. The backend
already supports it through the `CORS_ORIGINS` setting. No frontend code exists yet.

## Not built yet

Business agents, a real LLM provider or research provider wired into the running application by
default (the LLM port, the research port, an OpenRouter adapter and a Perplexity adapter are all
in place and were each manually verified once against their real API - see drafts.md and
providers.md - but `LLM_ENABLED` and `RESEARCH_ENABLED` both stay `false` by default), a route
exposing company research or the requalification batch (the services exist, see providers.md and
research_batch.md), real search providers (the sourcing ports are in place, see sourcing.md),
scraping, browser automation, sending a draft and tracking. (CV ingestion exists: see
cv-ingestion.md.) Each will be added in a dedicated step.
