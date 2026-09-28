# Application lifecycle tracking (step 11)

**Never considered received, refused or accepted without sufficient evidence.** This step adds a
chronological, append-only history of what actually happened to a candidature -
`ApplicationEvent` - reusing every relevant mechanism already in place rather than inventing a
parallel one: `InfoStatus` (confirmed/uncertain), the immutable-content + `corrected_*_id`
"correction is a new row" pattern (`ApplicationDraft`, `CompanyResearchFact`), and the existing
`ApplicationPackage`/`SendBatch` services, which now emit these events themselves the moment they
already know something for certain.

```text
ApplicationPackage.prepare()  -> ApplicationEvent(prepared,  origin=system, status=found)
ApplicationPackage.decide(approve=True) -> ApplicationEvent(approved, origin=system, status=found)
SendBatchService._attempt() (sent)      -> ApplicationEvent(sent,     origin=system, status=found)
                                                    |
                                    everything else: human entry, or a (future, simulated-only)
                                    Gmail detection - always UNCERTAIN until a human confirms it
```

## Why a new model, not `AuditEvent`

`AuditEvent` (step 1) is a closed-vocabulary, whitelisted-detail SECURITY/operational trail - by
design it cannot carry an interview date, a free note, or a reference id. Reusing it for candidate
lifecycle facts would mean either bloating its whitelist with domain data it was deliberately built
to exclude, or losing that data entirely. `ApplicationEvent` is a genuinely different concern (rich
domain history a human and a future learning step both need to read) and is kept separate, exactly
the same reasoning that kept `CompanyResearchFact`/`ContactResearchObservation` separate from
`AuditEvent` in steps 7/8.

## Provenance model (reused, not reinvented)

| Origin (`ApplicationEventOrigin`) | Who/what | Status (`InfoStatus`, reused) |
| --- | --- | --- |
| `system` | `ApplicationPackageService`/`SendBatchService` themselves | always `found` - these three facts (prepared/approved/sent) are certain the instant they happen; a human can never manually claim `sent` (see `_SYSTEM_ONLY_TYPES`) |
| `manual` | a human, via the API | `found` by default (they are reporting their own inbox) - but the human MAY mark their own entry `uncertain` if they are unsure |
| `gmail` | a (future, currently simulated-only) reply detector | always `uncertain` - a classifier's guess is never auto-confirmed |

Absence of a row is absence of information, never a negative claim - the same "absence ≠
negation" rule applied everywhere else in this project. Nothing here computes or stores a single
"current status" enum on the package: the full event history is the source of truth, read on
demand (`GET /api/applications/{id}/events`), exactly like `PersonalizationBrief` is computed on
demand rather than cached.

## Corrections: a new row, never an edit

Every `ApplicationEvent` column is immutable (database trigger, migration `0014`, the same
convention as `CompanyResearchFact`). `corrected_event_id` points FORWARD from a correction to the
event it corrects, set once at creation, never mutated - the original row is never deleted or
overwritten, so a mistake stays visible in the history exactly as it was first recorded.
Confirming an `uncertain` event is just a correction that only changes `status` to `found`
(`POST /applications/{id}/events/{event_id}/correct` with `{"status": "found"}`).

The dedup index (`application_package_id`, `event_type`, `reference`) explicitly excludes
corrections (`corrected_event_id IS NULL`): a correction is EXPECTED to reuse the same reference
as what it corrects (the real-world message didn't change, only the human's reading of it did).

## Idempotence

- Manual entry with the same `(event_type, reference)`: returns the existing event, never
  duplicates it.
- Detected (simulated) events: the same Gmail message id is never recorded twice for the same
  package.
- Both are enforced by the same partial unique index, checked at the database level.

## API (`app/api/applications.py`, enriched, not duplicated)

| Route | Purpose |
| --- | --- |
| `GET /api/applications` | every package, or (`?event_type=&since=&until=`) only those with a confirmed event of that type in range |
| `GET /api/applications/needing-follow-up?days=14` | sent (confirmed) at least N days ago, no confirmed response since |
| `GET /api/applications/{id}/events` | full chronological history |
| `POST /api/applications/{id}/events` | manual entry (refuses `prepared`/`approved`/`sent`) |
| `POST /api/applications/{id}/events/{event_id}/correct` | correction (also how `uncertain` gets confirmed) |

No route here ever sends an e-mail or a follow-up automatically. `needing-follow-up` only ever
LISTS candidates for a human to act on - a follow-up becomes real either through the EXISTING
`POST /applications/{id}/send` (step 10, a human explicitly triggers it) or through a manual
`ApplicationEventType.FOLLOW_UP_SENT` entry once the human has sent one themselves.

## Connecting Gmail for real reply detection (not done in this step)

The current OAuth grant (step 10) is `https://www.googleapis.com/auth/gmail.send` - it can send,
and cannot read a single message. `app.integrations.gmail.reply_detection.ReplyDetectionProvider`
is the port a real implementation would satisfy; only `SimulatedReplyDetectionProvider` (tests
only, no network) exists today.

**Reading replies for real needs a WIDER OAuth scope - this project will not request it
silently.** The narrowest scope that would work is `https://www.googleapis.com/auth/gmail.readonly`
(read-only; still broader than "just my sent thread's replies", since Gmail's API has no scope
narrower than mailbox-wide read access). Before any real implementation of
`ReplyDetectionProvider` is built:

1. this exact scope change must be presented to you explicitly, the same way this document does
   now - never bundled into an unrelated change;
2. you re-run the OAuth consent flow (`scripts/manual/gmail_oauth_setup.py` would need updating
   to request it) and explicitly approve the new scope in Google's consent screen;
3. only then would a real client be built, following the exact same pattern as `GmailClient`
   (injectable HTTP transport, no new dependency, tested with a fake, never in pytest against the
   real API).

Until that happens, every "detected" event in this system comes from a human feeding
`SimulatedReplyDetectionProvider` explicit, synthetic `ReplyEvidence` in a test - never a real
Gmail read.

## Toward the future learning system (deliberately not built here)

`ApplicationEvent` already ties back, through `ApplicationPackage`, to `Target` -> `Company`/
`Opportunity`/`Qualification`/`RequirementMatch`/`CompanyResearchFact`/GitHub evidence/accepted
`Contact` - everything a later, SEPARATE step would need to analyse response/interview/offer rates
by role type, sector, company size, skill combination or personalization, without this step
building any of that analysis itself. No score, no rank, no automatic Candidate Brain edit exists
anywhere in this module. Append-only immutability guarantees negative and null outcomes are never
silently dropped - a future analysis reading this history is not exposed to survivorship bias
introduced by this step.

## Limits (deliberate)

- No real Gmail reply detection (see above) - by explicit instruction for this step.
- No computed "current status" field on `ApplicationPackage`; the full event list is the source
  of truth, read on demand.
- `needing-follow-up`'s "no confirmed response" check does not consider `acknowledged` (a
  delivery/read receipt) as a response - an auto-reply receipt is not a human reply.
- A follow-up is never auto-generated (no new draft, no new e-mail): only a listing, and a manual
  `follow_up_sent` entry once a human has acted.
