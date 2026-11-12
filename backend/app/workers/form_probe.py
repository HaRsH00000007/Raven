"""Core form engine: find a company's contact form, fill it honestly, prove what was sent.

Guardrails (by design, not configuration):
  * Vanilla Chromium. No stealth plugins, no fingerprint spoofing, an identifying UA token.
  * Any CAPTCHA / bot challenge -> SKIPPED_CAPTCHA. We never solve or bypass one.
  * robots.txt is honoured for every URL we load.
  * "No solicitation" language -> SKIPPED_NO_SOLICITATION.

This module has no database access so it can run standalone (see scripts/run_benchmark.py).
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.robotparser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Frame,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    async_playwright,
)

from app.models.enums import SubmissionStatus
from app.services.form_rules import (
    FALLBACK_CONTACT_PATHS,
    choose_option,
    classify_field,
    detect_no_solicitation,
    judge_outcome,
    rank_contact_links,
)
from app.workers.form_js import (
    CHECK_VALIDITY_JS,
    COLLECT_LINKS_JS,
    DETECT_CAPTCHA_JS,
    DISMISS_COOKIES_JS,
    EXTRACT_FORMS_JS,
    FIND_FORM_OPENER_JS,
    PAGE_STATE_JS,
)

log = logging.getLogger("raven.form_probe")

MAX_FORM_STEPS = 4

CAPTCHA_FRAME_HINTS = ("recaptcha", "hcaptcha", "challenges.cloudflare.com", "turnstile", "friendlycaptcha", "arkoselabs", "funcaptcha")


class NoCreditsError(Exception):
    """Raised when the before_submit hook refuses (workspace is out of credits)."""


@dataclass
class SenderProfile:
    full_name: str
    email: str  # the per-submission reply address
    message: str
    phone: str | None = None
    company: str | None = None
    title: str | None = None
    website: str | None = None
    subject: str | None = None

    @property
    def first_name(self) -> str:
        return self.full_name.split()[0] if self.full_name.split() else self.full_name

    @property
    def last_name(self) -> str:
        parts = self.full_name.split()
        return " ".join(parts[1:]) if len(parts) > 1 else ""


@dataclass
class ProbeOptions:
    run_id: str  # used for screenshot file names (the submission id)
    screenshot_dir: Path
    dry_run: bool = False
    page_timeout_ms: int = 25000
    submit_wait_ms: int = 12000
    ua_token: str = "RavenFormBot/1.0"
    bot_info_url: str = "https://example.com/bot"
    # URL prefixes tried for the homepage, in order.
    schemes: tuple[str, ...] = ("https://", "https://www.", "http://")


@dataclass
class ProbeResult:
    status: SubmissionStatus
    detail: str
    contact_url: str | None = None
    fields_filled: dict[str, str] = field(default_factory=dict)
    pre_screenshot: str | None = None
    post_screenshot: str | None = None
    submit_clicked: bool = False
    duration_s: float = 0.0
    captcha_kinds: list[str] = field(default_factory=list)


FieldMapper = Callable[[list[dict]], Awaitable[dict[str, str]]]


class _Skip(Exception):
    def __init__(self, status: SubmissionStatus, detail: str, contact_url: str | None = None, captcha: list[str] | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.contact_url = contact_url
        self.captcha = captcha or []


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------


class RobotsChecker:
    """RFC 9309 semantics: 4xx -> allow all, 5xx / network failure -> disallow all."""

    def __init__(self, ua_token: str, client: httpx.AsyncClient | None = None):
        self.ua_name = ua_token.split("/")[0]
        self._client = client
        self._owns_client = client is None
        self._cache: dict[str, urllib.robotparser.RobotFileParser | bool] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=10.0, follow_redirects=True, headers={"User-Agent": f"Mozilla/5.0 (compatible; {self.ua_name})"}
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def _load(self, origin: str) -> urllib.robotparser.RobotFileParser | bool:
        client = await self._get_client()
        try:
            resp = await client.get(origin + "/robots.txt")
        except httpx.HTTPError as e:
            log.info("robots.txt unreachable for %s: %s", origin, e)
            return False  # disallow all
        if 400 <= resp.status_code < 500:
            return True  # allow all
        if resp.status_code >= 500:
            return False
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(resp.text.splitlines())
        return parser

    async def allowed(self, url: str) -> bool:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if len(self._cache) > 5000:
                self._cache.clear()
            if origin not in self._cache:
                self._cache[origin] = await self._load(origin)
        rules = self._cache[origin]
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(self.ua_name, url)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class FormEngine:
    """Holds one Chromium instance; each probe gets an isolated browser context."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._base_ua: str | None = None
        self._relaunch_lock = asyncio.Lock()

    async def _launch(self) -> None:
        assert self._pw is not None
        self._browser = await self._pw.chromium.launch(
            headless=self.headless, args=["--disable-dev-shm-usage"]
        )
        ctx = await self._browser.new_context()
        page = await ctx.new_page()
        self._base_ua = await page.evaluate("navigator.userAgent")
        await ctx.close()

    async def start(self) -> "FormEngine":
        self._pw = await async_playwright().start()
        await self._launch()
        return self

    async def _ensure_browser(self) -> None:
        """Relaunch Chromium if it crashed, so one bad site (or an OOM) can't fail every later job."""
        if self._browser is not None and self._browser.is_connected():
            return
        async with self._relaunch_lock:
            if self._browser is not None and self._browser.is_connected():
                return
            log.warning("Chromium is not connected; relaunching")
            try:
                if self._browser is not None:
                    await self._browser.close()
            except PlaywrightError:
                pass
            await self._launch()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def __aenter__(self) -> "FormEngine":
        return await self.start()

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def _new_context(self, opts: ProbeOptions) -> BrowserContext:
        assert self._pw is not None, "FormEngine.start() not called"
        await self._ensure_browser()
        assert self._browser is not None
        ua = f"{self._base_ua} {opts.ua_token} (+{opts.bot_info_url})"
        ctx = await self._browser.new_context(
            user_agent=ua,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            java_script_enabled=True,
        )
        ctx.set_default_timeout(opts.page_timeout_ms)
        ctx.set_default_navigation_timeout(opts.page_timeout_ms)
        # Skip heavy media; images/CSS stay so screenshots are faithful.
        await ctx.route("**/*", lambda route: route.abort() if route.request.resource_type == "media" else route.continue_())
        return ctx

    async def probe(
        self,
        domain: str,
        sender: SenderProfile,
        opts: ProbeOptions,
        robots: RobotsChecker,
        before_submit: Callable[[], Awaitable[bool]] | None = None,
        field_mapper: FieldMapper | None = None,
    ) -> ProbeResult:
        started = time.monotonic()
        opts.screenshot_dir.mkdir(parents=True, exist_ok=True)
        ctx = await self._new_context(opts)
        try:
            page = await ctx.new_page()
            result = await _run_probe(page, domain, sender, opts, robots, before_submit, field_mapper)
        except _Skip as s:
            result = ProbeResult(status=s.status, detail=s.detail, contact_url=s.contact_url, captcha_kinds=s.captcha)
        except NoCreditsError:
            raise
        except PlaywrightTimeout as e:
            result = ProbeResult(status=SubmissionStatus.FAILED_ERROR, detail=f"Timeout: {str(e).splitlines()[0][:200]}")
        except PlaywrightError as e:
            result = ProbeResult(status=SubmissionStatus.FAILED_ERROR, detail=f"Browser error: {str(e).splitlines()[0][:200]}")
        finally:
            try:
                await ctx.close()
            except PlaywrightError:
                pass  # browser already gone; next probe relaunches it
        result.duration_s = round(time.monotonic() - started, 2)
        return result


# ---------------------------------------------------------------------------
# Probe steps
# ---------------------------------------------------------------------------


async def _goto(page: Page, url: str, opts: ProbeOptions) -> int | None:
    resp = await page.goto(url, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=min(6000, opts.page_timeout_ms))
    except PlaywrightTimeout:
        pass
    return resp.status if resp else None


SCROLL_THROUGH_JS = r"""
async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const step = Math.max(400, Math.floor(window.innerHeight * 0.8));
  for (let i = 0; i < 12; i++) {
    const before = window.scrollY;
    window.scrollBy(0, step);
    await sleep(250);
    if (window.scrollY === before) break;  // reached the bottom
  }
  window.scrollTo(0, 0);
  await sleep(300);
}
"""


async def _dismiss_cookies(page: Page) -> None:
    try:
        if await page.evaluate(DISMISS_COOKIES_JS):
            await page.wait_for_timeout(500)
    except PlaywrightError:
        pass


async def _open_form_if_hidden(page: Page) -> bool:
    """Click a 'Send us a message'-style opener (modal/accordion). Returns True if one was clicked."""
    try:
        if not await page.evaluate(FIND_FORM_OPENER_JS):
            return False
        await page.locator("[data-raven-opener]").first.click(timeout=5000)
        await page.wait_for_timeout(1500)
        return True
    except PlaywrightError:
        return False


async def _settle(page: Page) -> None:
    """Dismiss cookie banners, then scroll through the page so lazy-loaded embeds and scroll-reveal
    animations (opacity:0 until in view) render before we analyse forms."""
    await _dismiss_cookies(page)
    try:
        await page.evaluate(SCROLL_THROUGH_JS)
        await page.wait_for_load_state("networkidle", timeout=4000)
    except PlaywrightTimeout:
        pass
    except PlaywrightError:
        pass


async def _captcha_kinds(page: Page) -> list[str]:
    kinds: set[str] = set()
    for frame in page.frames:
        url = (frame.url or "").lower()
        for hint in CAPTCHA_FRAME_HINTS:
            if hint in url:
                kinds.add(hint)
        try:
            for k in await frame.evaluate(DETECT_CAPTCHA_JS):
                kinds.add(k)
        except PlaywrightError:
            continue
    return sorted(kinds)


async def _open_home(page: Page, domain: str, opts: ProbeOptions, robots: RobotsChecker) -> str:
    last_err = "unreachable"
    for base in (f"{scheme}{domain}/" for scheme in opts.schemes):
        if not await robots.allowed(base):
            raise _Skip(SubmissionStatus.SKIPPED_ROBOTS, f"robots.txt disallows {base}")
        try:
            status = await _goto(page, base, opts)
        except PlaywrightError as e:
            last_err = str(e).splitlines()[0][:160]
            continue
        kinds = await _captcha_kinds(page)
        if "cloudflare_challenge" in kinds:
            raise _Skip(SubmissionStatus.SKIPPED_CAPTCHA, "Site is behind a bot challenge", captcha=kinds)
        if status is not None and status >= 400:
            last_err = f"HTTP {status}"
            continue
        return page.url
    raise _Skip(SubmissionStatus.FAILED_ERROR, f"Could not load homepage: {last_err}")


async def _extract_forms(page: Page) -> list[tuple[Frame, dict]]:
    out: list[tuple[Frame, dict]] = []
    for fi, frame in enumerate(page.frames):
        try:
            forms = await frame.evaluate(EXTRACT_FORMS_JS, f"fr{fi}")
        except PlaywrightError:
            continue
        for form in forms:
            out.append((frame, form))
    return out


def _score_form(form: dict) -> int:
    if form["excluded"] or not form["visible"]:
        return -1
    fields = form["fields"]
    roles = [classify_field(f) for f in fields]
    multi_step = bool(form.get("next_idx")) and form.get("submit_idx") is None
    if multi_step:
        # Step 1 of a wizard: the message box usually lives on a later step.
        if not (form.get("has_any_textarea") or "message" in roles) or not fields:
            return -1
        score = 4
    else:
        if "message" not in roles or form.get("submit_idx") is None:
            return -1
        score = 5
    if "email" in roles:
        score += 3
    if any(r in roles for r in ("full_name", "first_name")):
        score += 1
    if len(fields) >= 3:
        score += 2
    blob = (form.get("text") or "").lower() + " " + (form.get("meta") or "").lower()
    if any(w in blob for w in ("contact", "message", "inquir", "enquir", "get in touch", "how can we help")):
        score += 2
    return score


def _best_form(candidates: list[tuple[Frame, dict]]) -> tuple[Frame, dict] | None:
    best: tuple[int, tuple[Frame, dict]] | None = None
    for frame, form in candidates:
        s = _score_form(form)
        if s < 0:
            continue
        if best is None or s > best[0]:
            best = (s, (frame, form))
    return best[1] if best else None


def _value_for(role: str, f: dict, sender: SenderProfile) -> str | None:
    values = {
        "email": sender.email,
        "phone": sender.phone,
        "message": sender.message,
        "first_name": sender.first_name,
        "last_name": sender.last_name or (sender.first_name if f.get("required") else None),
        "full_name": sender.full_name,
        "company": sender.company,
        "title": sender.title,
        "subject": sender.subject or "Quick question",
        "website": sender.website,
    }
    v = values.get(role)
    if v is None and f.get("required") and role in ("city", "state", "zip", "country", "unknown", "title", "company", "website"):
        v = {"number": "1", "date": "2026-01-01", "url": "https://example.com"}.get(f.get("type") or "", "N/A")
    if v is not None and f.get("maxlength"):
        v = v[: int(f["maxlength"])]
    return v


async def _plan_fields(form: dict, field_mapper: FieldMapper | None) -> dict[str, str]:
    """Returns {idx: role}. Uses the LLM mapper only when rules miss a core field."""
    roles = {f["idx"]: classify_field(f) for f in form["fields"]}
    core = set(roles.values())
    needs_help = ("email" not in core and "phone" not in core) or "unknown" in core
    if field_mapper and needs_help:
        try:
            llm_roles = await field_mapper(form["fields"])
            for idx, role in llm_roles.items():
                if idx in roles and (roles[idx] == "unknown" or role in ("email", "message")):
                    roles[idx] = role
        except Exception as e:  # LLM is best-effort; rules remain authoritative
            log.warning("LLM field mapping failed: %s", e)
    return roles


async def _fill(frame: Frame, form: dict, roles: dict[str, str], sender: SenderProfile) -> dict[str, str]:
    filled: dict[str, str] = {}
    by_idx = {f["idx"]: f for f in form["fields"]}
    radio_groups_done: set[str] = set()
    for idx, role in roles.items():
        f = by_idx[idx]
        loc = frame.locator(f'[data-raven-idx="{idx}"]')
        key = f.get("name") or f.get("label") or idx
        if f["tag"] == "select":
            if role in ("select", "subject", "country", "state", "title") or f.get("required"):
                pref = "United States" if role == "country" else None
                value = choose_option(f["options"], preferred=pref)
                if value is not None:
                    await loc.select_option(value=value)
                    filled[key] = next((o["text"] for o in f["options"] if o["value"] == value), value)
            continue
        if role == "consent_checkbox" or (role == "checkbox" and f.get("required")):
            await loc.check(force=True)
            filled[key] = "checked"
            continue
        if role in ("optin_checkbox", "checkbox"):
            continue  # never opt the sender into marketing lists
        if role == "radio":
            group = f.get("name") or idx
            if group in radio_groups_done or not f.get("required"):
                continue
            await loc.check(force=True)
            radio_groups_done.add(group)
            filled[key] = f.get("value") or "selected"
            continue
        value = _value_for(role, f, sender)
        if value is None:
            continue
        try:
            await loc.fill(value)
        except PlaywrightError as e:
            log.info("could not fill %s (%s): %s", key, role, str(e).splitlines()[0][:120])
            continue
        filled[key] = value
    return filled


def _field_identity(f: dict) -> tuple:
    return (f.get("tag"), f.get("type"), f.get("name"), f.get("id"), f.get("label"), f.get("value"))


async def _fill_revealed_fields(frame: Frame, form: dict, sender: SenderProfile, filled: dict[str, str]) -> dict:
    """Choosing a dropdown option (e.g. 'Other') can reveal new required fields. Re-scan the form once
    and fill anything that wasn't there before. Returns the fresh form descriptor (markers are re-applied)."""
    frame_key = form["key"].rsplit("-", 1)[0]
    try:
        fresh = next((f for f in await frame.evaluate(EXTRACT_FORMS_JS, frame_key) if f["key"] == form["key"]), None)
    except PlaywrightError:
        return form
    if fresh is None or fresh.get("submit_idx") is None:
        return form
    known = {_field_identity(f) for f in form["fields"]}
    new_fields = [f for f in fresh["fields"] if _field_identity(f) not in known]
    if new_fields:
        roles = {f["idx"]: classify_field(f) for f in new_fields}
        filled.update(await _fill(frame, {**fresh, "fields": new_fields}, roles, sender))
    return fresh


async def _screenshot(target, path: Path, page: Page, full_page_fallback: bool = True) -> str | None:
    try:
        await target.screenshot(path=str(path))
        return path.name
    except PlaywrightError:
        if not full_page_fallback:
            return None
        try:
            await page.screenshot(path=str(path), full_page=True)
            return path.name
        except PlaywrightError:
            return None


async def _run_probe(
    page: Page,
    domain: str,
    sender: SenderProfile,
    opts: ProbeOptions,
    robots: RobotsChecker,
    before_submit: Callable[[], Awaitable[bool]] | None,
    field_mapper: FieldMapper | None,
) -> ProbeResult:
    home_url = await _open_home(page, domain, opts, robots)
    links = await page.evaluate(COLLECT_LINKS_JS)
    candidates = rank_contact_links(links, home_url, domain)
    if not candidates:
        base = home_url.rstrip("/")
        candidates = [base + p for p in FALLBACK_CONTACT_PATHS[:2]]
    # The homepage itself is the last resort.
    candidates.append(home_url)

    chosen: tuple[Frame, dict] | None = None
    contact_url: str | None = None
    robots_blocked = 0
    tried: list[str] = []
    for url in candidates:
        if not await robots.allowed(url):
            robots_blocked += 1
            continue
        if page.url.rstrip("/") != url.rstrip("/"):
            try:
                status = await _goto(page, url, opts)
            except PlaywrightError:
                continue
            if status is not None and status >= 400:
                continue
        body_text = await page.evaluate("document.body ? document.body.innerText : ''")
        phrase = detect_no_solicitation(body_text)
        if phrase:
            raise _Skip(SubmissionStatus.SKIPPED_NO_SOLICITATION, f"Page says: '{phrase}'", contact_url=page.url)
        await _settle(page)
        forms = await _extract_forms(page)
        best = _best_form(forms)
        if best is None and await _open_form_if_hidden(page):
            forms = await _extract_forms(page)
            best = _best_form(forms)
        if best is None:
            tried.append(page.url)
            continue
        kinds = await _captcha_kinds(page)
        if kinds:
            raise _Skip(SubmissionStatus.SKIPPED_CAPTCHA, f"CAPTCHA present: {', '.join(kinds)}", contact_url=page.url, captcha=kinds)
        chosen, contact_url = best, page.url
        break

    if chosen is None:
        if robots_blocked and robots_blocked == len(candidates):
            raise _Skip(SubmissionStatus.SKIPPED_ROBOTS, "robots.txt disallows every contact page candidate")
        pages = ", ".join(dict.fromkeys(tried)) or "none reachable"
        raise _Skip(SubmissionStatus.FAILED_NO_FORM, f"No contact form with a message field found (checked: {pages})"[:600])

    frame, form = chosen
    phrase = detect_no_solicitation(form.get("text") or "")
    if phrase:
        raise _Skip(SubmissionStatus.SKIPPED_NO_SOLICITATION, f"Form says: '{phrase}'", contact_url=contact_url)

    # Fill the form, stepping through up to MAX_FORM_STEPS pages of a multi-step wizard.
    filled: dict[str, str] = {}
    role_set: set[str] = set()
    done: set[tuple] = set()
    steps_taken = 0
    while True:
        roles = await _plan_fields(form, field_mapper)
        by_idx = {f["idx"]: f for f in form["fields"]}
        role_set |= set(roles.values())
        todo = {i: r for i, r in roles.items() if _field_identity(by_idx[i]) not in done}
        filled.update(await _fill(frame, {**form, "fields": [by_idx[i] for i in todo]}, todo, sender))
        done |= {_field_identity(f) for f in form["fields"]}
        form = await _fill_revealed_fields(frame, form, sender, filled)
        done |= {_field_identity(f) for f in form["fields"]}
        role_set |= {classify_field(f) for f in form["fields"]}

        if not form.get("next_idx") or form.get("submit_idx") is not None:
            break  # final step reached
        if steps_taken >= MAX_FORM_STEPS:
            raise _Skip(SubmissionStatus.FAILED_NO_FORM, f"Multi-step form longer than {MAX_FORM_STEPS} steps", contact_url=contact_url)
        if opts.dry_run and form.get("next_is_submit"):
            # Advancing would post this step to the site's server; a dry run never sends anything.
            return ProbeResult(
                status=SubmissionStatus.DRY_RUN,
                detail=f"Dry run: multi-step form, filled step {steps_taken + 1}; not advanced because "
                f"'{form.get('next_text')}' posts to the server",
                contact_url=contact_url,
                fields_filled=filled,
                pre_screenshot=await _screenshot(
                    frame.locator(f'[data-raven-form="{form["key"]}"]'),
                    opts.screenshot_dir / f"{opts.run_id}_pre.png", page),
            )
        step_invalid = await frame.evaluate(CHECK_VALIDITY_JS, {"key": form["key"], "visibleOnly": True})
        if step_invalid:
            return ProbeResult(
                status=SubmissionStatus.FAILED_VALIDATION,
                detail=f"Step {steps_taken + 1} has required fields that could not be filled: {', '.join(step_invalid)[:200]}",
                contact_url=contact_url,
                fields_filled=filled,
            )
        await _screenshot(
            frame.locator(f'[data-raven-form="{form["key"]}"]'),
            opts.screenshot_dir / f"{opts.run_id}_pre_step{steps_taken + 1}.png", page)
        await frame.locator(f'[data-raven-next="{form["next_idx"]}"]').click(timeout=8000)
        await page.wait_for_timeout(1200)
        steps_taken += 1
        frame_key = form["key"].rsplit("-", 1)[0]
        try:
            fresh = await frame.evaluate(EXTRACT_FORMS_JS, frame_key)
        except PlaywrightError:
            fresh = []
        nxt = next((f for f in fresh if f["key"] == form["key"]), None)
        if nxt is None or not nxt["visible"]:
            raise _Skip(SubmissionStatus.FAILED_NO_FORM, f"Multi-step form disappeared after step {steps_taken}", contact_url=contact_url)
        form = nxt

    if "email" not in role_set and not ("phone" in role_set and sender.phone):
        raise _Skip(SubmissionStatus.FAILED_NO_FORM, "Form has no email field to receive replies", contact_url=contact_url)
    if "message" not in role_set:
        raise _Skip(SubmissionStatus.FAILED_NO_FORM, "Multi-step form never showed a message field", contact_url=contact_url)

    invalid = await frame.evaluate(CHECK_VALIDITY_JS, form["key"])
    form_loc = frame.locator(f'[data-raven-form="{form["key"]}"]')
    try:
        await form_loc.scroll_into_view_if_needed(timeout=3000)
    except PlaywrightError:
        pass
    pre_path = opts.screenshot_dir / f"{opts.run_id}_pre.png"
    pre = await _screenshot(form_loc, pre_path, page)

    if invalid:
        by_idx = {f["idx"]: f for f in form["fields"]}
        labels = [
            ((by_idx[i].get("label") or by_idx[i].get("name") or i) if i in by_idx else i)[:40] for i in invalid
        ]
        return ProbeResult(
            status=SubmissionStatus.FAILED_VALIDATION,
            detail=f"Required fields could not be filled: {', '.join(labels)}",
            contact_url=contact_url,
            fields_filled=filled,
            pre_screenshot=pre,
        )

    if opts.dry_run:
        steps_note = f"{steps_taken + 1}-step form " if steps_taken else "form "
        return ProbeResult(
            status=SubmissionStatus.DRY_RUN,
            detail=f"Dry run: {steps_note}filled, not submitted (submit button: '{form.get('submit_text') or '?'}')",
            contact_url=contact_url,
            fields_filled=filled,
            pre_screenshot=pre,
        )

    if before_submit is not None and not await before_submit():
        raise NoCreditsError()

    before_state = await frame.evaluate(PAGE_STATE_JS, form["key"])
    post_statuses: list[int] = []

    def on_response(resp) -> None:
        try:
            if resp.request.method == "POST":
                post_statuses.append(resp.status)
        except PlaywrightError:
            pass

    page.on("response", on_response)
    submit = frame.locator(f'[data-raven-submit="{form["submit_idx"]}"]')
    await submit.click(timeout=8000)
    try:
        await page.wait_for_load_state("networkidle", timeout=opts.submit_wait_ms)
    except PlaywrightTimeout:
        pass
    await page.wait_for_timeout(1500)  # let client-side confirmation messages render

    try:
        after_state = await frame.evaluate(PAGE_STATE_JS, form["key"])
    except PlaywrightError:
        # The frame navigated away or was detached (common for embedded forms).
        after_state = await page.main_frame.evaluate(PAGE_STATE_JS, None)
        after_state["form_visible"] = False

    post_path = opts.screenshot_dir / f"{opts.run_id}_post.png"
    post = await _screenshot(page, post_path, page, full_page_fallback=False)

    late_captcha = [k for k in await _captcha_kinds(page) if k not in ("captcha",)]
    outcome = judge_outcome(before_state, after_state, post_statuses)
    if late_captcha and not outcome.verified:
        return ProbeResult(
            status=SubmissionStatus.SKIPPED_CAPTCHA,
            detail=f"CAPTCHA challenge appeared on submit: {', '.join(late_captcha)}",
            contact_url=contact_url,
            fields_filled=filled,
            pre_screenshot=pre,
            post_screenshot=post,
            submit_clicked=True,
            captcha_kinds=late_captcha,
        )

    if outcome.verified:
        status = SubmissionStatus.SUCCESS_VERIFIED
    elif outcome.failed:
        status = SubmissionStatus.FAILED_VALIDATION
    else:
        status = SubmissionStatus.SUBMITTED_UNVERIFIED
    return ProbeResult(
        status=status,
        detail=outcome.detail,
        contact_url=contact_url,
        fields_filled=filled,
        pre_screenshot=pre,
        post_screenshot=post,
        submit_clicked=True,
    )
