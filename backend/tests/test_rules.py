import uuid

import pytest

from app.services.campaigns import parse_domains_csv
from app.services.email_utils import extract_submission_id, strip_quoted
from app.services.form_rules import (
    choose_option,
    classify_field,
    detect_no_solicitation,
    dnc_matches,
    judge_outcome,
    normalize_domain,
    rank_contact_links,
    render_template,
)
from app.services.triage_rules import classify_reply_rules


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.Acme.com/about?x=1", "acme.com"),
        ("acme.co.uk", "acme.co.uk"),
        ("sub.acme.com/", "sub.acme.com"),
        ("not a domain", None),
        ("", None),
        ("localhost", None),
    ],
)
def test_normalize_domain(raw, expected):
    assert normalize_domain(raw) == expected


def test_dnc_patterns():
    assert dnc_matches("acme.com", "acme.com")
    assert dnc_matches("eu.acme.com", "acme.com")
    assert not dnc_matches("notacme.com", "acme.com")
    assert dnc_matches("agency.gov", "*.gov")


@pytest.mark.parametrize(
    "text",
    [
        "No solicitation please.",
        "Unsolicited sales pitches will be discarded.",
        "Recruiting agencies need not apply",
        "Staffing agencies please do not contact us",
        "We are not accepting vendor solicitations at this time.",
        "Solicitations are not accepted through this form.",
        "This form is not to be used for sales or marketing.",
    ],
)
def test_no_solicitation_detected(text):
    assert detect_no_solicitation(text)


@pytest.mark.parametrize(
    "text",
    ["Contact us for a free consultation.", "We solicit feedback from clients every quarter.", "Send us your resume."],
)
def test_no_solicitation_not_overmatched(text):
    assert detect_no_solicitation(text) is None


def test_contact_links_ranked():
    links = [
        {"href": "/about", "text": "About"},
        {"href": "https://other.com/contact", "text": "Contact"},
        {"href": "/company/contact-us/", "text": "Get in touch"},
        {"href": "/contact", "text": "Contact"},
        {"href": "mailto:hi@acme.com", "text": "Email us"},
    ]
    ranked = rank_contact_links(links, "https://acme.com/", "acme.com")
    assert ranked[0] == "https://acme.com/contact"
    assert "https://acme.com/company/contact-us/" in ranked
    assert all("other.com" not in u for u in ranked)


@pytest.mark.parametrize(
    "field,role",
    [
        ({"tag": "input", "type": "email", "name": "your-email"}, "email"),
        ({"tag": "input", "type": "text", "name": "fname", "label": "First Name"}, "first_name"),
        ({"tag": "input", "type": "text", "label": "Last name"}, "last_name"),
        ({"tag": "input", "type": "text", "label": "Your Name *"}, "full_name"),
        ({"tag": "input", "type": "text", "label": "Company / Organization"}, "company"),
        ({"tag": "textarea", "type": "textarea", "name": "comments"}, "message"),
        ({"tag": "input", "type": "tel", "name": "ph"}, "phone"),
        ({"tag": "input", "type": "checkbox", "label": "I agree to the privacy policy"}, "consent_checkbox"),
        ({"tag": "input", "type": "checkbox", "label": "Sign me up for the newsletter"}, "optin_checkbox"),
        ({"tag": "select", "type": "select-one", "label": "Reason for inquiry"}, "select"),
        ({"tag": "input", "type": "text", "label": "Job Title"}, "title"),
    ],
)
def test_classify_field(field, role):
    assert classify_field(field) == role


def test_choose_option():
    opts = [{"value": "", "text": "-- Select --"}, {"value": "s", "text": "Support"}, {"value": "g", "text": "General Inquiry"}]
    assert choose_option(opts) == "g"
    assert choose_option([{"value": "", "text": "Choose"}]) is None


def test_judge_outcome():
    before = {"url": "https://a.com/contact", "text": "Contact us. Send", "form_visible": True, "invalid_count": 0}
    ok = judge_outcome(before, {**before, "text": "Contact us. Thank you! Your message has been sent."}, [200])
    assert ok.verified
    redirect = judge_outcome(before, {**before, "url": "https://a.com/thank-you", "form_visible": False}, [302])
    assert redirect.verified
    err = judge_outcome(before, {**before, "text": "Contact us. This field is required."}, [])
    assert err.failed
    unknown = judge_outcome(before, before, [])
    assert not unknown.verified and not unknown.failed


@pytest.mark.parametrize(
    "body,expected",
    [
        ("Yes, we work with RPO partners. When can we talk?", "positive"),
        ("I've tried to call you a few times and it's a busy signal every time. When is a good time to call you?", "positive"),
        ("We are actively meeting with prospective RPO partners... I'd welcome a conversation with your team.", "positive"),
        ("Not interested, thanks.", "negative"),
        ("Please remove us from your list", "negative"),
        ("What exactly do you offer?", "not_sure"),
        ("Thanks for your message, we'll be in touch. This is an automated response.", "automatic"),
    ],
)
def test_reply_rules(body, expected):
    assert classify_reply_rules(body=body).classification == expected


def test_reply_rules_headers_and_sms():
    assert classify_reply_rules(body="Yes!", auto_header=True).classification == "automatic"
    assert classify_reply_rules(body="STOP", channel="sms").classification == "negative"
    assert classify_reply_rules(body="hi", from_address="noreply@acme.com").classification == "automatic"


def test_extract_submission_id():
    sid = uuid.uuid4()
    assert extract_submission_id([f"Rohit <r-{sid}@replies.x.com>"], "replies.x.com") == sid
    assert extract_submission_id([f"r-{sid}@evil.com"], "replies.x.com") is None
    assert extract_submission_id(["hello@replies.x.com"], "replies.x.com") is None


def test_strip_quoted():
    text = "Sounds good, call me.\n\nOn Mon, Jan 5, 2026 at 9:00 AM Rohit <r@x.com> wrote:\n> Are you hiring?"
    assert strip_quoted(text) == "Sounds good, call me."


def test_template_and_csv():
    assert render_template("Hi {{ company_name }} from {{sender_name}} {{unknown}}", {"company_name": "Acme", "sender_name": "Tom"}) == "Hi Acme from Tom {{unknown}}"
    rows = parse_domains_csv(b"\xef\xbb\xbfdomain,company_name\nacme.com,Acme\nfoo.io,\n")
    assert [(r.domain, r.company_name) for r in rows] == [("acme.com", "Acme"), ("foo.io", None)]
    plain = parse_domains_csv(b"acme.com\nfoo.io\n")
    assert [r.domain for r in plain] == ["acme.com", "foo.io"]
