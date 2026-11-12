// Typed client for the Raven backend. All requests go through the Next.js proxy
// (/api/proxy/*), which attaches the workspace API key server-side.
import type {
  CampaignCreate,
  CampaignDetail,
  CampaignSummary,
  CampaignUpdate,
  Classification,
  DncEntry,
  LaunchResult,
  LedgerEntry,
  OverviewStats,
  Paginated,
  Reply,
  ReplyList,
  ReplyUpdate,
  SimulateInbound,
  Submission,
  SubmissionStatus,
  TargetsResult,
  VariantInput,
  WebhookTestResult,
  Workspace,
  WorkspaceUpdate,
} from "./types";

export const PROXY_BASE = "/api/proxy";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => {
          if (d && typeof d === "object" && "msg" in d) {
            const loc = "loc" in d && Array.isArray(d.loc) ? `${d.loc.slice(1).join(".")}: ` : "";
            return `${loc}${String((d as { msg: unknown }).msg)}`;
          }
          return String(d);
        })
        .join("; ");
    }
  }
  return fallback;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function buildPath(path: string, query?: Query): string {
  const params = new URLSearchParams();
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
    }
  }
  const qs = params.toString();
  return `${PROXY_BASE}${path}${qs ? `?${qs}` : ""}`;
}

export async function request<T>(
  method: string,
  path: string,
  options: { body?: unknown; query?: Query; form?: FormData } = {},
): Promise<T> {
  const init: RequestInit = { method, headers: {}, cache: "no-store" };
  if (options.form) {
    init.body = options.form;
  } else if (options.body !== undefined) {
    init.body = JSON.stringify(options.body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(buildPath(path, options.query), init);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  if (!res.ok) {
    throw new ApiError(res.status, errorMessage(parsed, `Request failed (${res.status})`));
  }
  return parsed as T;
}

/** SWR fetcher: key is a full proxy URL built with buildPath(). */
export async function swrFetcher<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  const text = await res.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  if (!res.ok) throw new ApiError(res.status, errorMessage(parsed, `Request failed (${res.status})`));
  return parsed as T;
}

export const keys = {
  workspace: () => buildPath("/workspace"),
  ledger: (limit: number, offset: number) => buildPath("/workspace/ledger", { limit, offset }),
  overview: (campaignId?: string) => buildPath("/stats/overview", { campaign_id: campaignId }),
  campaigns: () => buildPath("/campaigns"),
  campaign: (id: string) => buildPath(`/campaigns/${id}`),
  submissions: (q: { campaign_id?: string; status?: SubmissionStatus | ""; q?: string; limit: number; offset: number }) =>
    buildPath("/submissions", q),
  submission: (id: string) => buildPath(`/submissions/${id}`),
  replies: (q: { classification?: Classification; campaign_id?: string; limit: number; offset: number }) =>
    buildPath("/replies", q),
  dnc: () => buildPath("/do-not-contact"),
};

export const screenshotUrl = (id: string, which: "pre" | "post") =>
  `${PROXY_BASE}/submissions/${id}/screenshots/${which}`;

export const api = {
  getWorkspace: () => request<Workspace>("GET", "/workspace"),
  updateWorkspace: (body: WorkspaceUpdate) => request<Workspace>("PATCH", "/workspace", { body }),
  getLedger: (limit = 50, offset = 0) =>
    request<Paginated<LedgerEntry>>("GET", "/workspace/ledger", { query: { limit, offset } }),
  testWebhook: () => request<WebhookTestResult>("POST", "/workspace/webhook/test"),
  rotateWebhookSecret: () => request<Workspace>("POST", "/workspace/webhook/rotate-secret"),
  simulateInbound: (body: SimulateInbound) => request<Reply>("POST", "/simulate/inbound", { body }),

  getOverview: (campaignId?: string) =>
    request<OverviewStats>("GET", "/stats/overview", { query: { campaign_id: campaignId } }),

  listCampaigns: () => request<{ items: CampaignSummary[] }>("GET", "/campaigns"),
  createCampaign: (body: CampaignCreate) => request<CampaignDetail>("POST", "/campaigns", { body }),
  getCampaign: (id: string) => request<CampaignDetail>("GET", `/campaigns/${id}`),
  updateCampaign: (id: string, body: CampaignUpdate) => request<CampaignDetail>("PATCH", `/campaigns/${id}`, { body }),
  replaceVariants: (id: string, variants: VariantInput[]) =>
    request<CampaignDetail>("PUT", `/campaigns/${id}/variants`, { body: { variants } }),
  addTargets: (id: string, domains: string[]) =>
    request<TargetsResult>("POST", `/campaigns/${id}/targets`, { body: { domains } }),
  addTargetsCsv: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<TargetsResult>("POST", `/campaigns/${id}/targets/csv`, { form });
  },
  launchCampaign: (id: string, dryRun: boolean) =>
    request<LaunchResult>("POST", `/campaigns/${id}/launch`, { body: { dry_run: dryRun } }),
  pauseCampaign: (id: string) => request<CampaignDetail>("POST", `/campaigns/${id}/pause`),
  resumeCampaign: (id: string) => request<LaunchResult>("POST", `/campaigns/${id}/resume`),

  getSubmission: (id: string) => request<Submission>("GET", `/submissions/${id}`),

  getReply: (id: string) => request<Reply>("GET", `/replies/${id}`),
  updateReply: (id: string, body: ReplyUpdate) => request<Reply>("PATCH", `/replies/${id}`, { body }),
  forwardReply: (id: string) => request<Reply>("POST", `/replies/${id}/forward`),
  listReplies: (q: { classification?: Classification; campaign_id?: string; limit: number; offset: number }) =>
    request<ReplyList>("GET", "/replies", { query: q }),

  listDnc: () => request<{ items: DncEntry[] }>("GET", "/do-not-contact"),
  addDnc: (pattern: string) => request<DncEntry>("POST", "/do-not-contact", { body: { pattern } }),
  deleteDnc: (id: string) => request<void>("DELETE", `/do-not-contact/${id}`),
};
