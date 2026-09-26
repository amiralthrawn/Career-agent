# Mail architecture (step 1: ports, MIME, dry-run; Gmail comes later)

Nothing in this step talks to Gmail or to the network. It fixes the contracts so that the Gmail
provider can be added later without touching the rest.

```text
draft (later) -> OutgoingEmail -> SendGuard -> audit -> build_message (MIME)
                                                        |
                          dry_run: DryRunMailProvider -> data/private/outbox/<name>.eml
                          manual : MailProvider (Gmail, step 6) -> provider ids
```

| Piece | Where | Role |
| --- | --- | --- |
| `OutgoingEmail`, `AttachmentRef`, `BuiltMessage`, `SentMessage`, `MailProvider` | `app/integrations/mail/ports.py` | Contracts. A provider only *transports* an already validated message. |
| `build_message` | `app/integrations/mail/mime.py` | RFC 5322 message with attachments, standard library only. |
| `DryRunMailProvider` | `app/integrations/mail/dry_run.py` | Writes a `.eml`, never overwrites, name without personal data. |
| `SendGuard` | `app/services/send_guard.py` | The only decision point (see `security.md`). |
| `MailSender` | `app/services/mail_sender.py` | guard -> audit -> provider, fail closed. |

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

## Gmail provider (step 6, not implemented)

Design decisions already taken:

- **API and auth:** Gmail REST API, OAuth 2.0 "installed application" flow with a loopback
  redirect. No Gmail password in the application, ever.
- **Scope:** `https://www.googleapis.com/auth/gmail.send` only (send, no read). The `send`
  response returns the message id and thread id, which is enough for the first tracking need.
  Reading replies (step 7) will need a separate, incremental grant.
- **Tokens:** only the refresh token is stored, through `SecretStore` (`gmail_refresh_token`,
  and `gmail_client_secret`). Access tokens stay in memory.
- **Provider contract:** `send(BuiltMessage) -> SentMessage(provider_message_id, thread_id)`,
  called only by `MailSender` after `SendGuard` and the audit.
- **Prerequisites on your side (not needed before step 6):** a Google Cloud project with the
  Gmail API enabled, an OAuth consent screen, and a "Desktop app" OAuth client. While the app is
  in "Testing" status the refresh token expires after 7 days; publishing it "In production" for
  personal use avoids that (Google then shows an "unverified app" warning).
- **First real sends:** `SEND_MODE=manual`, `SEND_ALLOWED_RECIPIENTS` limited to your own
  address, one explicit approval per message.
