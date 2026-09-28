"""Application package routes (step 9): prepare, read, approve, reject, list ready-to-send.
Lifecycle tracking routes (step 11): history, manual entry, correction, filtering, follow-up.

`POST /applications/{id}/send` (step 10) is a convenience: it sends exactly ONE package through
the SAME `SendBatch` mechanism as a real batch (a batch of one, created/approved/executed in one
call) - never a second, parallel send path. See app.api.send_batches for the general batch API.

The LLM and GitHub clients come from the same injectable, off-by-default dependencies as steps
4/9: `None` unless explicitly enabled and configured, so nothing depends on OpenRouter or GitHub
in production yet - a package can still be PREPARED (evidence selected) without either, staying
`draft` until an LLM becomes available.

Step 11 enriches this SAME router rather than adding a second, parallel "applications" interface
- `ApplicationEvent` routes read/write through the target package's id, exactly like `approve`/
`reject`/`send` already do. No route here ever sends an e-mail or a follow-up automatically: a
follow-up is only ever a human-recorded `ApplicationEventType.FOLLOW_UP_SENT` event, or a
still-manual `POST /applications/{id}/send` the human triggers themselves.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.drafts import get_llm_client
from app.api.send_batches import Service as SendBatchServiceDep
from app.api.send_batches import read_send_batch
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.secrets import SecretStoreError, get_secret_store
from app.integrations.github.ports import GitHubProvider
from app.integrations.llm.ports import LLMClient
from app.models import ApplicationPackage
from app.models.enums import ApplicationEventType
from app.schemas.application_event import (
    ApplicationEventCorrect,
    ApplicationEventCreate,
    ApplicationEventRead,
)
from app.schemas.application_package import (
    ApplicationPackageRead,
    ApplicationPrepareRequest,
    read_application_package,
)
from app.schemas.send_batch import SendBatchRead
from app.services.application_package import ApplicationPackageService
from app.services.application_tracking import ApplicationTrackingService
from app.services.github_evidence import default_github_provider

router = APIRouter(prefix="/api", tags=["applications"])

DbSession = Annotated[Session, Depends(get_db)]


def get_github_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> GitHubProvider | None:
    """`None` by default: GitHub evidence is then simply absent, never blocking package prep."""
    try:
        store = get_secret_store()
    except SecretStoreError:
        return None
    return default_github_provider(settings, store)


LLM = Annotated[LLMClient | None, Depends(get_llm_client)]
GitHub = Annotated[GitHubProvider | None, Depends(get_github_provider)]


def get_service(
    session: DbSession,
    llm: LLM,
    github: GitHub,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ApplicationPackageService:
    return ApplicationPackageService(session, llm, github, settings.github_username)


Service = Annotated[ApplicationPackageService, Depends(get_service)]


def get_tracking_service(session: DbSession) -> ApplicationTrackingService:
    return ApplicationTrackingService(session)


Tracking = Annotated[ApplicationTrackingService, Depends(get_tracking_service)]


def _read(
    service: ApplicationPackageService, package: ApplicationPackage
) -> ApplicationPackageRead:
    return read_application_package(
        package, stale=service.is_stale(package), cv_source_uri=service.cv_source_uri(package)
    )


@router.post(
    "/applications/{target_id}/prepare", response_model=ApplicationPackageRead, status_code=201
)
def prepare_application(
    target_id: int, data: ApplicationPrepareRequest, service: Service
) -> ApplicationPackageRead:
    package = service.prepare(target_id, data, actor="api")
    return _read(service, package)


@router.get("/applications/ready-to-send", response_model=list[ApplicationPackageRead])
def list_ready_to_send(service: Service) -> Sequence[ApplicationPackageRead]:
    """Individually-approved packages (step 9) never yet sent (step 10) - what a human picks
    from to build a `SendBatch`. Registered before `/applications/{package_id}` so this literal
    path is never swallowed by that dynamic one."""
    return [_read(service, package) for package in service.list_ready_to_send()]


@router.get("/applications", response_model=list[ApplicationPackageRead])
def list_applications(
    service: Service,
    tracking: Tracking,
    event_type: ApplicationEventType | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Sequence[ApplicationPackageRead]:
    """No filter: every package. `event_type` (+ optional `since`/`until`): only packages with a
    CONFIRMED event of that type in range - e.g. `?event_type=rejected&since=2026-10-01` for
    every rejection recorded since October. Registered before `/applications/{package_id}`."""
    packages = tracking.list_packages(event_type=event_type, since=since, until=until)
    return [_read(service, package) for package in packages]


@router.get("/applications/needing-follow-up", response_model=list[ApplicationPackageRead])
def list_needing_follow_up(
    service: Service, tracking: Tracking, days: int = 14
) -> Sequence[ApplicationPackageRead]:
    """Sent (confirmed) at least `days` ago, with no confirmed response since - candidates for a
    human-decided follow-up. Never sends anything itself."""
    packages = tracking.needing_follow_up(days_since_sent=days)
    return [_read(service, package) for package in packages]


@router.get("/applications/{package_id}", response_model=ApplicationPackageRead)
def get_application(package_id: int, service: Service) -> ApplicationPackageRead:
    return _read(service, service.get(package_id))


@router.post("/applications/{package_id}/approve", response_model=ApplicationPackageRead)
def approve_application(package_id: int, service: Service) -> ApplicationPackageRead:
    package = service.decide(package_id, approve=True, actor="api")
    return _read(service, package)


@router.post("/applications/{package_id}/reject", response_model=ApplicationPackageRead)
def reject_application(package_id: int, service: Service) -> ApplicationPackageRead:
    package = service.decide(package_id, approve=False, actor="api")
    return _read(service, package)


@router.post("/applications/{package_id}/send", response_model=SendBatchRead)
def send_application(package_id: int, batches: SendBatchServiceDep) -> SendBatchRead:
    """Individual send = a `SendBatch` of exactly one, created, approved and executed in this
    one call. Same mechanism, same audit trail, same preconditions as a real batch - never a
    second send path."""
    batch = batches.create([package_id], actor="api")
    batches.approve(batch.id, actor="api")
    batches.execute(batch.id, actor="api")
    return read_send_batch(batches, batch.id)


# --- lifecycle tracking (step 11) ---------------------------------------------------------


@router.get("/applications/{package_id}/events", response_model=list[ApplicationEventRead])
def list_application_events(
    package_id: int, service: Service, tracking: Tracking
) -> Sequence[ApplicationEventRead]:
    service.get(package_id)  # 404 if unknown/not this candidate's, same check as every route here
    return [
        ApplicationEventRead.model_validate(event) for event in tracking.list_events(package_id)
    ]


@router.post(
    "/applications/{package_id}/events", response_model=ApplicationEventRead, status_code=201
)
def record_application_event(
    package_id: int, data: ApplicationEventCreate, service: Service, tracking: Tracking
) -> ApplicationEventRead:
    """Manual entry: a response, an interview, a rejection, an offer, a withdrawal, or a
    follow-up already sent BY THE HUMAN THEMSELVES - never triggered from here."""
    service.get(package_id)
    event = tracking.record_manual_event(
        package_id,
        data.event_type,
        data.occurred_at,
        status=data.status,
        note=data.note,
        reference=data.reference,
        actor="api",
    )
    return ApplicationEventRead.model_validate(event)


@router.post(
    "/applications/{package_id}/events/{event_id}/correct",
    response_model=ApplicationEventRead,
)
def correct_application_event(
    package_id: int,
    event_id: int,
    data: ApplicationEventCorrect,
    service: Service,
    tracking: Tracking,
) -> ApplicationEventRead:
    """Creates a NEW event referencing the one it corrects - the original is never edited or
    deleted. Also how an `uncertain` (e.g. Gmail-detected) event gets confirmed: correct it with
    only `status=found` and nothing else changed."""
    service.get(package_id)
    event = tracking.correct_event(
        event_id,
        event_type=data.event_type,
        occurred_at=data.occurred_at,
        status=data.status,
        note=data.note,
        reference=data.reference,
        actor="api",
    )
    return ApplicationEventRead.model_validate(event)
