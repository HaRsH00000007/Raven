"""Raven MCP server: lets AI agents (Claude Desktop, Claude Code, ...) drive a Raven workspace.

It is a thin client over the Raven REST API, authenticated with a workspace API key:

    RAVEN_API_URL=http://localhost:8000  RAVEN_API_KEY=rvn_...  python server.py            # stdio (default)
    python server.py --transport streamable-http                                              # HTTP on :8765

Tools: list_campaigns, create_campaign, upload_targets, get_campaign_stats, get_positive_leads.
By design there is no tool that launches a campaign: sending real messages stays a human decision in the
dashboard.
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field


class RavenAPIError(ToolError):
    """ToolError so the message (e.g. "Campaign not found") reaches the AI client."""


class RavenClient:
    def __init__(self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None):
        if not api_key:
            raise RavenAPIError("RAVEN_API_KEY is not set")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/api/v1",
            headers={"X-API-Key": api_key, "User-Agent": "raven-mcp/1.0"},
            timeout=30,
            transport=transport,
        )

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise RavenAPIError(f"Cannot reach the Raven API: {e}") from e
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = resp.text[:300]
            raise RavenAPIError(f"Raven API {resp.status_code}: {detail}")
        return resp.json() if resp.content else None

    async def aclose(self) -> None:
        await self._client.aclose()


class VariantInput(BaseModel):
    label: Literal["A", "B", "C", "D", "E", "F"] = Field(description="Variant label, unique per campaign")
    sender_name: str = Field(description="Name the message is signed with, e.g. 'Rohit Singh'")
    message_body: str = Field(
        description="Message entered into the contact form. Short single questions perform best. "
        "Placeholders: {{company_name}}, {{domain}}, {{sender_name}}, {{sender_company}}"
    )
    sender_title: str | None = None
    subject: str | None = None
    weight: int = Field(default=1, ge=0, le=100, description="Relative share of targets for A/B testing")


def build_server(client_factory=None) -> MCPServer:
    """client_factory() -> RavenClient. Injectable for tests."""
    factory = client_factory or (lambda: RavenClient(os.environ.get("RAVEN_API_URL", "http://localhost:8000"), os.environ.get("RAVEN_API_KEY", "")))
    _holder: dict[str, RavenClient] = {}

    def api() -> RavenClient:
        if "c" not in _holder:
            _holder["c"] = factory()
        return _holder["c"]

    mcp = MCPServer(
        name="raven",
        instructions=(
            "Raven sends short outreach messages through companies' own website contact forms and sorts the replies. "
            "Use these tools to set up campaigns, add target domains, and read results. Campaigns are created as drafts; "
            "a human launches them from the Raven dashboard."
        ),
    )

    @mcp.tool(description="List all campaigns in the workspace with status, targets, forms sent, replies and positive replies.")
    async def list_campaigns() -> dict:
        data = await api().request("GET", "/campaigns")
        return {"campaigns": data["items"]}

    @mcp.tool(
        description="Create a draft campaign with 1-6 message variants (A-F) and optional target domains. "
        "Returns the campaign with its id. It is not launched."
    )
    async def create_campaign(
        name: str,
        sender_company: str,
        variants: list[VariantInput],
        domains: list[str] | None = None,
        sender_website: str | None = None,
        sender_phone: str | None = None,
    ) -> dict:
        body = {
            "name": name,
            "sender_company": sender_company,
            "sender_website": sender_website,
            "sender_phone": sender_phone,
            "variants": [v.model_dump() if isinstance(v, BaseModel) else v for v in variants],
            "domains": domains or [],
        }
        return await api().request("POST", "/campaigns", json=body)

    @mcp.tool(description="Add target company domains (e.g. 'acme.com' or full URLs) to a campaign. Duplicates and invalid entries are reported.")
    async def upload_targets(campaign_id: str, domains: list[str]) -> dict:
        if not domains:
            raise RavenAPIError("domains must not be empty")
        return await api().request("POST", f"/campaigns/{campaign_id}/targets", json={"domains": domains})

    @mcp.tool(
        description="Funnel and A/B results for one campaign: forms sent, response and positive rates, cost per positive "
        "reply, outcome breakdown, and per-variant stats."
    )
    async def get_campaign_stats(campaign_id: str) -> dict:
        detail = await api().request("GET", f"/campaigns/{campaign_id}")
        overview = await api().request("GET", "/stats/overview", params={"campaign_id": campaign_id})
        return {
            "campaign": {k: detail[k] for k in ("id", "name", "status", "targets", "sent", "replies", "positive", "pending")},
            "variants": [
                {k: v.get(k) for k in ("label", "message_body", "sent", "replies", "positive", "positive_rate")}
                for v in detail["variants"]
            ],
            "funnel": overview["funnel"],
            "response_rate": overview["response_rate"],
            "positive_rate": overview["positive_rate"],
            "spend_cents": overview["spend_cents"],
            "cost_per_positive_cents": overview["cost_per_positive_cents"],
            "status_breakdown": {k: v for k, v in overview["status_breakdown"].items() if v},
        }

    @mcp.tool(description="Positive replies (leads), newest first, with company, campaign, variant, channel and message text.")
    async def get_positive_leads(campaign_id: str | None = None, limit: int = 25) -> dict:
        params: dict[str, Any] = {"classification": "positive", "limit": max(1, min(limit, 200))}
        if campaign_id:
            params["campaign_id"] = campaign_id
        data = await api().request("GET", "/replies", params=params)
        keep = ("id", "created_at", "channel", "from_name", "from_address", "domain", "company_name", "campaign_name",
                "variant_label", "subject", "body", "confidence_score", "call_booked", "recording_url")
        return {"total": data["total"], "leads": [{k: r.get(k) for k in keep} for r in data["items"]]}

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Raven MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    args = parser.parse_args()
    server = build_server()
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run(args.transport, host=os.environ.get("MCP_HOST", "127.0.0.1"), port=int(os.environ.get("MCP_PORT", "8765")))


if __name__ == "__main__":
    main()
