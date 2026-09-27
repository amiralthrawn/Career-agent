"""One-time, manual Gmail OAuth setup (step 10). NOT part of the pytest suite.

This is the ONE step that genuinely needs a human and a browser: Google requires an explicit
"Allow" click on a consent screen before any refresh token can exist. Nothing here is, or could
be, automated in CI or in pytest.

Prerequisites (your own Google Cloud project, not automated here):
    1. Enable the Gmail API.
    2. Configure an OAuth consent screen (Testing is fine for personal use).
    3. Create an OAuth client of type "Desktop app"; note its Client ID and Client Secret.
    4. Store them once:
        python scripts/manage_secrets.py set gmail_client_id
        python scripts/manage_secrets.py set gmail_client_secret

Usage (PowerShell):
    .venv\\Scripts\\python.exe scripts\\manual\\gmail_oauth_setup.py

The script prints a URL; open it, sign in, click Allow, and you land on a
"This site can't be reached" page (expected - nothing is actually listening on the loopback
port). Copy the FULL address bar URL and paste it back when prompted; the script extracts the
`code` parameter, exchanges it for a refresh token, and stores it as `gmail_refresh_token`.

Never prints a token, a client secret, or an authorization code beyond what you pasted yourself.
"""

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from app.core.secrets import (  # noqa: E402
    GMAIL_CLIENT_ID,
    GMAIL_REFRESH_TOKEN,
    SecretStoreError,
    get_secret_store,
)
from app.integrations.gmail.oauth import GmailOAuth, authorize_url  # noqa: E402
from app.integrations.gmail.ports import GmailError  # noqa: E402

REDIRECT_URI = "http://localhost:8080/"


def main() -> int:
    store = get_secret_store()
    try:
        client_id = store.get(GMAIL_CLIENT_ID)
    except SecretStoreError as error:
        print(f"Cannot read gmail_client_id from the secret store: {error}")
        return 1
    if not client_id:
        print(
            "gmail_client_id is not set. Run: python scripts/manage_secrets.py set gmail_client_id"
        )
        return 1

    print("Open this URL, sign in, and click Allow:\n")
    print(authorize_url(client_id, REDIRECT_URI))
    print(f"\nAfter you click Allow, the browser lands on {REDIRECT_URI}... - that page will not")
    print("load; that is expected. Copy the FULL address bar URL from that failed page.")
    pasted = input("\nPaste the full redirected URL here: ").strip()

    query = parse_qs(urlparse(pasted).query)
    codes = query.get("code")
    if not codes:
        print("No 'code' parameter found in the pasted URL.")
        return 1

    oauth = GmailOAuth(store)
    try:
        refresh_token = oauth.exchange_code(codes[0], REDIRECT_URI)
    except GmailError as error:
        print(f"Token exchange failed: {error.code.value}")
        return 1

    store.set(GMAIL_REFRESH_TOKEN, refresh_token)
    print("\nStored gmail_refresh_token. Setup complete - the token itself was never printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
