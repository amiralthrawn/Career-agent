"""Controlled batch sending routes (step 10): create, approve, execute, read.

`execute` is the ONLY action that can ever cause a real e-mail to leave this machine, and it only
runs when this endpoint is explicitly called - no cron, no background worker, nothing triggered by
qualification or by preparing an `ApplicationPackage`. The live Gmail provider comes from the
same injectable, off-by-default dependency as every other external capability: `None` unless
`SEND_MODE=auto` AND every Gmail credential is present, so `execute` still runs but every item is
refused (`no_sender_configured`/`provider_error`) rather than silently doing nothing.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.secrets import SecretStoreError, get_secret_store
from app.integrations.gmail.client import default_gmail_provider
from app.integrations.mail.ports import MailProvider
from app.schemas.send_batch import SendBatchCreateRequest, SendBatchItemRead, SendBatchRead
from app.services.application_package import ApplicationPackageService
from app.services.audit import AuditLog
from app.services.mail_sender import MailSender
from app.services.send_batch import SendBatchService

router = APIRouter(prefix="/api", tags=["send-batches"])

DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_live_mail_provider(settings: SettingsDep) -> MailProvider | None:
    """`None` by default: a real send is then refused, explicitly, before anything runs."""
    try:
        store = get_secret_store()
    except SecretStoreError:
        return None
    return default_gmail_provider(settings, store)


LiveProvider = Annotated[MailProvider | None, Depends(get_live_mail_provider)]


def get_send_batch_service(
    session: DbSession, settings: SettingsDep, live_provider: LiveProvider
) -> SendBatchService:
    # `ApplicationPackageService` here needs no LLM/GitHub client: sending only ever reads an
    # already-approved, already-generated package, it never (re-)generates one.
    packages = ApplicationPackageService(session, None, None, settings.github_username)
    mail_sender = MailSender(settings, AuditLog(session), live_provider=live_provider, actor="api")
    return SendBatchService(session, settings, mail_sender, packages)


Service = Annotated[SendBatchService, Depends(get_send_batch_service)]


def read_send_batch(service: SendBatchService, batch_id: int) -> SendBatchRead:
    batch = service.get(batch_id)
    items = service.items(batch_id)
    return SendBatchRead(
        id=batch.id,
        status=batch.status,
        correlation_id=batch.correlation_id,
        created_by=batch.created_by,
        approved_at=batch.approved_at,
        approved_by=batch.approved_by,
        created_at=batch.created_at,
        items=[SendBatchItemRead.model_validate(item) for item in items],
    )


@router.post("/send-batches", response_model=SendBatchRead, status_code=201)
def create_batch(data: SendBatchCreateRequest, service: Service) -> SendBatchRead:
    batch = service.create(
        data.application_package_ids, idempotency_key=data.idempotency_key, actor="api"
    )
    return read_send_batch(service, batch.id)


@router.get("/send-batches/{batch_id}", response_model=SendBatchRead)
def get_batch(batch_id: int, service: Service) -> SendBatchRead:
    return read_send_batch(service, batch_id)


@router.post("/send-batches/{batch_id}/approve", response_model=SendBatchRead)
def approve_batch(batch_id: int, service: Service) -> SendBatchRead:
    service.approve(batch_id, actor="api")
    return read_send_batch(service, batch_id)


@router.post("/send-batches/{batch_id}/execute", response_model=SendBatchRead)
def execute_batch(batch_id: int, service: Service) -> SendBatchRead:
    service.execute(batch_id, actor="api")
    return read_send_batch(service, batch_id)
