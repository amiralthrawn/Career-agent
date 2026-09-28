"""`career-agent drafts ...` - list/show/generate application drafts.

The LLM is a GENERATOR only, never a source of truth (see `app.services.draft_generation`), and
comes from the SAME injectable, off-by-default capability as the API
(`app.api.drafts.get_llm_client`): `None` unless `LLM_ENABLED=true`, `OPENROUTER_MODEL` is set and
the `openrouter_api_key` secret is stored - generation is refused before anything runs otherwise.
"""

import argparse
import sys

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.core.secrets import OPENROUTER_API_KEY, SecretStoreError, get_secret_store
from app.integrations.llm.openrouter import OpenRouterClient
from app.integrations.llm.ports import LLMClient
from app.models import ApplicationDraft
from app.models.enums import DraftKind, DraftStatus
from app.schemas.drafts import DraftGenerateRequest
from app.services.draft_generation import DraftService


def _llm_client() -> LLMClient | None:
    settings = get_settings()
    if not settings.llm_enabled or not settings.openrouter_model:
        return None
    try:
        store = get_secret_store()
        if not store.exists(OPENROUTER_API_KEY):
            return None
    except SecretStoreError:
        return None
    return OpenRouterClient(store, settings.openrouter_model)


def _line(draft: ApplicationDraft) -> str:
    return (
        f"#{draft.id:<5} target=#{draft.target_id:<5} {draft.status.value:<10} "
        f"{draft.model:<24.24} {draft.created_at:%Y-%m-%d}"
    )


def _print_draft(draft: ApplicationDraft) -> None:
    print(f"Draft #{draft.id} - target #{draft.target_id} - {draft.status.value}")
    print(f"Model: {draft.model}")
    print(f"Subject: {draft.subject}")
    print(f"\n{draft.body}\n")
    if draft.warnings:
        print("Warnings:")
        for warning in draft.warnings:
            print(f"  - [{warning['code']}] {warning['text']}")


def _list(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        status = DraftStatus(args.status) if args.status else None
        drafts = DraftService(session, None).list(status=status, limit=args.limit)
        if not drafts:
            print("(none)")
        for draft in drafts:
            print(_line(draft))
    return 0


def _show(args: argparse.Namespace) -> int:
    with get_session_factory()() as session:
        _print_draft(DraftService(session, None).get(args.draft_id))
    return 0


def _generate(args: argparse.Namespace) -> int:
    llm = _llm_client()
    if llm is None:
        print(
            "error: no LLM client is configured (set LLM_ENABLED=true and OPENROUTER_MODEL, "
            "and store the openrouter_api_key secret: python scripts/manage_secrets.py set "
            "openrouter_api_key)",
            file=sys.stderr,
        )
        return 1
    with get_session_factory()() as session:
        draft = DraftService(session, llm).generate(
            args.target_id, DraftGenerateRequest(kind=DraftKind.APPLICATION_EMAIL), actor="cli"
        )
        _print_draft(draft)
    return 0


def add_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    drafts = subparsers.add_parser("drafts", help="List/show/generate application drafts")
    sub = drafts.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list", help="List drafts")
    list_cmd.add_argument("--status", choices=[s.value for s in DraftStatus], default=None)
    list_cmd.add_argument("--limit", type=int, default=50)
    list_cmd.set_defaults(handler=_list)

    show = sub.add_parser("show", help="Show one draft")
    show.add_argument("draft_id", type=int)
    show.set_defaults(handler=_show)

    generate = sub.add_parser("generate", help="Generate a draft for a target (needs LLM_ENABLED)")
    generate.add_argument("target_id", type=int)
    generate.set_defaults(handler=_generate)
