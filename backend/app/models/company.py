import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("workspace_id", "domain", name="uq_company_ws_domain"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255))
    contact_url: Mapped[str | None] = mapped_column(Text)
    # Digits only (E.164 without "+"), used to attribute SMS / voicemail replies.
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    blacklist_reason: Mapped[str | None] = mapped_column(String(64))
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
