from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.enums import CampaignStatus, Classification, LedgerAction, ReplyChannel, SubmissionStatus

VariantLabel = Literal["A", "B", "C", "D", "E", "F"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- workspace -----------------------------------------------------------------


class WorkspaceOut(ORM):
    id: uuid.UUID
    name: str
    credits_balance: int
    price_per_form_cents: int
    forward_emails: list[str]
    forward_not_sure: bool
    forward_negative: bool
    reply_phone_number: str | None
    default_sender_phone: str | None = None
    webhook_url: str | None = None
    webhook_secret: str | None = None
    created_at: datetime


def _e164(v: str | None) -> str | None:
    if v in (None, ""):
        return None
    cleaned = "+" + "".join(ch for ch in v if ch.isdigit())
    if not 8 <= len(cleaned) <= 16:
        raise ValueError("Use E.164 format, e.g. +15551234567")
    return cleaned


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    forward_emails: list[EmailStr] | None = Field(default=None, max_length=20)
    forward_not_sure: bool | None = None
    forward_negative: bool | None = None
    reply_phone_number: str | None = Field(default=None, max_length=32)
    default_sender_phone: str | None = Field(default=None, max_length=32)
    webhook_url: str | None = Field(default=None, max_length=2048)

    @field_validator("reply_phone_number", "default_sender_phone")
    @classmethod
    def e164(cls, v: str | None) -> str | None:
        return _e164(v)

    @field_validator("webhook_url")
    @classmethod
    def url(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return None
        v = v.strip()
        if not v.lower().startswith(("http://", "https://")) or len(v) < 10:
            raise ValueError("Webhook URL must start with http:// or https://")
        return v


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    credits: int = Field(default=0, ge=0)
    price_per_form_cents: int = Field(default=6, ge=0)


class WorkspaceCreated(BaseModel):
    id: uuid.UUID
    name: str
    api_key: str


class CreditGrant(BaseModel):
    amount: int = Field(description="Positive grants credits, negative removes them")
    note: str | None = Field(default=None, max_length=500)
    expires_in_days: int | None = Field(default=-1, ge=-1, le=3650, description="-1 = default validity, null = never")


class ExpiryOut(BaseModel):
    expired_grants: int
    credits_expired: int


class WebhookTestOut(BaseModel):
    delivered: bool
    status_code: int | None
    logged_only: bool
    error: str | None
    payload: dict


class SimulateInbound(BaseModel):
    channel: Literal["sms", "voicemail", "email"]
    from_number: str | None = Field(default=None, max_length=32)
    from_email: EmailStr | None = None
    body: str = Field(min_length=1, max_length=5000)
    company_domain: str | None = Field(default=None, max_length=255)
    submission_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _sender(self):
        if self.channel in ("sms", "voicemail"):
            self.from_number = _e164(self.from_number)
            if not self.from_number:
                raise ValueError("from_number is required for sms/voicemail")
        elif not self.from_email:
            raise ValueError("from_email is required for email")
        return self


class LedgerItem(ORM):
    id: uuid.UUID
    amount: int
    action: LedgerAction
    submission_id: uuid.UUID | None
    note: str | None
    expires_at: datetime | None = None
    remaining: int | None = None
    created_at: datetime


class LedgerPage(BaseModel):
    items: list[LedgerItem]
    total: int


# --- campaigns -----------------------------------------------------------------


class VariantIn(BaseModel):
    label: VariantLabel
    sender_name: str = Field(min_length=1, max_length=255)
    sender_title: str | None = Field(default=None, max_length=255)
    subject: str | None = Field(default=None, max_length=255)
    message_body: str = Field(min_length=1, max_length=5000)
    weight: int = Field(default=1, ge=0, le=100)


class VariantOut(ORM):
    id: uuid.UUID
    label: str
    sender_name: str
    sender_title: str | None
    subject: str | None
    message_body: str
    weight: int


class VariantStats(VariantOut):
    sent: int = 0
    replies: int = 0
    positive: int = 0
    positive_rate: float | None = None


def _check_variants(variants: list[VariantIn]) -> list[VariantIn]:
    labels = [v.label for v in variants]
    if len(set(labels)) != len(labels):
        raise ValueError("Variant labels must be unique")
    if not any(v.weight > 0 for v in variants):
        raise ValueError("At least one variant needs a weight above 0")
    return variants


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    sender_company: str | None = Field(default="", max_length=255)
    sender_website: str | None = Field(default=None, max_length=255)
    sender_phone: str | None = Field(default=None, max_length=32)
    variants: list[VariantIn] = Field(min_length=1, max_length=6)
    domains: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _v(self):
        _check_variants(self.variants)
        return self


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    sender_company: str | None = Field(default=None, max_length=255)
    sender_website: str | None = Field(default=None, max_length=255)
    sender_phone: str | None = Field(default=None, max_length=32)


class VariantsReplace(BaseModel):
    variants: list[VariantIn] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def _v(self):
        _check_variants(self.variants)
        return self


class CampaignSummary(BaseModel):
    id: uuid.UUID
    name: str
    status: CampaignStatus
    sender_company: str
    sender_website: str | None
    sender_phone: str | None
    dry_run: bool
    created_at: datetime
    launched_at: datetime | None
    targets: int = 0
    sent: int = 0
    replies: int = 0
    positive: int = 0
    pending: int = 0


class CampaignDetail(CampaignSummary):
    variants: list[VariantStats]


class CampaignList(BaseModel):
    items: list[CampaignSummary]


class TargetsIn(BaseModel):
    domains: list[str] = Field(min_length=1, max_length=100_000)


class TargetsResult(BaseModel):
    added: int
    duplicates: int
    invalid: list[str]


class LaunchIn(BaseModel):
    dry_run: bool = False


class LaunchOut(BaseModel):
    queued: int
    status: CampaignStatus


# --- submissions ---------------------------------------------------------------


class SubmissionOut(BaseModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    campaign_name: str
    variant_id: uuid.UUID
    variant_label: str
    company_id: uuid.UUID
    domain: str
    company_name: str | None
    contact_url: str | None
    unique_reply_email: str
    status: SubmissionStatus
    detail: str | None
    dry_run: bool
    has_pre_screenshot: bool
    has_post_screenshot: bool
    fields_filled: dict
    created_at: datetime
    submitted_at: datetime | None
    reply_count: int = 0
    latest_classification: Classification | None = None


class SubmissionPage(BaseModel):
    items: list[SubmissionOut]
    total: int


# --- replies -------------------------------------------------------------------


class ReplyOut(BaseModel):
    id: uuid.UUID
    submission_id: uuid.UUID | None
    workspace_id: uuid.UUID
    company_id: uuid.UUID | None
    domain: str | None
    company_name: str | None
    campaign_id: uuid.UUID | None
    campaign_name: str | None
    variant_label: str | None
    channel: ReplyChannel
    from_address: str | None
    from_name: str | None
    subject: str | None
    body: str
    raw_body: str
    recording_url: str | None
    classification: Classification | None
    confidence_score: float | None
    explanation: str | None
    classified_by: str | None
    is_forwarded: bool
    call_booked: bool
    created_at: datetime


class ReplyPage(BaseModel):
    items: list[ReplyOut]
    total: int
    counts: dict[str, int]


class ReplyUpdate(BaseModel):
    classification: Classification | None = None
    call_booked: bool | None = None


# --- do not contact -------------------------------------------------------------


class DncIn(BaseModel):
    pattern: str = Field(min_length=3, max_length=255)


class DncOut(ORM):
    id: uuid.UUID
    pattern: str
    reason: str | None
    created_at: datetime


class DncList(BaseModel):
    items: list[DncOut]


# --- stats ---------------------------------------------------------------------


class FunnelStage(BaseModel):
    stage: str
    count: int


class DailyPoint(BaseModel):
    date: str
    sent: int
    replies: int
    positive: int


class OverviewStats(BaseModel):
    forms_attempted: int
    forms_sent: int
    replies: int
    positive: int
    not_sure: int
    negative: int
    automatic: int
    calls_booked: int
    response_rate: float | None
    positive_rate: float | None
    credits_used: int
    spend_cents: int
    cost_per_positive_cents: int | None
    cost_per_reply_cents: int | None
    status_breakdown: dict[str, int]
    funnel: list[FunnelStage]
    daily: list[DailyPoint]
