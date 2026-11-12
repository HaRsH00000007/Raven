// Server-side proxy to the FastAPI backend. Attaches the workspace API key so it
// never reaches the browser. Streams binary responses (screenshots) unchanged.
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const FORWARD_REQUEST_HEADERS = ["content-type", "accept"];
const FORWARD_RESPONSE_HEADERS = ["content-type", "content-length", "content-disposition", "cache-control", "etag", "last-modified"];

async function proxy(req: NextRequest, { params }: { params: { path: string[] } }): Promise<Response> {
  const backend = process.env.BACKEND_URL;
  const apiKey = process.env.RAVEN_API_KEY;
  if (!backend) {
    return Response.json({ detail: "BACKEND_URL is not configured on the frontend server." }, { status: 500 });
  }
  if (!apiKey) {
    return Response.json({ detail: "RAVEN_API_KEY is not configured on the frontend server." }, { status: 500 });
  }

  // Reject path traversal; each segment is re-encoded.
  const segments = params.path ?? [];
  if (segments.some((s) => s === ".." || s === "." || s.includes("/"))) {
    return Response.json({ detail: "Invalid path." }, { status: 400 });
  }
  const target = new URL(`${backend.replace(/\/+$/, "")}/api/v1/${segments.map(encodeURIComponent).join("/")}`);
  target.search = req.nextUrl.search;

  const headers = new Headers();
  for (const h of FORWARD_REQUEST_HEADERS) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  headers.set("X-API-Key", apiKey);

  const hasBody = !["GET", "HEAD"].includes(req.method);
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? await req.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return Response.json({ detail: `Backend unreachable: ${message}` }, { status: 502 });
  }

  const resHeaders = new Headers();
  for (const h of FORWARD_RESPONSE_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) resHeaders.set(h, v);
  }
  if (!resHeaders.has("cache-control")) resHeaders.set("cache-control", "no-store");

  return new Response(upstream.status === 204 ? null : upstream.body, {
    status: upstream.status,
    headers: resHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
