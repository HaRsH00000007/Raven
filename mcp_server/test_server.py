"""Unit tests for the MCP server against a mocked Raven API (run: .venv/Scripts/python -m pytest)."""

import json

import httpx
import pytest

from server import RavenClient, build_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _server(handler):
    seen = []

    def wrapped(request: httpx.Request):
        assert request.headers["X-API-Key"] == "rvn_test"
        seen.append(request)
        return handler(request)

    srv = build_server(lambda: RavenClient("http://raven.test", "rvn_test", transport=httpx.MockTransport(wrapped)))
    return srv, seen


def _payload(result):
    """Extract the tool's returned dict from a CallToolResult."""
    if getattr(result, "structured_content", None):
        sc = result.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(result.content[0].text)


async def test_tools_listed():
    srv, _ = _server(lambda r: httpx.Response(200, json={}))
    names = {t.name for t in await srv.list_tools()}
    assert names == {"list_campaigns", "create_campaign", "upload_targets", "get_campaign_stats", "get_positive_leads"}


async def test_create_campaign_and_upload():
    def handler(req):
        if req.method == "POST" and req.url.path == "/api/v1/campaigns":
            body = json.loads(req.content)
            assert body["variants"][0]["label"] == "A" and body["domains"] == ["acme.com"]
            return httpx.Response(201, json={"id": "c1", "name": body["name"], "status": "draft"})
        if req.url.path == "/api/v1/campaigns/c1/targets":
            return httpx.Response(200, json={"added": 2, "duplicates": 0, "invalid": []})
        return httpx.Response(404, json={"detail": "nope"})

    srv, seen = _server(handler)
    r = await srv.call_tool(
        "create_campaign",
        {"name": "Nov", "sender_company": "Glocal RPO", "domains": ["acme.com"],
         "variants": [{"label": "A", "sender_name": "Rohit", "message_body": "Are you hiring recruiters this quarter?"}]},
    )
    assert _payload(r)["id"] == "c1"
    r = await srv.call_tool("upload_targets", {"campaign_id": "c1", "domains": ["a.com", "b.com"]})
    assert _payload(r)["added"] == 2
    assert [q.url.path for q in seen] == ["/api/v1/campaigns", "/api/v1/campaigns/c1/targets"]


async def test_stats_and_leads():
    def handler(req):
        p = req.url.path
        if p == "/api/v1/campaigns/c1":
            return httpx.Response(200, json={"id": "c1", "name": "Nov", "status": "running", "targets": 10, "sent": 8,
                                             "replies": 2, "positive": 1, "pending": 2,
                                             "variants": [{"label": "A", "message_body": "Q?", "sent": 8, "replies": 2, "positive": 1, "positive_rate": 0.125}]})
        if p == "/api/v1/stats/overview":
            assert req.url.params["campaign_id"] == "c1"
            return httpx.Response(200, json={"funnel": [], "response_rate": 0.25, "positive_rate": 0.125, "spend_cents": 48,
                                             "cost_per_positive_cents": 48, "status_breakdown": {"SUCCESS_VERIFIED": 8, "PENDING": 0}})
        if p == "/api/v1/replies":
            assert req.url.params["classification"] == "positive"
            return httpx.Response(200, json={"total": 1, "counts": {}, "items": [{"id": "r1", "domain": "acme.com", "body": "Yes", "channel": "email"}]})
        return httpx.Response(404, json={"detail": "nope"})

    srv, _ = _server(handler)
    stats = _payload(await srv.call_tool("get_campaign_stats", {"campaign_id": "c1"}))
    assert stats["positive_rate"] == 0.125 and stats["status_breakdown"] == {"SUCCESS_VERIFIED": 8}
    leads = _payload(await srv.call_tool("get_positive_leads", {"campaign_id": "c1"}))
    assert leads["total"] == 1 and leads["leads"][0]["domain"] == "acme.com"


async def test_api_errors_surface_as_tool_errors():
    srv, _ = _server(lambda r: httpx.Response(404, json={"detail": "Campaign not found"}))
    try:
        r = await srv.call_tool("get_campaign_stats", {"campaign_id": "missing"})
    except Exception as e:  # some SDK versions raise, others return isError
        assert "Campaign not found" in str(e)
    else:
        assert getattr(r, "is_error", getattr(r, "isError", False))
        assert "Campaign not found" in r.content[0].text
