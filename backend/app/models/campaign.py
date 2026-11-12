import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import CampaignStatus, str_enum


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[CampaignStatus] = mapped_column(
        str_enum(CampaignStatus), nullable=False, default=CampaignStatus.draft
    )
    sender_company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    sender_website: Mapped[str | None] = mapped_column(String(255))
    sender_phone: Mapped[str | None] = mapped_column(String(32))
    dry_run: Mapped[bool] = mapped_column(default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    variants: Mapped[list["MessageVariant"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="MessageVariant.label",
    )


class MessageVariant(Base):
    __tablename__ = "message_variants"
    __table_args__ = (
        # Only one *active* variant per label; archived ones are kept for history.
        Index(
            "uq_variant_active_label",
            "campaign_id",
            "label",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
            sqlite_where=text("archived_at IS NULL"),
        ),
        CheckConstraint("label IN ('A','B','C','D','E','F')", name="ck_variant_label"),
        CheckConstraint("weight >= 0", name="ck_variant_weight"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(1), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_title: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str | None] = mapped_column(String(255))
    message_body: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Replaced variants are archived (not deleted) so historical submissions keep their message.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    campaign: Mapped[Campaign] = relationship(back_populates="variants")


class CampaignTarget(Base):
    __tablename__ = "campaign_targets"
    __table_args__ = (UniqueConstraint("campaign_id", "company_id", name="uq_target_campaign_company"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
