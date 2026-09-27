# Application workflow (step 9)

**Evidence-first, then generation; a human still decides.** This step turns a qualified `Target`
into an exploitable, reviewable candidature by wrapping the EXISTING `ApplicationDraft` mechanism
(step 4) in an `ApplicationPackage`: a reference to the original CV, the accepted contact if any
(step 8), and clearly-separated company/GitHub evidence - all computed BEFORE the LLM ever runs,
and never merged into the draft's own Candidate-Brain-only `claims`/`selected_evidence`.

```text
Target -> Company/Opportunity -> Qualification + RequirementMatch (step 3, unchanged)
   |
Company research (step 7, accepted CompanyResearchFact only)
   |
GitHub evidence (step 9, deterministic match against requirement labels)
   |
Accepted Contact (step 8, never a `pending` observation)
   |
ApplicationPackage.personalization_context (company_evidence / contact / github_evidence)
   |
ApplicationDraft.generate(..., extra_context=...) (step 4, reused, additive parameter)
   |
Human validation: draft -> pending_validation -> approved / rejected
```

## This is not a second draft system

`ApplicationDraft` (step 4) is reused EXACTLY as it was: `claims`/`selected_evidence`/`warnings`
stay Candidate-Brain-only, built the same way, from the same `PersonalizationBrief`. The only
change to `app.services.draft_generation` is one additive, optional parameter,
`extra_context: dict[str, Any] | None`, threaded into `build_context()` under its OWN keys
(`company_evidence`, `contact`, `github_evidence`) - never merged into `strengths`/`do_not_claim`.
Omitting it (every existing caller does) reproduces step 4's context byte-for-byte; no existing
test needed to change.

`ApplicationPackage` (`app/models/application_package.py`) sits AROUND the draft: it references
the qualification, the draft, the original CV document, and the accepted contact, and holds its
OWN, separate JSON field for the additional evidence - so a Candidate Brain fact, a company fact
and a GitHub repository are never fused into one undifferentiated, unsourced assertion.

## Candidate Brain vs. GitHub evidence

A Candidate Brain fact (`Skill`/`Project`/`Experience`/...) is something the candidate told
Career-agent about themselves, with its own `InformationState` (known/verified/uncertain). A
GitHub repository is a PUBLIC artefact the candidate happens to own; `GitHubEvidence` never
carries a skill level and is always `is_personal_project=True` - a project is never presented as
professional experience. The two live in different JSON buckets on purpose
(`ApplicationDraft.claims`/`selected_evidence` vs. `ApplicationPackage
.personalization_context["github_evidence"]`) and are never merged into one claim.

## GitHub research (`app/integrations/github/`, `app/services/github_evidence.py`)

A new, separate port - not a `ResearchProvider` (that shape is for PROSE research about a
COMPANY; this is STRUCTURED, typed data about the candidate's OWN public repositories from one
fixed source). `GitHubProvider.research(username) -> GitHubResult` returns non-fork repositories
with name, description, languages, topics and a README excerpt - never a claim of skill or
quality. `GitHubClient` is the real adapter (same injectable HTTP transport as OpenRouter/
Perplexity, no new dependency); an optional `github_token` secret only raises the unauthenticated
rate limit, never required for public data. Off by default (`GITHUB_ENABLED=false`); unavailable
or erroring GitHub NEVER blocks a package - the rest of the evidence is still used, with an
explicit `github_unavailable` warning.

`match_repositories(repositories, requirement_labels)` is a plain, deterministic word-overlap
check (`app.core.normalize.normalize_text`) between a requirement's label and a repository's own
name/description/topics/languages/README - never a similarity score. A repository that matches
nothing is never offered; a fork is never offered. `rank` orders matches by how many distinct
requirements they answer (ties by name), the same explainable-not-a-score convention as
`PersonalizationBrief.emphasis_candidates`.

## Company research and contact framing

Company evidence is exactly the ALREADY-ACCEPTED `CompanyResearchFact` rows (step 7), capped to
the 3 most recent - "quelques éléments très pertinents suffisent", never a data dump, never a new
research call from this step. Contact framing comes from an ACCEPTED `Contact` only (step 8): the
best available channel (never invented), and a fixed, deterministic `approach_hint` looked up from
`RoleCategory` (`APPROACH_HINT` in `app.services.application_package`) - a generic instruction on
WHICH ANGLE to lead with (role fit / team mission / a technical project / direct), never a
fabricated fact about that person's own responsibilities, projects or opinions. A `pending`
`ContactResearchObservation` is never read here at all; a `do_not_contact` contact is skipped when
selecting (never silently used, never silently contacted - sending remains unbuilt).

## Human-in-the-loop (`ApplicationPackageStatus`)

`draft -> pending_validation -> approved / rejected`, plus `superseded` (mirrors `DraftStatus`
exactly). `prepare()` always creates a package (evidence selection is deterministic, no LLM
needed for it); if an LLM is configured it also generates the draft and the package moves straight
to `pending_validation`, otherwise it stays `draft` - a package with no generated text yet simply
cannot be approved or rejected (`409`). Preparing again with an LLM now available completes and
supersedes a `draft`-status package; nothing is silently overwritten, since content is immutable
by database trigger, exactly like `application_drafts`.

## Idempotence and staleness

`ApplicationPackage.inputs_fingerprint` hashes the qualification's own fingerprint, the accepted
company fact ids, the accepted contact's id/`updated_at`, and the GitHub evidence actually used.
`prepare()` on an unchanged target with an existing `pending_validation` package of the same
fingerprint returns it unchanged - no wasted LLM call. A `draft`-status package is always
re-attempted regardless of its fingerprint (nothing to lose: it has no content yet). Any change to
the qualification, an accepted company fact, the accepted contact, or the GitHub evidence used
changes the fingerprint and marks the package stale (`GET` recomputes and reports `stale`); an
already-decided (`approved`/`rejected`) package is never silently regenerated - decisions stay
historical. A qualification made stale by new company research (step 7's own, existing rule: the
qualification fingerprint hashes the whole `TargetView`, including `company_research_text`) must
be re-qualified before a package can be prepared at all - the same rule step 4 already enforces
for draft generation.

## API (`app/api/applications.py`)

| Route | Purpose |
| --- | --- |
| `POST /api/applications/{target_id}/prepare` | evidence selection + (if an LLM is configured) generation |
| `GET /api/applications/{id}` | read, with `stale` recomputed live |
| `POST /api/applications/{id}/approve` | human approval (`pending_validation` only) |
| `POST /api/applications/{id}/reject` | human rejection (`pending_validation` only) |

No send route exists anywhere in this project yet; none was added here.

## Limits (deliberate)

- No dedicated frontend: API only, as instructed.
- GitHub matching is a transparent word-overlap check, not semantic search: a repository whose
  README describes a skill in different words than the requirement's exact label may be missed.
- `is_stale` re-runs the GitHub provider live on every `GET` (the same in-process, no-batching
  simplification as `CompanyResearchService`): acceptable since GitHub's public API is free and
  unbudgeted, unlike Perplexity/OpenRouter which stay explicitly rate/cost-limited elsewhere.
- Sending an application (Gmail) and tracking replies are the next, separate step, not built here.
