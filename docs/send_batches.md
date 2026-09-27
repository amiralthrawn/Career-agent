# Controlled batch sending (step 10)

**Two explicit human approvals, one send mechanism, for one candidature or for a hundred.**
Step 9 already gets an `ApplicationPackage` to `approved` (a human read THIS candidature's actual
content). Step 10 adds a SECOND, EXPLICIT approval on top - the `SendBatch` itself - so a human
can select 15 (or 100) already-vetted candidatures and send them in one action, without ever
being forced to open Gmail and click Send 15 times, while every send-time precondition is
re-checked, per item, right before it happens.

```text
ApplicationPackage (step 9, status=approved)  <- layer 1: "I read and approved THIS candidature"
        |
   SendBatch.create([...ids])                  status=draft
        |
   SendBatch.approve()                          status=approved  <- layer 2: "send THIS group"
        |
   SendBatch.execute()                          status=executing -> completed / partially_failed
        |
   per item: re-validate -> build OutgoingEmail -> SendGuard -> audit -> GmailClient
```

## How individual vs. batch validation is resolved

**There is no second, parallel "send one" mechanism.** `POST /api/applications/{id}/send`
(individual send) internally creates, approves and executes a `SendBatch` of exactly **one**
item - the exact same code path, the exact same preconditions, the exact same audit trail as a
batch of 100. This is deliberate:

- it means a human's mental model stays simple: "approve the candidature, then approve sending
  it" is the same action whether it happens once or fifteen times at once;
- it means there is only ONE place (`app.services.send_batch.SendBatchService`) that can ever
  cause a real e-mail to leave this machine, one set of preconditions to get right, one audit
  trail to reconstruct history from - never two mechanisms that could silently drift apart;
- it is directly what the future learning system (Part 13) needs: every send, whatever triggered
  it, produces the SAME `SendBatchItem` row shape (outcome, timestamps, correlation id), so
  outcomes are comparable across a hand-picked batch of one and an automatically-assembled batch
  of a hundred, without a "which mechanism sent this" special case anywhere downstream.

## Two-layer approval, why it matters for trust

Layer 1 (`ApplicationPackage.status == approved`, step 9, unchanged) establishes that a human
read and accepted the CONTENT of one candidature - subject, body, evidence, contact. Layer 2
(`SendBatch.status == approved`, step 10) establishes that a human explicitly confirmed a GROUP
should be sent now. Neither approval is ever treated as a fact that stays true forever:
`SendBatchService._preflight` re-checks, for every item, right before it is attempted:

1. a live provider and a sender address are configured;
2. the package is still `approved` (not rejected since being added to the batch);
3. the package was never already sent (any batch, ever - the database-level guarantee below);
4. an accepted `Contact` still exists for it, is not `do_not_contact`, and has an e-mail channel;
5. a CV reference still exists;
6. the package is not stale (its own evidence, OR the underlying qualification, changed since it
   was prepared - see "Staleness reaches send time" below).

A failure at any of these EXCLUDES the item (`SendBatchItemStatus.EXCLUDED`, with a precise
`failure_reason`) - it is never silently skipped, and the rest of the batch is never blocked by
one bad item ("13 sent, 2 excluded" is reported exactly, never rounded up to "15 sent").

## The database-level idempotence guarantee

`uq_send_batch_items_sent_once` (migration `0013`) is a partial UNIQUE INDEX: at most one
`SendBatchItem` can ever be `sent` for a given `application_package_id`, across every batch it was
ever part of, enforced by the database itself - not by application logic that a bug or a race
could bypass. `SendBatchService.create()` also refuses, at creation time, to add a
package already sent to a NEW batch, so the common case is caught even earlier, with a clear
error rather than a silent no-op.

A `failed` item can be retried by calling `execute` again on the same batch: `pending`/`failed`
items are (re-)attempted, `sent`/`excluded` items are never touched again. Each attempt is claimed
atomically first (`claim_item_sending`, a conditional `UPDATE`), so two concurrent `execute` calls
on the same batch cannot both attempt the same item.

### Closing the "Google accepted it but we never heard back" gap

A `SendBatchItem.message_id` is generated **once**, deterministically
(`make_msgid(idstring=f"batch-{batch_id}-item-{item.id}", ...)`), the first time an item is
attempted, and stored immediately - before the network call, so it survives a crash mid-attempt.
Every later retry of that SAME item reuses this exact id (never a fresh, random one): it is
threaded through `OutgoingEmail.message_id` (additive field, step 10) into the `Message-ID`
header `build_message` writes, so the SAME logical send always carries the SAME id across
attempts.

Before a RETRY (never on a first attempt), `SendBatchService` checks whether the configured live
provider satisfies `IdempotentMailProvider` (a `@runtime_checkable` `Protocol` with one method,
`find_existing(message_id) -> SentMessage | None`, in `app.integrations.mail.ports` - `GmailClient`
implements it; the dry-run provider and any other plain `MailProvider` simply do not, and such a
provider's retries behave exactly as before this fix). When it does, it asks "was this exact
message already sent?" before calling `send()` again:

- found -> the item is marked `sent` using the found provider ids, and `send()` is never called
  a second time - closing the double-send window even when Google accepted the first attempt but
  the response never reached this process;
- nothing found, or the lookup itself fails (network down, provider error) -> falls through to a
  normal retry, with the same message id. A lookup failure is never treated as proof either way.

**Residual limitation, now smaller, not zero**: Gmail's search index has its own short
propagation delay, so a retry fired immediately after the failure could in principle still miss a
message search hasn't indexed yet. In practice this is not a live risk here: a `failed` item is
only ever retried by a NEW, explicit `execute` call (never automatically within the same one), so
real delay separates any two attempts already. This is a best-effort mitigation
(see `app.integrations.mail.ports.IdempotentMailProvider`'s own docstring), not a cryptographic
guarantee - the database-level unique index above remains the primary, always-correct protection.

## Staleness reaches send time

Step 9's `ApplicationPackageService.is_stale` originally only compared the package's OWN
fingerprint (its company facts, its accepted contact, its GitHub evidence) against a fresh
recomputation. Step 10 found and fixed a real gap while adding its own `stale` precondition: a
Candidate Brain change (a new Skill, say) can make the underlying `Qualification` row's LIVE
re-check fail (`PersonalizationBrief.qualification.stale`) without yet producing a NEW
`Qualification` row - so the package's stored `inputs_fingerprint` (built from that unchanged
row's own fingerprint value) looked identical to a fresh recomputation, hiding real staleness.
`is_stale` now also returns `True` whenever the underlying qualification itself is live-stale,
independently of whether a superseding `Qualification` row exists yet. This is additive to step
9's own logic, not a redesign of it.

## Gmail provider (`app/integrations/gmail/`)

`GmailClient` implements the EXISTING `app.integrations.mail.ports.MailProvider` protocol
(`send(BuiltMessage) -> SentMessage`) - no new send-port was needed, and `SendGuard`/`MailSender`
(step 1, unchanged) need no change to use it. `GmailOAuth` handles the "installed application"
OAuth 2.0 flow (loopback redirect, `gmail.send` scope only - send, never read, per the design
already fixed at step 1). Only the refresh token, client id and client secret live in
`SecretStore` (`gmail_refresh_token`, `gmail_client_id`, `gmail_client_secret`); an access token
is exchanged fresh for every call and kept in memory only - never persisted, never logged, never
returned by any API route.

`SendMode.AUTO` (previously refused by the configuration) is now available: a real send in
`auto` mode needs `approved=True`, which `SendBatchService` only ever passes after its own
preflight succeeds - and, unlike `manual` mode, no recipient allow-list, since a real
professional contact's address cannot be pre-enumerated the way a bootstrapping test address can
(the explicit two-layer approval IS the control here).

Verified against the real API: never in pytest. Two manual, non-pytest scripts exist:
- `scripts/manual/gmail_oauth_setup.py` - one-time, needs a human and a browser (Google's consent
  screen cannot be automated), stores the refresh token.
- `scripts/manual/gmail_smoke_test.py` - sends one real, synthetic e-mail to an address YOU give
  explicitly on the command line, through the exact same `SendGuard`/`MailSender` path a real
  batch send uses.

## Audit (Part 10)

New event types: `send_batch.created`, `send_batch.approved`, `send_batch.send_requested` (one
per item, right before it is attempted), `send_batch.completed`, `send_batch.partially_failed`.
`send.sent`/`send.failed`/`send.blocked`/`send.approved`/`send.dry_run` (step 1, unchanged) are
reused as-is, recorded by `MailSender` itself - never duplicated. Every batch-level event carries
the batch's `correlation_id` (added to `AuditLog.ALLOWED_DETAIL_KEYS`, along with `batch_id` and
`package_id`); never a token, a secret, a recipient address, or message content.

## API

| Route | Purpose |
| --- | --- |
| `GET /api/applications/ready-to-send` | approved packages never yet sent - what to pick from |
| `POST /api/send-batches` | create (`{application_package_ids: [...], idempotency_key?}`) |
| `GET /api/send-batches/{id}` | batch + every item's status |
| `POST /api/send-batches/{id}/approve` | layer 2 approval |
| `POST /api/send-batches/{id}/execute` | the ONE action that can cause a real send - explicit, never automatic |
| `POST /api/applications/{id}/send` | convenience: batch of one, in one call |

No cron, no background worker, nothing triggered by qualification or by preparing a package:
`execute` only ever runs when this endpoint is explicitly called.

## Toward the future learning system (Part 13/14)

Nothing here computes a score, a ranking, or a "best candidature". What it DOES preserve, per
send, for later analysis: which `ApplicationPackage` (and, through it, which `Target` /
`Qualification` / `RequirementMatch` / `CompanyResearchFact` / GitHub evidence / accepted
`Contact`) was sent, when, via which provider message id and thread id, and whether it succeeded,
failed, or was excluded and why. This is exactly the substrate a later, separate learning step
would need to correlate "what was sent" with "what happened next" (replies, interviews, offers -
none of which are tracked yet, deliberately: that is step 11's `replied`/`interviewed`/`rejected`/
`offer` states, not built here) - without this step inventing any outcome vocabulary itself, and
without ever rewriting a Candidate Brain fact to fit an observed pattern.

## Limits (deliberate)

- `find_existing` is best-effort, not a guarantee (Gmail search index propagation delay) - see
  "Closing the ... gap" above; the database-level unique index remains the primary protection.
- No CV or email-address is ever generated or guessed: only an already-ingested `DocumentIngestion`
  and an already-accepted `Contact`'s stored e-mail channel are ever used.
- Only the original CV is attached; no automatic "join a GitHub project's file" attachment exists
  (GitHub evidence stays a URL/text reference in the draft body, never a downloaded, re-attached
  file - fetching and re-emailing arbitrary GitHub content was judged out of this step's scope).
- `SendBatch` membership is fixed at creation; there is no route to remove one item from an
  existing `draft` batch (create a new batch instead).
- No thread-based reply tracking yet (`thread_id` is recorded, unused beyond that) - step 11.
