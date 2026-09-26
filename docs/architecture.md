# Architecture (foundation step)

```
app/
├── main.py      FastAPI application factory (create_app)
├── api/          HTTP layer: routers only, no business logic
├── schemas/      Pydantic models for request/response payloads
├── services/     Business logic, called by the API layer
├── repositories/ Data access (queries, persistence), called by services
├── models/       SQLAlchemy models (Candidate Brain, see candidate-brain.md)
├── core/         Configuration, DB engine/session, domain errors
└── agents/      Reserved for future business agents (empty)
migrations/      Alembic environment (URL taken from app settings)
data/private/    Personal candidate data - git-ignored, never committed
```

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

Business agents, LLM usage, scraping, browser automation, applications, email sending
and matching. (CV ingestion exists: see cv-ingestion.md.) Each will be added in a dedicated step.
