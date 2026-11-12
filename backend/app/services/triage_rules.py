"""Deterministic reply classifier. Used when no LLM is configured, or when the LLM call fails."""

import re
from dataclasses import dataclass

_AUTO_SENDER_RE = re.compile(r"^(no-?reply|do-?not-?reply|mailer-daemon|postmaster|notifications?|auto)", re.I)
_AUTO_SUBJECT_RE = re.compile(
    r"(automatic reply|auto(matic)?[- ]?(reply|response)|out of (the )?office|delivery status notification|"
    r"undeliverable|delivery (has )?failed|mail delivery|returned mail|we('ve| have) received your|"
    r"thank(s| you) for (contacting|reaching out|your (message|inquiry|enquiry|submission))|ticket #?\d+|\[ticket)",
    re.I,
)
_AUTO_BODY_RE = re.compile(
    r"(this is an automated|do not reply to this (e-?mail|message)|i am (currently )?out of (the )?office|"
    r"i('m| am) away|on (annual |parental )?leave|we('ve| have) received your (message|inquiry|enquiry|request|submission)|"
    r"thank(s| you) for (contacting|reaching out to) us|a member of our team will (be in touch|get back|contact you|respond)|"
    r"we will (get back to you|respond|be in touch) (within|shortly|as soon)|your (ticket|case|request) (number|#|id))",
    re.I,
)
_NEGATIVE_RE = re.compile(
    r"(\bnot interested\b|\bno,? thank(s| you)\b|\bno thanks\b|\bremove (me|us)\b|\bunsubscribe\b|"
    r"\bstop (contacting|emailing|messaging|calling)\b|\bdo not (contact|email|call|message)\b|\bdon'?t (contact|email|call|message)\b|"
    r"\bplease stop\b|\bnot looking\b|\bwe (don'?t|do not) (need|use|work with|require)\b|\bno need\b|"
    r"\bpass on this\b|\bnot at this time\b|\bnot a fit\b|\bnot a good fit\b|\bwe'?re all set\b|\btake (me|us) off\b)",
    re.I,
)
_STOP_SMS_RE = re.compile(r"^\s*(stop|unsubscribe|end|quit|cancel)\s*\.?\s*$", re.I)
_POSITIVE_RE = re.compile(
    r"(\byes\b|\binterested\b|\blet'?s (talk|chat|connect|meet)\b|\bhappy to (chat|talk|connect|meet)\b|"
    r"\bschedule (a|some) (call|time|meeting)\b|\bbook a (call|meeting|time)\b|\bset up a (call|meeting|time)\b|"
    r"\bcall me\b|\bgive (me|us) a (call|ring)\b|\bwhen (can|could) we (talk|chat|meet|speak)\b|\bwhat time\b|"
    r"\bavailable (on|this|next|for|tomorrow|today)\b|\bcalendly\b|\bsounds good\b|\bwould (love|like) to (learn|hear|talk|chat|discuss)\b|"
    r"\bsend (me|us) (your )?(rates|pricing|a proposal|a quote)\b|\bwhat (are|is) your (rates|pricing|fees)\b|"
    r"\bactively (meeting|looking)\b|\bwelcome a conversation\b|\bgood time to call\b)",
    re.I,
)


@dataclass
class RuleResult:
    classification: str
    confidence: float
    explanation: str


def classify_reply_rules(
    *, body: str, subject: str | None = None, from_address: str | None = None, auto_header: bool = False, channel: str = "email"
) -> RuleResult:
    body = body or ""
    subject = subject or ""
    local = (from_address or "").split("@")[0]

    if auto_header:
        return RuleResult("automatic", 0.95, "Message headers mark it as auto-submitted")
    if channel == "sms" and _STOP_SMS_RE.match(body):
        return RuleResult("negative", 0.99, "SMS opt-out keyword")
    if _AUTO_SENDER_RE.match(local) or _AUTO_SUBJECT_RE.search(subject):
        return RuleResult("automatic", 0.85, "Automated sender or subject line")
    if _AUTO_BODY_RE.search(body) and not _POSITIVE_RE.search(body):
        return RuleResult("automatic", 0.75, "Body reads like an auto-acknowledgement")
    neg = _NEGATIVE_RE.search(body)
    if neg:
        return RuleResult("negative", 0.8, f"Declines: '{neg.group(0)}'")
    pos = _POSITIVE_RE.search(body)
    if pos:
        return RuleResult("positive", 0.7, f"Shows interest: '{pos.group(0)}'")
    if not body.strip():
        return RuleResult("automatic", 0.5, "Empty reply")
    return RuleResult("not_sure", 0.5, "Human reply without a clear yes or no")
