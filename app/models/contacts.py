"""Professional contacts of a company and their channels, each with its own provenance.

Third-party personal data: a contact or a channel is only ever stored with a source, and
never guessed. Absence of a contact or of an address is represented by NO row.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import ChannelKind, InfoStatus, RoleCategory, enum_column
from app.models.sources import Source


class Contact(TimestampMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint("full_name IS NOT NULL OR is_generic", name="named_or_generic"),
        # Referenced by target_contacts so a target only links contacts of its own company.
        UniqueConstraint("id", "company_id"),
        Index(
            "uq_contacts_company_name_key",
            "company_id",
            "name_key",
            unique=True,
            postgresql_where=text("name_key IS NOT NULL"),
            sqlite_where=text("name_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    full_name: Mapped[str | None] = mapped_column(String(255))
    name_key: Mapped[str | None] = mapped_column(String(255))
    # A shared mailbox (e.g. a recruitment address) rather than a person.
    is_generic: Mapped[bool] = mapped_column(Boolean, default=False)
    role_title: Mapped[str | None] = mapped_column(String(255))
    role_category: Mapped[RoleCategory] = mapped_column(
        enum_column(RoleCategory), default=RoleCategory.UNKNOWN
    )
    status: Mapped[InfoStatus] = mapped_column(enum_column(InfoStatus), default=InfoStatus.FOUND)
    # Confirmed by a human (separate from `status`, like `verified` on evidence).
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)

    source: Mapped[Source] = relationship(lazy="joined")
    channels: Mapped[list["ContactChannel"]] = relationship(
        lazy="selectin", order_by="ContactChannel.id", cascade="all, delete-orphan"
    )


class ContactChannel(TimestampMixin, Base):
    __tablename__ = "contact_channels"
    __table_args__ = (UniqueConstraint("contact_id", "kind", "value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[ChannelKind] = mapped_column(enum_column(ChannelKind))
    value: Mapped[str] = mapped_column(String(2048))
    status: Mapped[InfoStatus] = mapped_column(enum_column(InfoStatus), default=InfoStatus.FOUND)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)

    source: Mapped[Source] = relationship(lazy="joined")
