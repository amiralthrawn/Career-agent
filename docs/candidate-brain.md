# Candidate Brain (V0.1)

## 1. Why it exists

Later steps (offer matching, application generation, outreach) will use language models. A
model asked to "write a cover letter for this candidate" will happily invent skills,
experiences or diplomas that sound plausible. The Candidate Brain is the **single, structured,
source-grounded description of the candidate** that every future agent must read from. Anything
that is not in the Brain, or is in the Brain without evidence, must not be *asserted as true*.
Just as importantly, it must not be *asserted as false* either: see section 6.

## 2. Facts / Preferences / Constraints / Evidence

| Kind | Question it answers | Examples | Backed by evidence? |
| --- | --- | --- | --- |
| **Facts** | What is true about the candidate? | a skill, a project, a degree, a job, a language | Yes - each fact can be linked to evidence |
| **Preferences** | What does the candidate want? | target roles, contract types, remote preference, target sectors | No - they are declarations |
| **Constraints** | What limits the candidate? | geographic, availability, salary, schedule limits | No - they are declarations |
| **Evidence** | Why do we believe a fact? | a CV, a GitHub repository, a diploma, a certificate | - |

The distinction is strict: *"I want to work in Data/AI"* is a **preference**. It is not a skill
and must never be turned into one. A preference can guide *which offers to look at*; only facts
(with evidence) can be used *to describe the candidate in an application*.

## 3. Entities

- `Candidate` - the main candidate (identity data). One candidate for now.
- Dates on facts keep the precision of the source (`YYYY`, `YYYY-MM` or `YYYY-MM-DD`, with a
  `year` / `month` / `day` precision reported by the API); a missing month or day is never
  filled in.
- Facts (all belong to a candidate, all can have evidence):
  `Education`, `Experience`, `Project`, `Skill`, `Certification`, `Language`.
  Optional fields (a skill `level`, a language `level`, an education `status`...) stay `NULL`
  until known - nothing is inferred, and `NULL` means "not known", never "none" or "lowest".
- `CandidatePreference` - one row per candidate (lists of roles, contract types, locations,
  sectors, domains, companies, company sizes; remote preference; salary range).
- `CandidateConstraint` - generic: a `constraint_type`, a description, an optional structured
  `value` (JSON) and `is_hard` (must never be violated vs. preferably respected).
- `Evidence` - a proof that information exists: `source_type` (cv, github, portfolio, diploma,
  certification, cover_letter, document, linkedin_export, other), `source_name`, `source_uri`,
  `extracted_text`, `source_metadata` (JSON), `confidence` (low/medium/high) and `verified`.
- `EvidenceLink` - joins one Evidence to one fact, with an optional `note` explaining why.

Enumerations are stored as VARCHAR (not native PostgreSQL enums), so adding a value never
needs an `ALTER TYPE`.

## 4. Relations

```text
Candidate 1 ──< Education / Experience / Project / Skill / Certification / Language
Candidate 1 ──  CandidatePreference        (at most one)
Candidate 1 ──< CandidateConstraint
Candidate 1 ──< Evidence

Evidence  1 ──< EvidenceLink >── 1 fact (exactly one of the six fact types)
```

**Design choice - explicit foreign keys instead of a polymorphic link.** `EvidenceLink` has one
nullable foreign key per fact type (`skill_id`, `project_id`, `experience_id`, `education_id`,
`certification_id`, `language_id`) and a `CHECK` constraint enforcing that **exactly one** is set.
A generic `(target_type, target_id)` pair would be shorter, but it cannot carry foreign keys: the
database could not prevent dangling links or cascade deletes. The cost of the explicit version is
one column per fact type, which is acceptable for six stable types. `EvidenceLink.target_type` and
`target_id` are exposed as read-only properties, so callers still see a uniform interface.
Each `(evidence, fact)` pair can only be linked once.

Deleting a fact, an evidence or the candidate removes the related links (`ON DELETE CASCADE`).

## 5. Source-grounded principle

Every fact is *born* without evidence, and is therefore `unknown` (the fact exists in the Brain,
but nothing yet backs it). Evidence is attached
explicitly (`POST /api/candidate/evidence-links`); the service checks that the evidence and the
fact belong to the same candidate.

The **state** of a fact is *derived* from its evidence and never stored, so it cannot drift:

| State | Rule |
| --- | --- |
| `unknown` | the fact exists in the Brain, but has no usable evidence (this is *not* "false") |
| `uncertain` | evidence exists, but only low-confidence and unverified |
| `known` | at least one medium/high-confidence evidence, none verified |
| `verified` | at least one `verified` evidence |

This is deliberately not a scoring system: `verified` is a boolean set by a human decision, and
`confidence` is a three-value label. The goal is **traceability**, not an arbitrary number.
New evidence defaults to `confidence = low` and `verified = false`.

`source_uri` is either an `http(s)` URL or a path **relative to `data/private/`** (absolute paths,
`~` and `..` are rejected). File contents are not stored in the database at this stage.

## 6. Absence of information is not negative information

> **Absence of information ≠ negative information.**

Three situations must never be confused:

| Situation | In the Brain | Meaning | What may be asserted |
| --- | --- | --- | --- |
| Fact with evidence | row exists, `state = verified` (or `known`) | backed by a source | the fact may be asserted, with the confidence its state allows |
| Fact without evidence | row exists, `state = unknown` | someone recorded it, nothing supports it yet | nothing: neither "the candidate has it" nor "the candidate lacks it" |
| No fact at all | **no row** (call it *not known* / absent) | the Brain holds no information on the subject | nothing: neither "the candidate has it" nor "the candidate lacks it" |

Concretely, with three skills:

```text
Python -> Skill row + evidence      -> state = verified   -> may be claimed
Java   -> Skill row, no evidence    -> state = unknown    -> may not be claimed, may not be denied
Rust   -> no Skill row at all       -> not known / absent -> may not be claimed, may not be denied
```

Rules that follow:

- `unknown` means only *"the fact exists in the Brain but has no usable evidence"*.
- A missing row means only *"the Brain has no information on this"*. It is **not** a state and is
  deliberately not stored: there is no "absent" or "false" value in `InformationState`, and no
  such value must be added. The V0.1 model has no way to record a negative claim.
- No absence (missing row, missing evidence, `NULL` field) may be interpreted as a negation.
  "Java is not in the Brain" never becomes "the candidate does not know Java"; a `NULL` skill
  `level` never becomes "beginner"; an empty language list never becomes "speaks no other
  language".
- **Future agents must respect this distinction.** Against an offer requiring Java, the correct
  reading is "no information / not established", never "gap" or "does not qualify". Anything
  built on the Brain (matching, application writing, outreach) must treat absent and `unknown`
  facts as *questions to ask the candidate*, not as weaknesses to report or as reasons to reject
  an offer, and must not write sentences such as "I have no experience with X".
- Recording that the candidate genuinely lacks something (an explicit negative fact) is a
  separate, deliberate feature, not implied by absence. It is out of scope for V0.1.

## 7. How this prevents the AI from inventing information

1. Agents read the Brain through the service layer, not free text: only rows that exist can be
   used.
2. The `state` of each fact tells an agent how strongly it may assert it: `unknown` facts must
   neither be claimed nor denied; `uncertain` ones need hedging or confirmation; `known`/`verified`
   can be used. Facts that are absent from the Brain are treated like `unknown` (section 6).
3. Skill levels are never computed - they are `NULL` unless the candidate provided them.
4. Preferences and constraints live in separate tables, so "wants to do X" cannot be confused with
   "can do X".
5. Any generated statement should be traceable to fact ids and, through them, to evidence ids
   ("why do we say the candidate knows Python?" -> follow `evidence_ids`).
6. Later steps will add a check that rejects generated content *asserting* facts that are absent
   from the Brain or `unknown` (see section 9). That check must not reject content merely for
   *not mentioning* a fact, and must not turn absence into a negative statement.

## 8. Example

```text
Skill:
Python

Evidence:
- Example-Project-A
- CV

Confidence:
verified
```

```text
Skill:
Java

Evidence:
aucune

State:
unknown
```

```text
Skill:
Rust

Skill row:
none (the Brain contains no information about it)

State:
not known / absent  (not a stored state; it must never be read as "the candidate does not know Rust")
```

(Names above are illustrative only; no real candidate data is stored in this repository.)

## 9. Planned evolutions

Not implemented yet, deliberately:

- ingestion of documents other than the CV (the CV chain exists: see `cv-ingestion.md`);
- update / delete endpoints and a way to mark evidence as verified after creation;
- extraction of text/skills from documents (with the extracted values proposed as
  *unverified* evidence, never as facts);
- a guard verifying that generated applications only reference facts present in the Brain;
- multi-candidate support (the schema already uses `candidate_id` everywhere).

## 10. API summary

All routes are under `/api/candidate` (API -> service -> repository -> model):

`GET|POST` on `` (the candidate), `/skills`, `/projects`, `/experiences`, `/education`,
`/certifications`, `/languages`, `/preferences`, `/constraints`, `/evidence`, plus
`POST /evidence-links`. Errors: `404` (no candidate / unknown id), `409` (duplicate),
`422` (invalid payload), `503` (database not configured).
