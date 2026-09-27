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
matching with the Brain and the personalisation brief (3b, see requirements.md) -> personalisation -> validation -> sending -> tracking.

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

Business agents, LLM usage, real search providers (the sourcing ports are in place, see
sourcing.md), scraping, browser automation, applications and email sending. (CV ingestion exists: see cv-ingestion.md.) Each will be added in a dedicated step.
