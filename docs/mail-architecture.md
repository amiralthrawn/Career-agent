# Mail architecture (step 1: ports, MIME, dry-run; Gmail implemented at step 10)

Step 1 fixed the contracts so the Gmail provider could be added later without touching the rest;
step 10 did exactly that - see the "Gmail provider" section below and send_batches.md.

```text
draft -> OutgoingEmail -> SendGuard -> audit -> build_message (MIME)
                                               |
                 dry_run: DryRunMailProvider -> data/private/outbox/<name>.eml
                 auto/manual: GmailClient (step 10) -> provider ids
```

| Piece | Where | Role |
| --- | --- | --- |
| `OutgoingEmail`, `AttachmentRef`, `BuiltMessage`, `SentMessage`, `MailProvider` | `app/integrations/mail/ports.py` | Contracts. A provider only *transports* an already validated message. |
| `build_message` | `app/integrations/mail/mime.py` | RFC 5322 message with attachments, standard library only. |
| `DryRunMailProvider` | `app/integrations/mail/dry_run.py` | Writes a `.eml`, never overwrites, name without personal data. |
| `SendGuard` | `app/services/send_guard.py` | The only decision point (see `security.md`). |
| `MailSender` | `app/services/mail_sender.py` | guard -> audit -> provider, fail closed. |
| `GmailClient`, `GmailOAuth` (step 10) | `app/integrations/gmail/` | The real provider: implements `MailProvider` directly, OAuth token management. |
| `SendBatchService` (step 10) | `app/services/send_batch.py` | Controlled batch sending on top of `MailSender` - see send_batches.md. |

## Attachments

The original CV is attached **unchanged**: the file is read read-only from `data/private/`,
embedded byte for byte and never rewritten or moved (tests compare the decoded attachment with
the original file). Constraints: path relative to `data/private/`, no `..`, no absolute path, no
link escaping the directory, `.docx` or `.pdf` only, 10 MB max, 5 per message. The display name
defaults to the file name and can be overridden.

Try it locally (no network, nothing sent):

```powershell
# in .env: SEND_MODE=dry_run, MAIL_FROM=<your address>, DATABASE_URL=<configured database>
python scripts/dry_run_mail.py --to someone@example.invalid --attach documents/<your-cv>.docx
```

The terminal shows only the decision and the outbox location; open the `.eml` from
`data/private/outbox/` with a mail client to check it.

## Gmail provider (implemented at step 10)

The design decided here at step 1 was built exactly as planned, in `app/integrations/gmail/`:

- **API and auth:** Gmail REST API, OAuth 2.0 "installed application" flow with a loopback
  redirect. No Gmail password in the application, ever.
- **Scope:** `https://www.googleapis.com/auth/gmail.send` only (send, no read). The `send`
  response returns the message id and thread id, which is enough for the first tracking need.
  Reading replies (a later step) will need a separate, incremental grant.
- **Tokens:** the refresh token, client id and client secret all live in `SecretStore`
  (`gmail_refresh_token`, `gmail_client_id`, `gmail_client_secret`). Access tokens stay in memory
  only, exchanged fresh for every call.
- **Provider contract:** `send(BuiltMessage) -> SentMessage(provider_message_id, thread_id)`,
  called only by `MailSender` after `SendGuard` and the audit - `GmailClient` implements this
  EXISTING protocol directly, no new port was needed.
- **Prerequisites on your side:** a Google Cloud project with the Gmail API enabled, an OAuth
  consent screen, and a "Desktop app" OAuth client. While the app is in "Testing" status the
  refresh token expires after 7 days; publishing it "In production" for personal use avoids that
  (Google then shows an "unverified app" warning). One-time setup:
  `scripts/manual/gmail_oauth_setup.py` (needs a human and a browser, never automated).
- **Real sends:** step 10 unlocks `SEND_MODE=auto` for controlled batch sending, with a
  two-layer human approval replacing the `manual`-mode recipient allow-list - see
  send_batches.md. `manual` (allow-list, one message at a time) still exists unchanged, for
  bootstrapping a single test send.
