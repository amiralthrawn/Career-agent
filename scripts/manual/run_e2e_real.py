"""Manual, controlled run of the REAL end-to-end pipeline. NOT part of the pytest suite.

Runs, against the REAL configured database, exactly the same services the API/CLI use:

    SearchProfile -> Perplexity (if enabled) -> Target -> Qualification -> Requirements
    -> PersonalizationBrief -> LLM draft (if enabled) -> [human review] -> ApplicationPackage
    -> SendBatch -> SendGuard -> Gmail (only if SEND_MODE=manual/auto AND --allow-real-send)

Every external capability is used ONLY if the operator already enabled it through the SAME
mechanism as the API/CLI (`RESEARCH_ENABLED`/`LLM_ENABLED`/`SEND_MODE`, `SecretStore`) - this
script never bypasses `default_web_search_provider`/`default_llm_client`/`default_gmail_provider`,
and never invents a shortcut around `SendGuard`.

**Safety.** The default send mode stays whatever `SEND_MODE` already is (normally `dry_run`,
which writes a local `.eml` and reaches no one). `disabled` and `dry_run` can NEVER reach a real
provider (see `app.services.send_guard.SendGuard.evaluate`); `manual` (an allow-listed bootstrap
address) and `auto` (a fully human-approved batch) BOTH can. If `SEND_MODE` is `manual` or `auto`,
this script REFUSES to reach the send step unless `--allow-real-send` is ALSO passed - a
deliberate, redundant confirmation on top of the existing `SendGuard` gate - and then asks for one
more typed confirmation at the terminal before calling `execute()`. Nothing here sends
automatically because a capability happens to be enabled: a human runs this script, on purpose,
one target at a time.

Usage (PowerShell), a safe dry run against an existing target:
    .venv\\Scripts\\python.exe scripts\\manual\\run_e2e_real.py --target 12

Or, to also try real sourcing (needs RESEARCH_ENABLED=true, PERPLEXITY_PRESET, the secret):
    .venv\\Scripts\\python.exe scripts\\manual\\run_e2e_real.py --profile 1 --mode offers
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import get_settings  # noqa: E402
from app.core.database import get_session_factory  # noqa: E402
from app.core.secrets import OPENROUTER_API_KEY, SecretStoreError, get_secret_store  # noqa: E402
from app.integrations.gmail.client import default_gmail_provider  # noqa: E402
from app.integrations.llm.openrouter import OpenRouterClient  # noqa: E402
from app.integrations.llm.ports import LLMClient  # noqa: E402
from app.integrations.sourcing.ports import SourcingProviders  # noqa: E402
from app.models.enums import SourcingMode  # noqa: E402
from app.schemas.application_package import ApplicationPrepareRequest  # noqa: E402
from app.schemas.sourcing import SearchRunCreate  # noqa: E402
from app.services.application_package import ApplicationPackageService  # noqa: E402
from app.services.audit import AuditLog  # noqa: E402
from app.services.mail_sender import MailSender  # noqa: E402
from app.services.qualification import QualificationService  # noqa: E402
from app.services.requirements import RequirementService  # noqa: E402
from app.services.send_batch import SendBatchService  # noqa: E402
from app.services.sourcing import SourcingService, default_web_search_provider  # noqa: E402


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


def _web_providers() -> SourcingProviders:
    settings = get_settings()
    try:
        store = get_secret_store()
    except SecretStoreError:
        return SourcingProviders()
    provider = default_web_search_provider(settings, store)
    return SourcingProviders(web={"perplexity": provider} if provider else {})


def _find_target(args: argparse.Namespace, session: object) -> int | None:
    from sqlalchemy.orm import Session as SessionType

    assert isinstance(session, SessionType)
    providers = _web_providers()
    if not providers.web:
        print(
            "No web-search provider configured (RESEARCH_ENABLED/PERPLEXITY_PRESET/"
            "perplexity_api_key). Pass --target <id> to use an existing target instead.",
            file=sys.stderr,
        )
        return None
    service = SourcingService(session, providers)
    data = SearchRunCreate(
        mode=SourcingMode(args.mode),
        provider=args.provider,
        profile_id=args.profile,
        max_results=args.max_results,
    )
    run = service.run(data, actor="cli")
    print(f"Search run #{run.id}: {run.status.value}, {run.targets_created} target(s) created")
    items = [item for item in service.items(run.id) if item.target_id is not None]
    if not items:
        print("No target was created by this run.")
        return None
    return items[0].target_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the real pipeline once, end to end.")
    parser.add_argument(
        "--profile", type=int, default=None, help="SearchProfile id (default: active)"
    )
    parser.add_argument("--mode", choices=["offers", "companies"], default="offers")
    parser.add_argument("--provider", default="perplexity")
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument(
        "--target", type=int, default=None, help="Use this Target instead of sourcing"
    )
    parser.add_argument(
        "--allow-real-send",
        action="store_true",
        help=(
            "Required, in addition to SEND_MODE=manual/auto, before this script will call "
            "execute()."
        ),
    )
    args = parser.parse_args()

    settings = get_settings()
    print(
        f"SEND_MODE={settings.send_mode.value}  LLM_ENABLED={settings.llm_enabled}  "
        f"RESEARCH_ENABLED={settings.research_enabled}"
    )
    # Both `manual` (an allow-listed real send) and `auto` (a fully-approved real send) can reach
    # a real provider (see `app.services.send_guard.SendGuard.evaluate`) - `disabled`/`dry_run`
    # cannot. Guard on both, never on `auto` alone.
    real_send = settings.send_mode.value in ("manual", "auto")
    if real_send and not args.allow_real_send:
        print(
            f"Refusing: SEND_MODE={settings.send_mode.value} but --allow-real-send was not "
            "passed. Pass it explicitly, or set SEND_MODE=dry_run to rehearse safely.",
            file=sys.stderr,
        )
        return 1

    with get_session_factory()() as session:
        target_id = args.target
        if target_id is None:
            target_id = _find_target(args, session)
            if target_id is None:
                return 1
        print(f"Using target #{target_id}")

        report = RequirementService(session).extract(target_id, actor="cli")
        print(f"Requirements: {report.outcome} (found={report.found}, created={report.created})")
        # Extraction changes the qualification's own inputs (3b): re-qualify so `prepare()` below
        # never refuses on a now-stale qualification (the SAME rule the API/CLI already enforce).
        requalified = QualificationService(session).qualify(target_id, args.profile)
        print(f"Qualification: {requalified.qualification.status.value}")

        llm = _llm_client()
        print(f"LLM client: {'configured' if llm else 'NOT configured (package will stay draft)'}")
        package = ApplicationPackageService(session, llm, None, settings.github_username).prepare(
            target_id, ApplicationPrepareRequest(), actor="cli"
        )
        print(f"Package #{package.id}: status={package.status.value}")
        if package.warnings:
            for warning in package.warnings:
                print(f"  warning: [{warning['code']}] {warning['text']}")
        if package.status.value != "pending_validation":
            print("No draft was generated (no LLM configured): stopping here for human review.")
            return 0

        print(
            "\n--- Draft is ready for human review (career-agent drafts show <id>, "
            "career-agent applications show <id>) ---"
        )
        if real_send:
            answer = input(
                f"\nSEND_MODE={settings.send_mode.value}: approving now WILL send a real "
                "e-mail via Gmail if you continue to the send step. Type 'SEND' to proceed, "
                "anything else to stop: "
            )
            if answer.strip() != "SEND":
                print("Stopped before approval: nothing was approved or sent.")
                return 0

        service = ApplicationPackageService(session, None, None, settings.github_username)
        approved = service.decide(package.id, approve=True, actor="cli")
        print(f"Package #{approved.id}: status={approved.status.value}")

        live_provider = None
        if real_send:
            try:
                store = get_secret_store()
                live_provider = default_gmail_provider(settings, store)
            except SecretStoreError:
                live_provider = None
        mail_sender = MailSender(
            settings, AuditLog(session), live_provider=live_provider, actor="cli"
        )
        batches = SendBatchService(session, settings, mail_sender, service)
        batch = batches.create([approved.id], actor="cli")
        batches.approve(batch.id, actor="cli")
        batches.execute(batch.id, actor="cli")
        batch = batches.get(batch.id)
        print(f"\nSendBatch #{batch.id}: status={batch.status.value}")
        for item in batches.items(batch.id):
            print(f"  item #{item.id}: {item.status.value} (reason={item.failure_reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
