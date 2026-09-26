"""Manage local secrets without ever displaying them.

    python scripts/manage_secrets.py status
    python scripts/manage_secrets.py set <name>          # value typed hidden, never an argument
    python scripts/manage_secrets.py delete <name>
    python scripts/manage_secrets.py init-api-token [--rotate]

Secrets live in the operating system credential vault (Windows Credential Manager), except
the local API token, which is written to `.env` (git-ignored). Values are never printed,
logged or passed on the command line. Changes are recorded in the audit trail (names only).
"""

import argparse
import getpass
import os
import secrets
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from app.core.config import PROJECT_ROOT, get_settings
from app.core.database import get_session_factory
from app.core.secrets import (
    KNOWN_SECRETS,
    SecretStore,
    SecretStoreError,
    get_secret_store,
    validate_name,
)
from app.models.audit import AuditEventType
from app.services.audit import AuditLog

API_TOKEN_KEY = "API_TOKEN"


def _audit(event_type: AuditEventType, name: str) -> None:
    """Best effort: the vault operation has already happened, so a missing audit is reported."""
    try:
        get_settings().require_database_url()
        with get_session_factory()() as session:
            AuditLog(session).record(
                event_type, actor="cli", subject=f"secret:{name}", details={"name": name}
            )
    except Exception:
        print("Warning: the change could not be recorded in the audit trail.", file=sys.stderr)


def write_api_token(env_file: Path, *, rotate: bool) -> bool:
    """Write a fresh API_TOKEN into `env_file`. Returns False if one exists and not `rotate`."""
    if not env_file.exists():
        example = PROJECT_ROOT / ".env.example"
        env_file.write_text(
            example.read_text(encoding="utf-8") if example.exists() else "", encoding="utf-8"
        )
    lines = env_file.read_text(encoding="utf-8").splitlines()
    index = next((i for i, line in enumerate(lines) if line.startswith(f"{API_TOKEN_KEY}=")), None)
    if index is not None and lines[index].split("=", 1)[1].strip() and not rotate:
        return False
    line = f"{API_TOKEN_KEY}={secrets.token_urlsafe(32)}"
    if index is None:
        lines.append(line)
    else:
        lines[index] = line
    temporary = env_file.with_name(env_file.name + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, env_file)
    return True


def main(
    argv: Sequence[str] | None = None,
    *,
    store: SecretStore | None = None,
    env_file: Path | None = None,
    read_secret: Callable[[str], str] = getpass.getpass,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    set_parser = commands.add_parser("set")
    set_parser.add_argument("name")
    delete_parser = commands.add_parser("delete")
    delete_parser.add_argument("name")
    token_parser = commands.add_parser("init-api-token")
    token_parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.command == "init-api-token":
            target = env_file or PROJECT_ROOT / ".env"
            if write_api_token(target, rotate=args.rotate):
                print("API_TOKEN written to .env (not displayed). Open .env to read it.")
                return 0
            print("API_TOKEN already exists in .env; use --rotate to replace it.")
            return 1

        secret_store = store or get_secret_store()
        if args.command == "status":
            for name in KNOWN_SECRETS:
                print(f"{name}: {'set' if secret_store.exists(name) else 'not set'}")
            token_set = get_settings().api_token is not None
            print(f"API_TOKEN (.env): {'set' if token_set else 'not set'}")
            return 0

        name = validate_name(args.name)
        if args.command == "set":
            value = read_secret(f"Value for {name} (hidden): ")
            secret_store.set(name, value)
            _audit(AuditEventType.SECRET_SET, name)
            print(f"{name}: stored in the credential vault.")
        else:
            secret_store.delete(name)
            _audit(AuditEventType.SECRET_DELETED, name)
            print(f"{name}: deleted.")
        return 0
    except SecretStoreError as error:
        print(f"Error: {error}", file=sys.stderr)  # messages never contain secret values
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
