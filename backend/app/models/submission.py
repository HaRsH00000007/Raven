import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import SubmissionStatus, str_enum


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        Index("ix_submissions_campaign_status", "campaign_id", "status"),
        Index("ix_submissions_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("message_variants.id"), nullable=False, index=True)
    unique_reply_email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[SubmissionStatus] = mapped_column(
        str_enum(SubmissionStatus), nullable=False, default=SubmissionStatus.PENDING
    )
    detail: Mapped[str | None] = mapped_column(Text)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    contact_url: Mapped[str | None] = mapped_column(Text)
    fields_filled: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    pre_screenshot_path: Mapped[str | None] = mapped_column(Text)
    post_screenshot_path: Mapped[str | None] = mapped_column(Text)
    credit_charged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
