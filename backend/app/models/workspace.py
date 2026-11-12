import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import LedgerAction, str_enum


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    credits_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    price_per_form_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=6, server_default="6")
    forward_emails: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    forward_not_sure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    forward_negative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    reply_phone_number: Mapped[str | None] = mapped_column(String(32), unique=True)
    default_sender_phone: Mapped[str | None] = mapped_column(String(32), index=True)
    webhook_url: Mapped[str | None] = mapped_column(String(2048))
    webhook_secret: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ledger: Mapped[list["CreditLedger"]] = relationship(back_populates="workspace", cascade="all, delete-orphan")


class CreditLedger(Base):
    __tablename__ = "credit_ledger"
    __table_args__ = (Index("ix_credit_ledger_ws_created", "workspace_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[LedgerAction] = mapped_column(str_enum(LedgerAction), nullable=False)
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("submissions.id", ondelete="SET NULL"), index=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    # Grants only: when the unused part expires, and how much of it is still unused (FIFO).
    # NULL remaining = legacy/non-tracked grant (never expires).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    remaining: Mapped[int | None] = mapped_column(Integer)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Usage rows: the grant the credit was drawn from (so refunds go back to it).
    grant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("credit_ledger.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped[Workspace] = relationship(back_populates="ledger")


class DoNotContact(Base):
    """Per-workspace suppression list. `pattern` is a bare domain (matches it and its subdomains)
    or a wildcard like `*.gov`."""

    __tablename__ = "do_not_contact"
    __table_args__ = (Index("uq_dnc_ws_pattern", "workspace_id", "pattern", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    pattern: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
