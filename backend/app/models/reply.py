import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import Classification, ReplyChannel, str_enum


class InboundReply(Base):
    __tablename__ = "inbound_replies"
    __table_args__ = (
        Index("ix_replies_ws_class_created", "workspace_id", "classification", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("submissions.id", ondelete="SET NULL"), index=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), index=True)
    channel: Mapped[ReplyChannel] = mapped_column(str_enum(ReplyChannel, 16), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    from_address: Mapped[str | None] = mapped_column(String(255))
    from_name: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(998))
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_auto_submitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    recording_url: Mapped[str | None] = mapped_column(Text)
    # NULL until the triage worker runs.
    classification: Mapped[Classification | None] = mapped_column(str_enum(Classification, 16))
    confidence_score: Mapped[float | None] = mapped_column(Float)
    explanation: Mapped[str | None] = mapped_column(Text)
    classified_by: Mapped[str | None] = mapped_column(String(16))
    is_forwarded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    webhook_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    call_booked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
