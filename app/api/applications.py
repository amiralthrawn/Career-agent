"""Application package routes (step 9): prepare, read, approve, reject, list ready-to-send.

`POST /applications/{id}/send` (step 10) is a convenience: it sends exactly ONE package through
the SAME `SendBatch` mechanism as a real batch (a batch of one, created/approved/executed in one
call) - never a second, parallel send path. See app.api.send_batches for the general batch API.

The LLM and GitHub clients come from the same injectable, off-by-default dependencies as steps
4/9: `None` unless explicitly enabled and configured, so nothing depends on OpenRouter or GitHub
in production yet - a package can still be PREPARED (evidence selected) without either, staying
`draft` until an LLM becomes available.
"""

from collections.abc import Sequence
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
from app.schemas.application_package import (
    ApplicationPackageRead,
    ApplicationPrepareRequest,
    read_application_package,
)
from app.schemas.send_batch import SendBatchRead
from app.services.application_package import ApplicationPackageService
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
