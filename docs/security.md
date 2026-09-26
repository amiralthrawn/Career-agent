# Local security (step 1)

Career Agent handles personal data and, later, can send e-mail from a real mailbox. This page
describes the protections that exist **before** any real sending is possible.

## 1. Local API

- Every `/api/*` route requires `Authorization: Bearer <API_TOKEN>`, reads included (they expose
  the Candidate Brain). `/health` is public. `/docs` and `/openapi.json` are served but every
  call they make still needs the token (use the "Authorize" button with the token).
- The token is read from `.env` (git-ignored). It has at least 32 characters. Generate it
  without displaying it:

  ```powershell
  python scripts/manage_secrets.py init-api-token          # writes API_TOKEN into .env
  python scripts/manage_secrets.py init-api-token --rotate # replaces it
  ```

- **Fail closed.** With no `API_TOKEN`, every `/api/*` request answers `503`. A wrong, missing
  or malformed token answers `401`. The comparison is constant-time. The token is only accepted
  in the `Authorization` header (never in the URL).
- **Host header.** Only `ALLOWED_HOSTS` (default `127.0.0.1,localhost`) are served; any other
  `Host` gets `400`. This protects against DNS rebinding, where a web page tricks the browser
  into talking to the local API under another name.
- **CORS** is closed unless `CORS_ORIGINS` lists an origin.
- **Bind address.** Start the server with `python scripts/run_api.py`: it listens on
  `127.0.0.1` only (there is no option to change the host).
- The token never appears in logs, responses, `repr(settings)` or tests (tests generate a
  throw-away token per run and never read the real `.env`).

## 2. Secrets

`SecretStore` (`app/core/secrets.py`) is the only way the application stores secrets. The
implementation uses the Windows Credential Manager through `keyring`, under the service name
`career-agent`. There is **no fallback to a plain-text file**: if no secure backend is available
(or a plain-text one is configured) the store refuses to work.

Known names: `gmail_refresh_token`, `gmail_client_secret`, `openrouter_api_key`. Manage them
without ever displaying them:

```powershell
python scripts/manage_secrets.py status                  # names and set / not set only
python scripts/manage_secrets.py set openrouter_api_key  # value typed hidden, not an argument
python scripts/manage_secrets.py delete openrouter_api_key
```

A secret is limited to 1000 characters (Windows Credential Manager limit). Error messages never
contain a value. Only the OAuth *refresh token* will be stored; access tokens stay in memory.

## 3. Sending modes

`SEND_MODE` in `.env`:

| Mode | Behaviour |
| --- | --- |
| `disabled` (default) | Nothing is sent and nothing is written. |
| `dry_run` | The message is built and written as a `.eml` file in `data/private/outbox/`. No network call. |
| `manual` | A real send requires an **approved** draft **and** a recipient listed in `SEND_ALLOWED_RECIPIENTS`. No live provider exists yet, so nothing can be sent. |
| `auto` | Refused by the configuration until a later step. |

`SendGuard` is the single decision point (a pure function of the configuration and the
request); `MailSender` is the only code that can reach a provider, and it always goes
guard -> audit -> provider. Recipients must be one plain address (no list, no display name, no
line break). The allow-list contains exact addresses, no wildcards.

## 4. Audit trail

`audit_events` records what the application **attempted or authorised**: start-up (with the
send mode), secret changes (name only) and every send decision.

- It cannot hold sensitive data: event types are a closed set, `details` only accepts the keys
  `reason`, `mode`, `recipient_domain`, `attachments`, `bytes`, `name`, `app_version`, values
  are short scalars, and a value that looks like an address or contains a line break is
  refused. Mail content, subjects, attachment names or contents, secrets, the API token and
  profile data are never stored. Only the recipient's **domain** is kept.
- It is append-only at three levels: the repository has no update/delete, the ORM refuses them,
  and database triggers reject `UPDATE` and `DELETE` (and `TRUNCATE` on PostgreSQL). A database
  owner could still drop the triggers: that is an administrative action outside the
  application.
- **No audit, no send:** if the decision cannot be recorded, nothing is sent or written. When
  sending is enabled, the application refuses to start if the start-up event cannot be
  recorded.
- Secret-management commands record their change on a best-effort basis (the vault operation
  has already happened) and warn when the audit is unavailable.

## 5. Private data

`data/private/` and `.env` are git-ignored, so the CV, the outbox `.eml` files (which contain
the CV) and the review reports cannot be committed. Attachments can only come from
`data/private/` (no `..`, no absolute path, no link pointing outside), must be `.docx` or
`.pdf`, and are limited to 10 MB each and 5 per message.

## 6. Known limits

- Authentication is a single shared token for a single local user.
- The audit records intent and decisions, not the provider's final delivery state.
- The Windows Credential Manager protects secrets for the current Windows user; it does not
  protect against malware running as that user.
