import re
import uuid
from email.utils import getaddresses

_REPLY_ADDR_RE = re.compile(
    r"\br-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})@([a-z0-9.-]+)", re.I
)


def reply_address(submission_id: uuid.UUID, reply_domain: str) -> str:
    return f"r-{submission_id}@{reply_domain}"


def extract_submission_id(candidates: list[str], reply_domain: str) -> uuid.UUID | None:
    """Find `r-<uuid>@<reply_domain>` in any recipient field (To, Cc, envelope, original recipient)."""
    for raw in candidates:
        if not raw:
            continue
        for m in _REPLY_ADDR_RE.finditer(raw):
            if m.group(2).lower().rstrip(".") == reply_domain.lower():
                try:
                    return uuid.UUID(m.group(1))
                except ValueError:
                    continue
    return None


def parse_address(raw: str | None) -> tuple[str | None, str | None]:
    """'Jane Doe <jane@acme.com>' -> ('Jane Doe', 'jane@acme.com')."""
    if not raw:
        return None, None
    pairs = getaddresses([raw])
    if not pairs:
        return None, None
    name, addr = pairs[0]
    return (name or None), (addr.lower() or None)


_QUOTE_HEADER_RE = re.compile(
    r"^(On .{5,200}wrote:|-{2,}\s*Original Message\s*-{2,}|From:\s.+|Sent from my \w+|_{5,})\s*$",
    re.I | re.M,
)


def strip_quoted(text: str) -> str:
    """Drop quoted history and signatures-after-quote from a plain-text reply."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n")
    m = _QUOTE_HEADER_RE.search(text)
    if m and m.start() > 0:
        text = text[: m.start()]
    lines = [ln for ln in text.split("\n") if not ln.lstrip().startswith(">")]
    return "\n".join(lines).strip()


def html_to_text(html: str) -> str:
    if not html:
        return ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&nbsp;", " ", s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"&lt;", "<", s)
    s = re.sub(r"&gt;", ">", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def headers_indicate_auto(headers: dict[str, str]) -> bool:
    h = {k.lower(): (v or "").lower() for k, v in headers.items()}
    if h.get("auto-submitted", "no") not in ("", "no"):
        return True
    if h.get("x-autoreply") or h.get("x-autorespond") or h.get("x-auto-response-suppress") in ("all", "oof"):
        return True
    if h.get("precedence") in ("auto_reply", "bulk", "junk"):
        return True
    return False


def parse_raw_headers(raw: str) -> dict[str, str]:
    """Parse a raw RFC822 header block (as SendGrid provides) into a dict (last value wins)."""
    out: dict[str, str] = {}
    current = None
    for line in (raw or "").splitlines():
        if line[:1] in (" ", "\t") and current:
            out[current] += " " + line.strip()
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            current = k.strip()
            out[current] = v.strip()
    return out
