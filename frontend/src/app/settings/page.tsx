"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { Check, Copy, Eye, EyeOff, FlaskConical, Plus, RefreshCw, Send, ShieldBan, Trash2, Webhook } from "lucide-react";
import {
  ClassificationBadge,
  EmptyState,
  ErrorState,
  InlineError,
  LoadingState,
  NativeSelect,
  PageHeader,
  Pagination,
} from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, keys, swrFetcher } from "@/lib/api";
import type {
  DncEntry,
  LedgerAction,
  LedgerEntry,
  Paginated,
  Reply,
  SimulatedChannel,
  WebhookTestResult,
  Workspace,
} from "@/lib/types";
import { formatCents, formatDate, formatDateTime, formatNumber } from "@/lib/utils";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function SettingsPage() {
  const ws = useSWR<Workspace>(keys.workspace(), swrFetcher);
  return (
    <>
      <PageHeader title="Settings" description="Where replies go, your Raven number, credits and the do-not-contact list." />
      {ws.error ? (
        <ErrorState error={ws.error} onRetry={() => ws.mutate()} />
      ) : !ws.data ? (
        <LoadingState />
      ) : (
        <div className="flex flex-col gap-6">
          <WorkspaceForm key={ws.data.id} workspace={ws.data} onSaved={(w) => ws.mutate(w, { revalidate: false })} />
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <WebhookCard key={`wh-${ws.data.id}`} workspace={ws.data} onSaved={(w) => ws.mutate(w, { revalidate: false })} />
            <SimulateCard />
          </div>
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <LedgerCard workspace={ws.data} />
            <DncCard />
          </div>
        </div>
      )}
    </>
  );
}

function WorkspaceForm({ workspace, onSaved }: { workspace: Workspace; onSaved: (w: Workspace) => void }) {
  const [name, setName] = useState(workspace.name);
  const [emails, setEmails] = useState(workspace.forward_emails.join("\n"));
  const [notSure, setNotSure] = useState(workspace.forward_not_sure);
  const [negative, setNegative] = useState(workspace.forward_negative);
  const [phone, setPhone] = useState(workspace.reply_phone_number ?? "");
  const [senderPhone, setSenderPhone] = useState(workspace.default_sender_phone ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!saved) return;
    const t = setTimeout(() => setSaved(false), 3000);
    return () => clearTimeout(t);
  }, [saved]);

  async function save() {
    const list = emails
      .split(/[\s,;]+/)
      .map((e) => e.trim())
      .filter(Boolean);
    const bad = list.filter((e) => !EMAIL_RE.test(e));
    if (bad.length) {
      setError(new Error(`Not a valid email: ${bad.join(", ")}`));
      return;
    }
    if (!name.trim()) {
      setError(new Error("Workspace name is required."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const w = await api.updateWorkspace({
        name: name.trim(),
        forward_emails: Array.from(new Set(list)),
        forward_not_sure: notSure,
        forward_negative: negative,
        reply_phone_number: phone.trim() || null,
        default_sender_phone: senderPhone.trim() || null,
      });
      onSaved(w);
      setEmails(w.forward_emails.join("\n"));
      setSaved(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Workspace & reply routing</CardTitle>
        <CardDescription>Positive replies are always forwarded. Automatic replies (“Thanks for your message”) never are.</CardDescription>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ws-name">Workspace name</Label>
            <Input id="ws-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ws-emails">Forward replies to</Label>
            <Textarea
              id="ws-emails"
              rows={4}
              value={emails}
              onChange={(e) => setEmails(e.target.value)}
              placeholder={"sales@yourcompany.com\nrohit@yourcompany.com"}
            />
            <p className="text-xs text-muted-foreground">One address per line — for example one per salesperson.</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ws-phone">Raven phone number</Label>
            <Input id="ws-phone" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+15551234567" />
            <p className="text-xs text-muted-foreground">
              Texts and voicemails to this number are transcribed and sorted like emails. Use E.164 format.
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ws-sender-phone">Default sender phone</Label>
            <Input
              id="ws-sender-phone"
              value={senderPhone}
              onChange={(e) => setSenderPhone(e.target.value)}
              placeholder="+15551234567"
            />
            <p className="text-xs text-muted-foreground">
              Entered in a form’s phone field when a campaign has no sender phone of its own. Many forms require one. Calls
              and texts to this number are also matched to this workspace.
            </p>
          </div>
        </div>
        <div className="flex flex-col gap-3">
          <div className="text-sm font-medium">Which replies reach you</div>
          <RouteRow label="Positive" description="“Yes, when can we talk?”" checked disabled />
          <RouteRow label="Not sure" description="“What exactly do you offer?”" checked={notSure} onChange={setNotSure} />
          <RouteRow label="Negative" description="“Not interested, thanks.” The company is added to do-not-contact either way." checked={negative} onChange={setNegative} />
          <RouteRow label="Automatic" description="Auto-responders stay in Raven." checked={false} disabled />
        </div>
      </CardContent>
      <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <Button onClick={save} disabled={busy}>
          Save settings
        </Button>
        {saved && <span className="text-sm text-emerald-700">Saved.</span>}
        <div className="flex-1">
          <InlineError error={error} />
        </div>
      </CardContent>
    </Card>
  );
}

function RouteRow({
  label,
  description,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange?: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-md border p-3">
      <div className="text-sm">
        <div className="font-medium">{label}</div>
        <div className="text-muted-foreground">{description}</div>
      </div>
      <Switch checked={checked} onCheckedChange={(v) => onChange?.(v)} disabled={disabled} aria-label={`Forward ${label} replies`} />
    </div>
  );
}

const LEDGER_LABEL: Record<LedgerAction, string> = {
  grant: "Credits added",
  used: "Form sent",
  refund_skip: "Refund · not sent",
  adjustment: "Adjustment",
  expired: "Expired · unused",
};

function grantExpiry(e: LedgerEntry): string | null {
  if (e.action !== "grant" || e.amount <= 0) return null;
  if (!e.expires_at) return "Never expires";
  const expired = new Date(e.expires_at).getTime() <= Date.now();
  const left = e.remaining ?? null;
  const when = `${expired ? "Expired" : "Expires"} ${formatDate(e.expires_at)}`;
  return left === null ? when : `${when} · ${formatNumber(left)} unused`;
}

function LedgerCard({ workspace }: { workspace: Workspace }) {
  const limit = 25;
  const [offset, setOffset] = useState(0);
  const { data, error, mutate } = useSWR<Paginated<LedgerEntry>>(keys.ledger(limit, offset), swrFetcher, { keepPreviousData: true });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Credits</CardTitle>
        <CardDescription>
          <span className="text-2xl font-semibold tabular text-foreground">{formatNumber(workspace.credits_balance)}</span> forms left ·{" "}
          {formatCents(workspace.price_per_form_cents)} per form. Only forms that send are charged. Unused credits
          carry over one month, then expire.
        </CardDescription>
      </CardHeader>
      {error ? (
        <ErrorState error={error} onRetry={() => mutate()} />
      ) : !data ? (
        <LoadingState />
      ) : data.items.length === 0 ? (
        <EmptyState title="No credit activity yet" className="py-8" />
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>When</TableHead>
                <TableHead>Activity</TableHead>
                <TableHead>Expires</TableHead>
                <TableHead className="text-right">Amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((e) => (
                <TableRow key={e.id}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">{formatDateTime(e.created_at)}</TableCell>
                  <TableCell>
                    <div>{LEDGER_LABEL[e.action] ?? e.action}</div>
                    {e.note && <div className="text-xs text-muted-foreground">{e.note}</div>}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">{grantExpiry(e) ?? "—"}</TableCell>
                  <TableCell
                    className={`text-right tabular ${e.amount > 0 ? "text-emerald-700" : e.action === "expired" ? "text-amber-700" : ""}`}
                  >
                    {e.amount > 0 ? "+" : ""}
                    {formatNumber(e.amount)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Pagination total={data.total} limit={limit} offset={offset} onChange={setOffset} />
        </>
      )}
    </Card>
  );
}

function DncCard() {
  const { data, error, mutate } = useSWR<{ items: DncEntry[] }>(keys.dnc(), swrFetcher);
  const [pattern, setPattern] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [filter, setFilter] = useState("");

  async function add() {
    const p = pattern.trim().toLowerCase();
    if (!p) return;
    if (!/^(\*\.)?[a-z0-9.-]+\.[a-z]{2,}$|^\*\.[a-z]{2,}$/.test(p)) {
      setActionError(new Error("Use a domain like acme.com, or a wildcard like *.gov or *.acme.com."));
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await api.addDnc(p);
      setPattern("");
      await mutate();
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setBusy(true);
    setActionError(null);
    try {
      await api.deleteDnc(id);
      await mutate();
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(false);
    }
  }

  const items = (data?.items ?? []).filter((d) => !filter || d.pattern.includes(filter.toLowerCase()));

  return (
    <Card>
      <CardHeader>
        <CardTitle>Do not contact</CardTitle>
        <CardDescription>These domains are never sent a form. Companies that reply negatively are added automatically.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void add();
          }}
        >
          <Input value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="acme.com or *.gov" aria-label="Domain or pattern" />
          <Button type="submit" disabled={busy || !pattern.trim()}>
            <Plus /> Add
          </Button>
        </form>
        <InlineError error={actionError} />
        {(data?.items.length ?? 0) > 10 && (
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter list…" aria-label="Filter list" />
        )}
      </CardContent>
      {error ? (
        <ErrorState error={error} onRetry={() => mutate()} />
      ) : !data ? (
        <LoadingState />
      ) : items.length === 0 ? (
        <EmptyState icon={ShieldBan} title={filter ? "No matches" : "The list is empty"} className="py-8" />
      ) : (
        <ul className="max-h-96 divide-y overflow-y-auto border-t">
          {items.map((d) => (
            <li key={d.id} className="flex items-center justify-between gap-3 px-5 py-2.5 text-sm">
              <div className="min-w-0">
                <div className="truncate font-mono text-xs">{d.pattern}</div>
                <div className="text-xs text-muted-foreground">{formatDateTime(d.created_at)}</div>
              </div>
              <div className="flex items-center gap-2">
                {d.reason && <Badge variant="muted">{d.reason.replace(/_/g, " ")}</Badge>}
                <Button variant="ghost" size="icon" aria-label={`Remove ${d.pattern}`} disabled={busy} onClick={() => remove(d.id)}>
                  <Trash2 />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}


function WebhookCard({ workspace, onSaved }: { workspace: Workspace; onSaved: (w: Workspace) => void }) {
  const [url, setUrl] = useState(workspace.webhook_url ?? "");
  const [reveal, setReveal] = useState(false);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState<"save" | "rotate" | "test" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const [result, setResult] = useState<WebhookTestResult | null>(null);

  useEffect(() => {
    if (!saved && !copied) return;
    const t = setTimeout(() => {
      setSaved(false);
      setCopied(false);
    }, 2500);
    return () => clearTimeout(t);
  }, [saved, copied]);

  const secret = workspace.webhook_secret;
  const dirty = (workspace.webhook_url ?? "") !== url.trim();

  async function run(kind: "save" | "rotate" | "test", fn: () => Promise<void>) {
    setBusy(kind);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  function save() {
    const v = url.trim();
    if (v && !/^https?:\/\/\S+$/i.test(v)) {
      setError(new Error("Use a full URL starting with https:// (or http:// for local testing)."));
      return;
    }
    void run("save", async () => {
      const w = await api.updateWorkspace({ webhook_url: v || null });
      onSaved(w);
      setUrl(w.webhook_url ?? "");
      setSaved(true);
    });
  }

  async function copy() {
    if (!secret) return;
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
    } catch {
      setError(new Error("Could not copy. Reveal the secret and copy it manually."));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Webhook className="h-4 w-4" /> CRM webhook
        </CardTitle>
        <CardDescription>
          Every reply sorted as positive is POSTed here as JSON — point it at your CRM, Zapier or Make. With no URL, events
          are only written to the server log.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="wh-url">Webhook URL</Label>
          <div className="flex gap-2">
            <Input
              id="wh-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://hooks.zapier.com/hooks/catch/…"
            />
            <Button onClick={save} disabled={busy !== null || !dirty}>
              Save
            </Button>
          </div>
          {saved && <span className="text-xs text-emerald-700">Saved.</span>}
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Signing secret</Label>
          <div className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded-md border bg-muted/40 px-3 py-2 font-mono text-xs">
              {secret ? (reveal ? secret : "•".repeat(Math.min(secret.length, 32))) : "Not generated yet — save a URL or rotate to create one"}
            </code>
            {secret && (
              <>
                <Button variant="outline" size="icon" onClick={() => setReveal((v) => !v)} aria-label={reveal ? "Hide secret" : "Reveal secret"}>
                  {reveal ? <EyeOff /> : <Eye />}
                </Button>
                <Button variant="outline" size="icon" onClick={copy} aria-label="Copy secret">
                  {copied ? <Check /> : <Copy />}
                </Button>
              </>
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={busy !== null}
              onClick={() =>
                void run("rotate", async () => {
                  const w = await api.rotateWebhookSecret();
                  onSaved(w);
                  setReveal(true);
                })
              }
            >
              <RefreshCw /> {secret ? "Rotate" : "Generate"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Each request carries <code className="font-mono">X-Raven-Timestamp</code> and{" "}
            <code className="font-mono">X-Raven-Signature: sha256=&lt;hex&gt;</code>, an HMAC-SHA256 of{" "}
            <code className="font-mono">&quot;&lt;timestamp&gt;.&lt;raw body&gt;&quot;</code> with this secret. Rotating
            invalidates the old secret immediately.
          </p>
        </div>

        <div className="flex flex-col gap-2 border-t pt-4">
          <div>
            <Button
              variant="secondary"
              disabled={busy !== null || dirty}
              onClick={() =>
                void run("test", async () => {
                  setResult(await api.testWebhook());
                })
              }
            >
              <Send /> {busy === "test" ? "Sending…" : "Send test event"}
            </Button>
            {dirty && <span className="ml-3 text-xs text-muted-foreground">Save the URL first.</span>}
          </div>
          {result && <WebhookResult result={result} />}
          <InlineError error={error} />
        </div>
      </CardContent>
    </Card>
  );
}

function WebhookResult({ result }: { result: WebhookTestResult }) {
  const [open, setOpen] = useState(false);
  let tone = "border-emerald-200 bg-emerald-50 text-emerald-900";
  let text = `Delivered${result.status_code ? ` · HTTP ${result.status_code}` : ""}.`;
  if (result.logged_only) {
    tone = "border-sky-200 bg-sky-50 text-sky-900";
    text = "No URL configured — the payload was written to the server log only.";
  } else if (!result.delivered) {
    tone = "border-red-200 bg-red-50 text-red-900";
    text = `Not delivered${result.status_code ? ` · HTTP ${result.status_code}` : ""}${result.error ? ` · ${result.error}` : ""}.`;
  }
  return (
    <div className={`rounded-md border p-3 text-sm ${tone}`}>
      <div className="flex items-center justify-between gap-3">
        <span>{text}</span>
        <button type="button" className="text-xs underline underline-offset-2" onClick={() => setOpen((v) => !v)}>
          {open ? "Hide payload" : "Show payload"}
        </button>
      </div>
      {open && (
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border bg-background p-2 text-xs text-foreground">
          {JSON.stringify(result.payload, null, 2)}
        </pre>
      )}
    </div>
  );
}

const SIM_PLACEHOLDER: Record<SimulatedChannel, string> = {
  sms: "Yes, we work with RPO partners. Call me tomorrow?",
  voicemail: "Hi, this is Jane from Acme Staffing returning your message. We'd like to talk, call me back at 555-0100.",
  email: "Thanks for reaching out — what exactly do you offer?",
};

function SimulateCard() {
  const [channel, setChannel] = useState<SimulatedChannel>("sms");
  const [fromNumber, setFromNumber] = useState("+15555550123");
  const [fromEmail, setFromEmail] = useState("jane@acme-staffing.com");
  const [domain, setDomain] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [reply, setReply] = useState<Reply | null>(null);

  async function submit() {
    const text = body.trim() || SIM_PLACEHOLDER[channel];
    if (channel === "email" ? !EMAIL_RE.test(fromEmail.trim()) : !/^\+?[\d\s().-]{7,}$/.test(fromNumber.trim())) {
      setError(new Error(channel === "email" ? "Enter a valid sender email." : "Enter a valid phone number, e.g. +15555550123."));
      return;
    }
    setBusy(true);
    setError(null);
    setReply(null);
    try {
      const r = await api.simulateInbound({
        channel,
        from_number: channel === "email" ? null : fromNumber.trim(),
        from_email: channel === "email" ? fromEmail.trim() : null,
        company_domain: domain.trim() || null,
        body: text,
      });
      setReply(r);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FlaskConical className="h-4 w-4" /> Simulate an inbound reply
        </CardTitle>
        <CardDescription>
          Test routing and AI triage without Twilio or an email provider. Runs the same attribution, triage, forwarding
          and webhook path as a real reply. Only works when the backend allows the simulator (non-production).
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="sim-channel">Channel</Label>
              <NativeSelect id="sim-channel" value={channel} onChange={(e) => setChannel(e.target.value as SimulatedChannel)}>
                <option value="sms">SMS</option>
                <option value="voicemail">Voicemail</option>
                <option value="email">Email</option>
              </NativeSelect>
            </div>
            {channel === "email" ? (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="sim-email">From email</Label>
                <Input id="sim-email" value={fromEmail} onChange={(e) => setFromEmail(e.target.value)} />
              </div>
            ) : (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="sim-phone">From number</Label>
                <Input id="sim-phone" value={fromNumber} onChange={(e) => setFromNumber(e.target.value)} />
              </div>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="sim-domain">Company domain (optional)</Label>
            <Input id="sim-domain" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="acme-staffing.com" />
            <p className="text-xs text-muted-foreground">
              Links the reply to that company’s latest submission. Leave empty to test automatic attribution.
            </p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="sim-body">{channel === "voicemail" ? "Transcript" : "Message"}</Label>
            <Textarea id="sim-body" rows={3} value={body} onChange={(e) => setBody(e.target.value)} placeholder={SIM_PLACEHOLDER[channel]} />
          </div>
          <div>
            <Button type="submit" disabled={busy}>
              <Send /> {busy ? "Sending…" : "Simulate reply"}
            </Button>
          </div>
        </form>
        <InlineError error={error} />
        {reply && (
          <div className="flex flex-col gap-2 rounded-md border p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <ClassificationBadge value={reply.classification} />
              {reply.classified_by && <span className="text-xs text-muted-foreground">by {reply.classified_by}</span>}
            </div>
            {reply.explanation && <div className="text-muted-foreground">{reply.explanation}</div>}
            <div>
              {reply.domain || reply.campaign_name ? (
                <>
                  Matched to <span className="font-medium">{reply.company_name || reply.domain || "unknown company"}</span>
                  {reply.campaign_name && (
                    <>
                      {" "}· campaign <span className="font-medium">{reply.campaign_name}</span>
                      {reply.variant_label && <> · variant {reply.variant_label}</>}
                    </>
                  )}
                </>
              ) : (
                <span className="text-muted-foreground">Not matched to a company or campaign.</span>
              )}
            </div>
            {reply.classification && (
              <Link
                href={`/inbox?classification=${reply.classification}`}
                className="text-sm font-medium text-primary underline-offset-4 hover:underline"
              >
                Open in inbox →
              </Link>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
