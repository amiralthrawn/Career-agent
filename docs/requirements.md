# Requirements, matching with the Candidate Brain, personalisation brief (step 3b)

A target may come with **requirements**: what the *source* (the offer text, or a human who read
it somewhere) asks for. Step 3b extracts them deterministically, records what the Candidate Brain
**establishes** about each one, and prepares the only input a future writer may read: the
`PersonalizationBrief`.

**Nothing here uses the network, an LLM or a clock. There is no score, no percentage and no
ranking of targets.** Out of scope (steps 3c and 4): web sourcing, `WebSearchProvider`, LLM
clients, message writing, sending.

```text
Offer text ──extract──> TargetRequirement (source + exact excerpt)
                              │
Candidate Brain ──────────────┼──match──> RequirementMatch (inside the immutable Qualification)
(Skill / Project / Experience │                          │
 + evidence states)           │                          └──> PersonalizationBrief (on demand)
```

## Principles

1. **Only what the source says.** A requirement exists only for a skill *written* in the text (or
   entered by a human with its excerpt), or for an explicit duration ("2 ans d'expérience").
2. **Exact excerpt, never generated.** `excerpt` is a contiguous slice of the source text.
3. **Importance is never inferred.** `required` / `nice_to_have` only from an explicit marker in
   the same sentence; anything else, including doubt, is `unspecified`.
4. **A gap is not a verdict.** `gap` means "not established by the Candidate Brain", never "the
   candidate lacks it". A skill gap never excludes a target.
5. **Only a Skill covers a skill.** A Project that mentions a skill can support an existing Skill;
   it never becomes one.
6. **Proximity is not coverage.** Tableau never covers Power BI; pandas never covers Python.
   Coverage between two *different* skills exists only where the taxonomy declares it
   explicitly and one way (`implies`): a Skill PostgreSQL covers a requirement SQL.
7. **Nothing is invented.** No date completed, no duration guessed, no fact created.
8. **Everything is append-only** and reproducible (fingerprint, versions).

## `TargetRequirement`

Belongs to a **target** (not to an offer), so a spontaneous target can carry manual requirements.

| Field | Meaning |
| --- | --- |
| `kind` | `skill` or `experience` |
| `key`, `label` | taxonomy key + canonical name (`python`, `Python`), or `experience_years:2`. A label outside the taxonomy keeps its normalised text as key |
| `importance` | `required` / `nice_to_have` / `unspecified` — only what the source says |
| `origin` | `offer_text` (extracted), `manual` (entered), `company_signal` (**reserved**: nothing creates it yet) |
| `source_field`, `source_id`, `source_hash` | where it was read: `opportunity.description_text` (with the offer's own `Source`, and the sha256 of the text read) or `manual` (with the source the human gave, and the hash of the excerpt) |
| `excerpt` | exact source text (the sentence; a bounded exact window if it is very long) |
| `value`, `qualifier` | experience only, **as written**: `2+ years`, `en data analysis` |
| `extractor_version` | rules version (`extract-1`); `NULL` for a manual entry |
| `active`, `deactivated_at`, `superseded_by_id` | the only mutable part (versioning) |

A spontaneous target has no offer text: extraction reports `no_offer` and creates nothing.
`requirements_total = 0` is a normal state, not an error.

### Immutability and versions

Requirement content never changes (ORM guard + database trigger, like search criteria). A change
creates a **new version** and deactivates the old one, which stays readable
(`include_inactive=true`). At most one *active* requirement exists per (target, kind, key), so a
re-extraction cannot duplicate.

Extraction of a target (explicit call only):

| Situation | Result |
| --- | --- |
| new key | created |
| same key, same content | `unchanged` (idempotent) |
| same key, offer content changed | new version, old one deactivated (`superseded`) |
| key no longer in the text | deactivated, not deleted (`retired`) |
| key already held by a **manual** requirement | manual one kept (`kept_manual`) |

A manual requirement for a key that an extraction created **supersedes** it; extraction never
overwrites a manual requirement. Manual entry needs the exact excerpt and where it comes from
(`reference` or `url`): no source, no requirement.

## Deterministic extraction (`RequirementExtractor`)

Pure function of the text and of `app/data/skill_taxonomy.json` (versioned, no personal data):
canonical name, categories and **three distinct relations**. Only the offer *description*
is read (a job title such as "Python Developer" is not a requirement statement).

- **Detection**: whole words, longest form first (`SQL Server` before `SQL`, `Node.js` before
  `js`); `NoSQL`, `MySQL`, `T-SQL`, `PL/SQL` never yield `SQL`; `Java` is not read in `JavaScript`.
- **Strict short terms** (`C`, `R`, `Go`, `Rust`, `Excel`): exact spelling **and** an unambiguous
  context (a "language" word nearby, or a list next to a recognised skill: `Python, R and SQL`).
  `Vitamin C`, `R&D`, `Go to market`, `go-getter`, `excel in a team` are never skills.
Taxonomy relations (never mixed up):

| Relation | Meaning | Example | Counts as coverage? |
| --- | --- | --- | --- |
| `aliases` | another written form of the same skill | `postgres` = PostgreSQL | yes (same skill) |
| `implies` | explicit, **one-way** coverage | Skill PostgreSQL covers requirement SQL (also MySQL, SQL Server, Oracle, SQLite) | yes, noted `skill_implied` |
| `related` | proximity only | Tableau ~ Power BI, pandas ~ Python, PostgreSQL ~ MySQL | **never** |

The reverse of an `implies` does not hold (a Skill SQL does not cover a requirement
PostgreSQL), a pair cannot be both `implies` and `related`, and the taxonomy refuses mutual
coverage at load time.

- **Importance markers**: required → `required`, `mandatory`, `must`, `requis`,
  `obligatoire`, `indispensable`; nice to have → `nice to have`, `preferred`, `desirable`,
  `souhaité`, `apprécié`, `bonus`, `a plus` / `un plus`.
  Where a marker is read, from the narrowest scope to the widest:

  1. **in parentheses** right after a term: it qualifies that term only: `Python (required),
     SQL (nice to have)`;
  2. **in the same sentence**: `Python is required. SQL is a plus.` gives Python `required` and
     SQL `nice_to_have`. A general sentence with no marker states nothing:
     `We are looking for someone who knows Python. SQL is a plus.` gives Python
     `unspecified`. One marker for a list applies to the whole list (`Python, SQL required`);
  3. **in the same clause** when one sentence holds contradictory markers: `Python required,
     SQL a plus` gives `required` / `nice_to_have`. A clause without marker never borrows
     another clause's (`Python, SQL required, Docker a plus`: Python is `unspecified`);
  4. **in the heading of a short block**: `Required:` / `Nice to have:` followed by lines,
     until a blank line, another heading, or 8 lines. The **excerpt then runs from the
     heading to the item**, so it contains the words that justify the importance; if the
     heading is too far to be quoted with the item (over the excerpt limit), it is not used.

  Doubt gives `unspecified`: a negated marker (`not required`, `pas obligatoire`), a
  contradiction that cannot be attributed (`Python is required and SQL is a plus` in one
  clause), a term whose own marker contradicts its heading, a negated heading, one skill
  mentioned twice with different markers.
- **Experience**: `2 years of experience`, `2+ years experience`, `2 ans d'expérience`,
  `expérience de 3 ans`, `2 à 3 ans d'expérience`. Kept as written; months are not read. If the
  duration is followed by a scope (`in data analysis`, `en gestion`), that scope is kept as
  `qualifier`.

Known limits: an ambiguous sentence gives `unspecified` (safe, sometimes less informative);
the taxonomy is a starter vocabulary, not an exhaustive ontology; a skill missing from it is not
detected.

## Matching (`RequirementMatch`)

One match per active requirement, **stored with the qualification** (immutable), with the Brain
facts it rests on (`RequirementMatchFact`: a reference to a Skill / Project / Experience, its
`role` and its evidence state at that time).

| Status | When | Facts |
| --- | --- | --- |
| `covered` | a **Skill** matching through the taxonomy (canonical name or alias) has state `known` or `verified` | the Skill (`establishes`) + Projects/Experiences that mention it (`supports`) |
| `weak` | the matching Skill exists but is `unknown` / `uncertain` | the Skill with its real state |
| `gap` | no matching Skill: **not established by the Brain** | none — or, if a Project/Experience mentions the skill, `mentions` (note `mentioned_by_project_only`: an open question) |
| `unmeasurable` | the data cannot give a reliable conclusion | see below |

Aliases match (`postgres` ↔ `PostgreSQL`, `PowerBI` ↔ `Power BI`). **Explicit coverage** goes one
way only: a Skill `PostgreSQL` (or MySQL, SQL Server, Oracle, SQLite) with state `known` or
`verified` covers a requirement `SQL` (note `skill_implied`, the real evidence state is kept in
the facts); `unknown` / `uncertain` gives `weak`. The reverse does not hold: a Skill `SQL` does
not cover a requirement `PostgreSQL`. Neighbours (`related`) never cover: pandas does not cover
Python, Tableau does not cover Power BI, MySQL does not cover PostgreSQL; they remain declared
for a future, explicit "related fact" (never coverage). A Project citing PostgreSQL supports a
covered SQL requirement, and with no Skill at all it is only an open question, as before.

### Experience durations

Interval arithmetic on **partial dates**: `2022` could be any day of 2022, so nothing is ever
completed with January 1st. For the established experiences (state `known`/`verified`) the
duration is bounded from below (what the dates *certainly* cover, overlaps counted once) and from
above (what they *could* cover):

| Situation | Status |
| --- | --- |
| Lower bound reaches the need | `covered` |
| Only the upper bound (or a missing date) could reach it | `unmeasurable` (dates insufficient) |
| Even the upper bound cannot reach it | `gap` (insufficient), or `weak` if experiences with unknown/uncertain evidence could |
| No experience in the Brain | `gap` |
| The duration is scoped to a domain (`qualifier`) | `unmeasurable`: the Brain does not say which experience relates to it |

An experience without end date is not "until today": no clock is read, so results never depend on
the date the code runs.

## Qualification integration

`Qualification` now also holds its `requirement_matches`. The **status stays 3a's** (`excluded` /
`needs_information` / `candidate`): matches never feed it. New raw counters, never combined:
`requirements_total`, `requirements_covered`, `requirements_weak`, `requirements_gap`,
`requirements_unmeasurable` (all `0` for a target without requirements).

The fingerprint now also hashes the active requirements, the Brain snapshot they are matched
against (names, texts, dates, evidence states of Skills, Projects, Experiences), the taxonomy and
matcher versions. A change of any of them makes the qualification **stale**; re-qualifying creates
a new one and keeps the old. Without requirements the Brain is not an input, so unrelated Brain
edits do not make such a qualification stale. `EVALUATOR_VERSION` moved to `criteria-3`, so
qualifications computed before 3b are reported stale once.

## `PersonalizationBrief`

Computed on demand from the **latest qualification** (the source of truth); not persisted. No
score, no timestamp: same inputs, same brief. Every item carries the ids of its system objects.

| Section | Content |
| --- | --- |
| `target` | company, offer or `null` (spontaneous), contract, source of the target |
| `qualification` | id, status, **`stale`** (see the contract below) |
| `strengths` | covered requirements + supporting Brain facts with their evidence state |
| `do_not_claim` | `gap`, `weak`, `unmeasurable`: nothing written may assert them |
| `open_questions` | what only the human can settle: confirm a weak skill, record a project-mentioned skill as a Skill, give precise dates, relate experiences to a domain |
| `emphasis_candidates` | established facts ordered by number of covered requirements they support, then a fixed type order (project, experience, skill), then id. `rank` is a position, not a score. Facts with `unknown`/`uncertain` state are never put forward |
| `company_context` | structure only, from stored data (sector, location, country, domain, URLs, offer location and remote mode); possibly empty; no external information |

A future writer (LLM or human) receives **only this brief**, never the database.

### Contract: `qualification.stale` (for the future personalisation module)

A brief built on a **stale** qualification is deliberately **returned with `stale: true`**
(status `200`), not refused with `409`: reading the state of a target must stay possible.
`stale: true` means the criteria, the target data, the requirements or the Candidate Brain
changed since that qualification was computed, so the brief may describe something that is
no longer true (a skill since verified, a requirement since added, a project since removed).

**The personalisation module MUST check `qualification.stale` before generating any
content.** If it is `true` it must not generate: it must re-qualify the target
(`POST /api/targets/{id}/qualify`) and read the brief again. Nothing in the API enforces this
for the caller, so a module that ignores the field could write from outdated facts. The check
belongs in the module's own entry point, with a test.

## API (all under the local API token)

| Route | Purpose |
| --- | --- |
| `GET /api/targets/{id}/requirements` | active requirements (`include_inactive=true` for history) |
| `POST /api/targets/{id}/requirements` | manual requirement (exact excerpt + source); idempotent (`200`) or a new version (`201`) |
| `POST /api/targets/{id}/requirements/extract` | explicit extraction; report of counters; audited (`requirements.extract`, counters only) |
| `GET /api/targets/{id}/personalization-brief` | the brief of the latest qualification (`404` if never qualified; `200` with `qualification.stale: true` when it is out of date, see the contract) |
| `GET /api/targets/{id}/qualification` | now includes the matches and the requirement counters |

## Limits (deliberate)

- `company_signal` is a reserved origin: nothing produces it in 3b.
- No requirement is extracted from the offer *title*. Headings only govern a short block.
- Spoken languages, degrees and certifications are not requirement kinds yet.
- Related skills are only declared in the taxonomy; no `related_fact` is produced yet.
- The taxonomy must be extended by hand (a new alias is a versioned data change).
