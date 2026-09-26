"""Start the API bound to the loopback interface only.

Usage: python scripts/run_api.py [--port 8000] [--reload]

The host is fixed to 127.0.0.1 on purpose: the API exposes personal data and, later, can send
e-mail, so it must not be reachable from the network.
"""

import argparse

import uvicorn

from app.core.config import get_settings

LOOPBACK_HOST = "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Career Agent API (127.0.0.1 only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if settings.api_token is None:
        print("Warning: API_TOKEN is not set; every /api/* request will be refused (503).")
        print("Create one with: python scripts/manage_secrets.py init-api-token")
    print(f"Send mode: {settings.send_mode.value}")
    uvicorn.run("app.main:app", host=LOOPBACK_HOST, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
