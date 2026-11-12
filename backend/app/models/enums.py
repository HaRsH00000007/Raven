import enum


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    running = "running"
    paused = "paused"
    completed = "completed"


class SubmissionStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCESS_VERIFIED = "SUCCESS_VERIFIED"
    SUBMITTED_UNVERIFIED = "SUBMITTED_UNVERIFIED"
    DRY_RUN = "DRY_RUN"
    SKIPPED_CAPTCHA = "SKIPPED_CAPTCHA"
    SKIPPED_NO_SOLICITATION = "SKIPPED_NO_SOLICITATION"
    SKIPPED_ROBOTS = "SKIPPED_ROBOTS"
    SKIPPED_COOLDOWN = "SKIPPED_COOLDOWN"
    SKIPPED_DNC = "SKIPPED_DNC"
    FAILED_NO_FORM = "FAILED_NO_FORM"
    FAILED_VALIDATION = "FAILED_VALIDATION"
    FAILED_ERROR = "FAILED_ERROR"


TERMINAL_STATUSES = {s for s in SubmissionStatus if s is not SubmissionStatus.PENDING}


class ReplyChannel(str, enum.Enum):
    email = "email"
    sms = "sms"
    voicemail = "voicemail"


class Classification(str, enum.Enum):
    positive = "positive"
    not_sure = "not_sure"
    negative = "negative"
    automatic = "automatic"


class LedgerAction(str, enum.Enum):
    grant = "grant"
    used = "used"
    refund_skip = "refund_skip"
    adjustment = "adjustment"
    expired = "expired"


def str_enum(enum_cls: type[enum.Enum], length: int = 32):
    """Portable enum column: stored as VARCHAR with a CHECK constraint (works on Postgres and SQLite)."""
    from sqlalchemy import Enum

    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
        name=f"{enum_cls.__name__.lower()}_enum",
    )
