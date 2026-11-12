"""Pure, deterministic rules used by the form engine. Kept free of I/O so they are unit-testable."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------

_DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def normalize_domain(raw: str) -> str | None:
    """'https://www.Acme.com/about?x' -> 'acme.com'. Returns None if it isn't a plausible domain."""
    if not raw:
        return None
    s = raw.strip().lower()
    if not s:
        return None
    if "://" not in s:
        s = "http://" + s
    try:
        host = urlparse(s).hostname or ""
    except ValueError:
        return None
    host = host.strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not _DOMAIN_RE.match(host):
        return None
    return host


def same_site(url: str, domain: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    domain = domain.split(":")[0]
    return host == domain or host.endswith("." + domain)


def dnc_matches(domain: str, pattern: str) -> bool:
    """`acme.com` matches acme.com and *.acme.com; `*.gov` matches any .gov domain."""
    pattern = pattern.strip().lower()
    if not pattern:
        return False
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".gov"
        return domain.endswith(suffix) or domain == suffix[1:]
    return domain == pattern or domain.endswith("." + pattern)


# ---------------------------------------------------------------------------
# Contact page discovery
# ---------------------------------------------------------------------------

_CONTACT_PATH_RE = re.compile(
    r"/(contact(-?us)?|contactus|get-?in-?touch|reach-?us|talk-?to-?us|enquir(e|y|ies)|inquir(e|y|ies)|lets-?talk|connect)(/|\.html?|\.php|$|\?)",
    re.I,
)
_CONTACT_TEXT_RE = re.compile(r"\b(contact( us)?|get in touch|talk to us|reach us|enquire|inquire|let'?s talk)\b", re.I)
_BAD_LINK_RE = re.compile(r"\.(pdf|jpe?g|png|gif|zip|docx?)$|/(careers?|jobs?|login|signin|cart|blog/)", re.I)

FALLBACK_CONTACT_PATHS = ("/contact", "/contact-us", "/contact/", "/contact-us/")


def rank_contact_links(links: list[dict], base_url: str, domain: str, limit: int = 4) -> list[str]:
    scored: dict[str, int] = {}
    for link in links:
        href = (link.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(base_url, href).split("#")[0]
        if not url.startswith(("http://", "https://")) or not same_site(url, domain):
            continue
        path = urlparse(url).path or "/"
        if _BAD_LINK_RE.search(path):
            continue
        text = link.get("text") or ""
        score = 0
        if _CONTACT_PATH_RE.search(path):
            score += 10
        if _CONTACT_TEXT_RE.search(text):
            score += 6
        if score == 0:
            continue
        score -= path.count("/")  # prefer shallow paths
        scored[url] = max(scored.get(url, -999), score)
    ordered = sorted(scored, key=lambda u: scored[u], reverse=True)
    return ordered[:limit]


# ---------------------------------------------------------------------------
# Policy: "no solicitation"
# ---------------------------------------------------------------------------

_NO_SOLICIT_PATTERNS = [
    r"\bno\s+solicit(?:ation|ations|ing|ors)?\b",
    r"\bunsolicited\s+(?:\w+\s+){0,3}(?:pitch(?:es)?|offers?|solicitations?|sales|vendors?|services|inquir(?:y|ies)|emails?|messages?|proposals?|resumes?|submissions?|calls?|marketing|advertis\w*)",
    r"\b(?:please\s+)?(?:do\s+not|don'?t)\s+(?:solicit|use\s+this\s+form\s+(?:for|to)\s+(?:sell|solicit|sales|market|pitch|advertis))",
    r"\b(?:vendors?|agencies|recruiters|recruitment\s+agencies|staffing\s+(?:agencies|firms)|third[- ]party\s+recruiters)\s+(?:need\s+not\s+apply|please\s+do\s+not\s+(?:contact|call|apply|solicit|submit))",
    r"\bnot\s+(?:accepting|interested\s+in)\s+(?:any\s+)?(?:vendor|sales|marketing|agency|third[- ]party|recruit\w*|outsourc\w*)\s+(?:pitch(?:es)?|solicitations?|inquir(?:y|ies)|offers?|services|calls?|proposals?)",
    r"\bsolicitations?\s+(?:are|is|will\s+be)\s+(?:not\s+(?:accepted|welcome|permitted)|ignored|discarded|deleted|prohibited)",
    r"\bno\s+(?:sales|vendor|agency|recruiter|marketing)\s+(?:calls|inquir(?:y|ies)|pitch(?:es)?|solicitations?|emails?)\b",
    r"\bnot\s+(?:to\s+be\s+)?used\s+for\s+(?:sales|marketing|solicitation|vendor)",
]
_NO_SOLICIT_RE = re.compile("|".join(f"(?:{p})" for p in _NO_SOLICIT_PATTERNS), re.I)


def detect_no_solicitation(text: str) -> str | None:
    """Returns the matched phrase if the page asks not to be solicited."""
    if not text:
        return None
    m = _NO_SOLICIT_RE.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Field classification
# ---------------------------------------------------------------------------

ROLES = (
    "email",
    "phone",
    "message",
    "first_name",
    "last_name",
    "full_name",
    "company",
    "title",
    "subject",
    "website",
    "city",
    "state",
    "zip",
    "country",
    "consent_checkbox",
    "optin_checkbox",
    "checkbox",
    "radio",
    "select",
    "unknown",
)


def _blob(f: dict) -> str:
    return " ".join(
        str(f.get(k) or "") for k in ("label", "name", "id", "placeholder", "aria", "autocomplete")
    ).lower().replace("_", " ").replace("-", " ")


def classify_field(f: dict) -> str:
    t = (f.get("type") or "").lower()
    tag = (f.get("tag") or "").lower()
    ac = (f.get("autocomplete") or "").lower()
    b = _blob(f)

    if t == "checkbox":
        if re.search(r"agree|consent|privacy|terms|gdpr|accept|acknowledge|i have read|permission to contact", b):
            return "consent_checkbox"
        if re.search(r"newsletter|subscribe|updates|marketing|offers|mailing list|promotional", b):
            return "optin_checkbox"
        return "checkbox"
    if t == "radio":
        return "radio"
    if t == "email" or ac == "email" or re.search(r"\be ?mail\b", b):
        return "email"
    if t == "tel" or ac.startswith("tel") or re.search(r"\b(phone|telephone|tel|mobile|cell)\b", b):
        return "phone"
    if tag == "textarea" or re.search(r"\b(message|comments?|inquiry|enquiry|question|how can we help|details|describe|description|your request|project)\b", b):
        if tag == "select":
            return "select"
        return "message"
    if ac == "given-name" or re.search(r"\b(first ?name|fname|given ?name|forename)\b", b):
        return "first_name"
    if ac == "family-name" or re.search(r"\b(last ?name|lname|surname|family ?name)\b", b):
        return "last_name"
    if ac == "organization" or re.search(r"\b(company|organi[sz]ation|business( name)?|firm|employer|agency name)\b", b):
        return "company"
    if re.search(r"\b(subject|topic|regarding|reason)\b", b):
        return "select" if tag == "select" else "subject"
    if ac == "organization-title" or re.search(r"\b(job ?title|title|position|role|designation)\b", b):
        return "select" if tag == "select" else "title"
    if ac == "url" or t == "url" or re.search(r"\b(website|web site|url|site)\b", b):
        return "website"
    if ac == "name" or re.search(r"\b(full ?name|your name|name|contact name)\b", b):
        return "full_name"
    if re.search(r"\b(zip|postal|postcode)\b", b):
        return "zip"
    if re.search(r"\bcity|town\b", b):
        return "city"
    if re.search(r"\b(state|province|region)\b", b):
        return "select" if tag == "select" else "state"
    if re.search(r"\bcountry\b", b):
        return "select" if tag == "select" else "country"
    if tag == "select":
        return "select"
    return "unknown"


_PREFERRED_OPTION_RE = re.compile(
    r"\b(general|inquiry|enquiry|business|partnership|partner|sales|question|contact|new business|services|employer|hiring|client)\b",
    re.I,
)
# "Other" often reveals a required follow-up field, so it is only the second choice.
_OTHER_OPTION_RE = re.compile(r"\bother\b", re.I)
_PLACEHOLDER_OPTION_RE = re.compile(r"^(--|select|choose|please|pick|\s*$)", re.I)


def choose_option(options: list[dict], preferred: str | None = None) -> str | None:
    """Pick a sensible value for a select/radio group. Returns the option value."""
    usable = [
        o
        for o in options
        if not o.get("disabled") and str(o.get("value", "")).strip() != "" and not _PLACEHOLDER_OPTION_RE.match(o.get("text") or "")
    ]
    if not usable:
        return None
    if preferred:
        for o in usable:
            if preferred.lower() in (o.get("text") or "").lower():
                return o["value"]
    for o in usable:
        text = o.get("text") or ""
        if _PREFERRED_OPTION_RE.search(text) and not _OTHER_OPTION_RE.search(text):
            return o["value"]
    for o in usable:
        if _OTHER_OPTION_RE.search(o.get("text") or ""):
            return o["value"]
    return usable[0]["value"]


# ---------------------------------------------------------------------------
# Outcome detection
# ---------------------------------------------------------------------------

_SUCCESS_RE = re.compile(
    r"thank(?:s| you)(?! for visiting)|"
    r"message (?:has been |was |is )?(?:sent|received|submitted|delivered)|"
    r"we(?:'ll| will| shall)? (?:be in touch|get back to you|contact you|respond|reach out|be contacting)|"
    r"submission (?:was |has been )?(?:received|successful|complete)|"
    r"successfully (?:sent|submitted|received)|"
    r"(?:have |'ve )?received your (?:message|inquiry|enquiry|request|submission)|"
    r"got your message|form (?:has been |was )?(?:submitted|sent)",
    re.I,
)
_ERROR_RE = re.compile(
    r"(?:this field is required|is required|required field|please (?:enter|fill|provide|complete|select|check)|"
    r"invalid (?:email|phone|entry|input)|not a valid|there (?:was|were) (?:an? )?(?:error|problem)|"
    r"failed to send|could not be sent|try again|one or more fields have an error)",
    re.I,
)
_SUCCESS_URL_RE = re.compile(r"(thank|success|confirm|submitted|sent|received)", re.I)


@dataclass
class Outcome:
    verified: bool
    failed: bool
    detail: str


def judge_outcome(
    before: dict,
    after: dict,
    post_statuses: list[int],
) -> Outcome:
    """Compare page snapshots taken right before and after clicking submit."""
    b_text, a_text = before.get("text") or "", after.get("text") or ""
    new_success = len(_SUCCESS_RE.findall(a_text)) > len(_SUCCESS_RE.findall(b_text))
    url_changed = (after.get("url") or "") != (before.get("url") or "")
    success_url = url_changed and bool(_SUCCESS_URL_RE.search(after.get("url") or ""))
    new_errors = len(_ERROR_RE.findall(a_text)) > len(_ERROR_RE.findall(b_text))
    more_invalid = (after.get("invalid_count") or 0) > (before.get("invalid_count") or 0)
    form_gone = before.get("form_visible") and not after.get("form_visible")
    posted_ok = any(200 <= s < 400 for s in post_statuses)
    posted_err = any(s >= 400 for s in post_statuses)

    if new_success:
        m = None
        for m in _SUCCESS_RE.finditer(a_text):
            pass
        phrase = m.group(0) if m else "thank you"
        return Outcome(True, False, f"Confirmation text detected: '{phrase}'")
    if success_url:
        return Outcome(True, False, f"Redirected to confirmation URL {after.get('url')}")
    if (new_errors or more_invalid) and after.get("form_visible"):
        m = _ERROR_RE.search(a_text)
        return Outcome(False, True, f"Form reported validation errors: '{m.group(0) if m else 'invalid fields'}'")
    if posted_err and not posted_ok and after.get("form_visible"):
        return Outcome(False, True, f"Form endpoint returned HTTP {max(post_statuses)}")
    if form_gone:
        return Outcome(False, False, "Form disappeared after submit; no confirmation text found")
    if url_changed:
        return Outcome(False, False, f"Navigated to {after.get('url')}; no confirmation text found")
    if posted_ok:
        return Outcome(False, False, "Form data was posted (HTTP 2xx/3xx); no confirmation text found")
    return Outcome(False, False, "No visible change after submit")


def render_template(template: str, values: dict[str, str]) -> str:
    def repl(m: re.Match) -> str:
        return values.get(m.group(1).strip(), m.group(0))

    return re.sub(r"\{\{\s*([a-z_]+)\s*\}\}", repl, template or "")
