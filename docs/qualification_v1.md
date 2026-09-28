# Qualification Criteria v1 (step 3a)

Answers exactly one question: **"does this opportunity fall within my search scope?"**
Deterministic, explainable, versioned, reproducible. No LLM, no network call, no score, no
personalisation - those stay out of scope (3b/3c/4).

## Where the criteria live

Criteria are **data**, not a new configuration file. Career-agent already has a deterministic
qualification engine (step 3a, built earlier): `SearchProfile` + `SearchCriterion`
(`app/models/search.py`), evaluated by `app/services/criteria_evaluation.py` and persisted,
immutably, as `Qualification`/`CriterionResult`/`QualificationReason`
(`app/models/qualification.py`). "Criteria v1" is a **concrete search profile** named
`"Criteria v1"`, seeded once by `app/services/criteria_v1.py::ensure_profile` and reused on every
later call (idempotent by name) - not a parallel engine, not a second decision model.

The existing decision vocabulary (`QualificationStatus`: `candidate` / `excluded` /
`needs_information`) is reused UNCHANGED, because six-plus already-shipped steps (contact
intelligence, application packages, sending, lifecycle tracking...) depend on it. The spec's own
words (`qualified` / `not_qualified` / `uncertain`) are a **display mapping**
(`app.schemas.opportunity_qualification.DECISION_LABELS`), applied only by the new CLI and the new
`/api/opportunities/...` routes - the database keeps exactly one vocabulary.

## The two criteria

"Criteria v1" has exactly two REQUIRED criteria (both immutable, both versioned like any other
`SearchCriterion`):

1. `contract_type`, `any_of` `[apprenticeship, professionalization]` - **the only inflexible
   constraint** (section 2.1). `EmploymentType` gained a `professionalization` member (a plain,
   additive `StrEnum` value - no migration needed, values are stored as `VARCHAR`) because
   "alternance" covers both contract types in France and only `apprenticeship` existed before.
2. `keyword`, `any_of`, every term of `app/services/role_taxonomy.py` (114 terms across the 7
   targeted job families of section 9 plus the `adjacent_technical` terms of section 10) - a
   **positive** list (what the search IS about), never a blacklist (section 11 forbids those).

Both criteria are evaluated by the pre-existing, unmodified `criteria_evaluation.py`. For an
`any_of` criterion that evaluator can only return `satisfied` / `not_matched` / `unknown` - never
`incompatible` (only a closed vocabulary, like `contract_type`, or an excluded value can produce
that). Consequence: **only `contract_type` can ever exclude a target.** An offer whose title and
description match no recognised term becomes `needs_information` ("uncertain"), never `excluded`
("not_qualified") - exactly the "ambiguous title -> uncertain, no blacklist" rule of sections 10-11.

Everything else the spec says is NOT a filter - location (section 3), availability (4), education
(5), salary (6), skills (7), company sector (8) - simply has no criterion at all. There is no
`CriterionDimension` for education, salary or availability; skills are matched separately
(`RequirementMatch`, step 3b) and never touch `QualificationStatus`.

## Non-gating context: role family and location tier

Two labels are computed at READ time only (never stored, like `QualificationRead.stale`), and
never feed back into the decision:

- `app.services.role_family.classify` - which job family (if any) the offer's title/description
  matches, using the SAME taxonomy as the `keyword` criterion. `unknown` when there is no offer or
  nothing recognisable - never a negative signal.
- `app.services.location_tier.classify` - `priority` (Paris/92) / `idf` (rest of Île-de-France) /
  `accepted` (elsewhere in France, or remote stated) / `remote_abroad` (outside France with remote
  stated) / `unknown`. A plain, versioned, best-effort text match against a small term list - not a
  geocoder, and never a numeric score (section 15 forbids those).

## Service and layering

`OpportunityQualificationService` (`app/services/opportunity_qualification.py`) wraps the
EXISTING `QualificationService` unchanged: it resolves/creates the "Criteria v1" profile, calls
`qualify()`/`current()` with that profile's id, and labels the result. Both the CLI and the API
call this same service - the architecture the spec asks for:

```
CLI -> OpportunityQualificationService -> QualificationService/repositories -> SQLite
API -> OpportunityQualificationService -> QualificationService/repositories -> SQLite
```

## CLI

The first commands of a new, minimal `career-agent` CLI (`app/cli/`, stdlib `argparse` - no new
dependency), installed via `[project.scripts]` in `pyproject.toml`:

```
career-agent qualification run <target_id>
career-agent qualification show <target_id>
career-agent opportunities qualified
career-agent opportunities uncertain
```

Run with `python -m app.cli <args>` without installing, or `career-agent <args>` after
`pip install -e .`. Symbols: `✓` satisfied/qualified, `—` incompatible/not_qualified, `?`
not_matched/unknown/uncertain, `★` a priority (or IDF) location tier.

```
$ career-agent qualification show 12
Target #12
Decision:     ✓ QUALIFIED  (criteria: v1)
Role family:  data_ai (matched: data analyst)
Location:     ★ priority
Stale:        no
Reasons:
  ✓ contract_type   satisfied     expected=[apprenticeship, professionalization] actual=apprenticeship
  ✓ keyword         satisfied     expected=[software developer, ...] actual=data analyst
```

## API

```
POST/GET /api/opportunities/{target_id}/qualification
GET      /api/opportunities/qualified
GET      /api/opportunities/uncertain
```

`target_id`, not a separate `opportunity_id`: qualification has always operated on `Target` (the
pipeline's addressable unit, which has zero or one `Opportunity` - a spontaneous application has
none), matching the existing `/api/targets/{id}/qualify` convention. This is the one deliberate
naming reconciliation with the spec's literal wording - flagged, not silently decided.

## What this step does NOT do

No LLM call, no network call, no write to the Candidate Brain, no mail, no sourcing, no invented
data, no score/percentage anywhere, no 3b/3c/4 behaviour. `RequirementMatch`/skill matching (3b)
keeps running exactly as before, unaffected by this step.
