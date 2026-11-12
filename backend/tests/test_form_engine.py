"""End-to-end tests of the Playwright form engine against local fixture websites (no internet)."""

import functools
import http.server
import threading
from pathlib import Path

import pytest

from app.models.enums import SubmissionStatus
from app.workers.form_probe import FormEngine, NoCreditsError, ProbeOptions, RobotsChecker, SenderProfile

FIXTURES = Path(__file__).parent / "fixtures"


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):  # fetch('/submit.json', {method: 'POST'})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")


@pytest.fixture(scope="module")
def sites():
    servers = {}
    for site in FIXTURES.iterdir():
        handler = functools.partial(_Handler, directory=str(site))
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers[site.name] = f"127.0.0.1:{srv.server_address[1]}"
    yield servers
    # daemon threads exit with the test process


def _sender() -> SenderProfile:
    return SenderProfile(
        full_name="Rohit Singh",
        email="r-test@replies.example.com",
        message="Are you hiring recruiters this quarter?\n\n- Rohit, Glocal RPO",
        phone="+15555550123",
        company="Glocal RPO",
        title="Partnerships",
    )


async def _probe(domain: str, tmp_path: Path, **kw):
    opts = ProbeOptions(run_id="t", screenshot_dir=tmp_path, schemes=("http://",), page_timeout_ms=10000, submit_wait_ms=3000, **{k: v for k, v in kw.items() if k in ("dry_run",)})
    robots = RobotsChecker("RavenFormBot/1.0")
    async with FormEngine() as engine:
        try:
            return await engine.probe(domain, _sender(), opts, robots, before_submit=kw.get("before_submit"))
        finally:
            await robots.aclose()


async def test_success_verified(sites, tmp_path):
    charged = []

    async def reserve():
        charged.append(1)
        return True

    r = await _probe(sites["site_ok"], tmp_path, before_submit=reserve)
    assert r.status == SubmissionStatus.SUCCESS_VERIFIED, r.detail
    assert charged == [1]
    assert r.contact_url.endswith("/contact-us.html")
    filled = r.fields_filled
    assert filled["email"] == "r-test@replies.example.com"
    assert filled["first_name"] == "Rohit" and filled["last_name"] == "Singh"
    assert filled["topic"] == "General inquiry"
    assert filled["consent"] == "checked"
    assert "news" not in filled  # never opt into newsletters
    assert "website_hp" not in filled and "trap" not in filled  # honeypots untouched
    assert (tmp_path / "t_pre.png").exists() and (tmp_path / "t_post.png").exists()


async def test_dry_run_does_not_submit(sites, tmp_path):
    async def reserve():
        raise AssertionError("must not reserve credit on dry run")

    r = await _probe(sites["site_ok"], tmp_path, dry_run=True, before_submit=reserve)
    assert r.status == SubmissionStatus.DRY_RUN
    assert r.pre_screenshot and not r.post_screenshot


async def test_no_credits_raises(sites, tmp_path):
    async def reserve():
        return False

    with pytest.raises(NoCreditsError):
        await _probe(sites["site_ok"], tmp_path, before_submit=reserve)


async def test_captcha_skipped(sites, tmp_path):
    r = await _probe(sites["site_captcha"], tmp_path)
    assert r.status == SubmissionStatus.SKIPPED_CAPTCHA
    assert "recaptcha" in r.captcha_kinds


async def test_no_solicitation_skipped(sites, tmp_path):
    r = await _probe(sites["site_nosolicit"], tmp_path)
    assert r.status == SubmissionStatus.SKIPPED_NO_SOLICITATION, r.detail


async def test_robots_respected(sites, tmp_path):
    r = await _probe(sites["site_robots"], tmp_path)
    # /contact.html is disallowed; homepage has no form -> robots skip or no form, never a submission
    assert r.status in (SubmissionStatus.SKIPPED_ROBOTS, SubmissionStatus.FAILED_NO_FORM)
    assert not r.submit_clicked


async def test_no_form(sites, tmp_path):
    r = await _probe(sites["site_noform"], tmp_path)
    assert r.status == SubmissionStatus.FAILED_NO_FORM


async def test_unfillable_required_field(sites, tmp_path):
    r = await _probe(sites["site_required"], tmp_path)
    assert r.status == SubmissionStatus.FAILED_VALIDATION
    assert not r.submit_clicked


async def test_formless_embed_in_iframe(sites, tmp_path):
    r = await _probe(sites["site_iframe"], tmp_path, before_submit=lambda: _true())
    assert r.status == SubmissionStatus.SUCCESS_VERIFIED, r.detail
    assert r.fields_filled["work_email"] == "r-test@replies.example.com"
    assert r.fields_filled["fullname"] == "Rohit Singh"


async def _true():
    return True


async def test_shadow_dom_form(sites, tmp_path):
    r = await _probe(sites["site_shadow"], tmp_path, before_submit=lambda: _true())
    assert r.status == SubmissionStatus.SUCCESS_VERIFIED, r.detail
    assert r.fields_filled["email"] == "r-test@replies.example.com"


async def test_cookie_banner_and_modal_form(sites, tmp_path):
    r = await _probe(sites["site_modal"], tmp_path, before_submit=lambda: _true())
    assert r.status == SubmissionStatus.SUCCESS_VERIFIED, r.detail
    assert r.fields_filled["message"].startswith("Are you hiring")


async def test_fields_revealed_by_dropdown_get_filled(sites, tmp_path):
    r = await _probe(sites["site_reveal"], tmp_path, before_submit=lambda: _true())
    assert r.status == SubmissionStatus.SUCCESS_VERIFIED, r.detail
    assert r.fields_filled["subject"] == "Other"
    assert r.fields_filled["subject_other"] == "Quick question"


async def test_multi_step_form(sites, tmp_path):
    charged = []

    async def reserve():
        charged.append(1)
        return True

    r = await _probe(sites["site_multistep"], tmp_path, before_submit=reserve)
    assert r.status == SubmissionStatus.SUCCESS_VERIFIED, r.detail
    assert charged == [1]  # one credit, reserved only before the final submit
    f = r.fields_filled
    assert f["email"] == "r-test@replies.example.com" and f["company"] == "Glocal RPO"
    assert f["size"] in ("1-10", "11-50") and f["message"].startswith("Are you hiring")
    assert (tmp_path / "t_pre_step1.png").exists() and (tmp_path / "t_pre_step2.png").exists()


async def test_multi_step_dry_run_walks_client_side_steps(sites, tmp_path):
    r = await _probe(sites["site_multistep"], tmp_path, dry_run=True)
    assert r.status == SubmissionStatus.DRY_RUN and "3-step form" in r.detail, r.detail
    assert r.fields_filled["message"].startswith("Are you hiring")


async def test_multi_step_dry_run_never_posts_a_step(sites, tmp_path):
    # Step 1's Next is a real submit button: advancing would send data, so a dry run stops there.
    r = await _probe(sites["site_multistep_post"], tmp_path, dry_run=True)
    assert r.status == SubmissionStatus.DRY_RUN and "not advanced" in r.detail, r.detail
    assert "message" not in r.fields_filled
