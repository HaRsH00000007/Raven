"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import useSWR from "swr";
import { ArrowLeft, Bot, CalendarCheck, Forward, Images, Mail, MessageSquare, Voicemail } from "lucide-react";
import {
  ClassificationBadge,
  classificationLabel,
  EmptyState,
  ErrorState,
  InlineError,
  LoadingState,
  NativeSelect,
  PageHeader,
  Pagination,
} from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, keys, swrFetcher } from "@/lib/api";
import { CLASSIFICATIONS, type CampaignSummary, type Classification, type Reply, type ReplyChannel, type ReplyList } from "@/lib/types";
import { cn, formatDateTime, formatPercent, relativeTime } from "@/lib/utils";

const LIMIT = 25;

const CHANNEL_ICON: Record<ReplyChannel, React.ComponentType<{ className?: string }>> = {
  email: Mail,
  sms: MessageSquare,
  voicemail: Voicemail,
};

function isClassification(v: string | null): v is Classification {
  return v !== null && (CLASSIFICATIONS as readonly string[]).includes(v);
}

function InboxInner() {
  const router = useRouter();
  const params = useSearchParams();
  const initialTab = params.get("classification");
  const [tab, setTab] = useState<Classification>(isClassification(initialTab) ? initialTab : "positive");
  const [campaignId, setCampaignId] = useState(params.get("campaign_id") ?? "");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const campaigns = useSWR<{ items: CampaignSummary[] }>(keys.campaigns(), swrFetcher);
  const listKey = keys.replies({ classification: tab, campaign_id: campaignId || undefined, limit: LIMIT, offset });
  const list = useSWR<ReplyList>(listKey, swrFetcher, { refreshInterval: 20_000, keepPreviousData: true });

  useEffect(() => {
    const qs = new URLSearchParams();
    qs.set("classification", tab);
    if (campaignId) qs.set("campaign_id", campaignId);
    router.replace(`/inbox?${qs.toString()}`, { scroll: false });
  }, [tab, campaignId, router]);

  const items = list.data?.items ?? [];
  const selected = items.find((r) => r.id === selectedId) ?? null;

  return (
    <>
      <PageHeader
        title="Inbox"
        description="Every reply by email, text or voicemail, sorted by AI the moment it arrives."
        actions={
          <NativeSelect
            aria-label="Filter by campaign"
            value={campaignId}
            onChange={(e) => {
              setCampaignId(e.target.value);
              setOffset(0);
              setSelectedId(null);
            }}
            className="w-full sm:w-56"
          >
            <option value="">All campaigns</option>
            {campaigns.data?.items.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </NativeSelect>
        }
      />

      <Tabs
        value={tab}
        onValueChange={(v) => {
          if (isClassification(v)) {
            setTab(v);
            setOffset(0);
            setSelectedId(null);
          }
        }}
      >
        <TabsList>
          {CLASSIFICATIONS.map((c) => (
            <TabsTrigger key={c} value={c}>
              {classificationLabel(c)}
              <span className="rounded-full bg-background/70 px-1.5 text-xs tabular text-muted-foreground">
                {list.data?.counts?.[c] ?? "·"}
              </span>
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-[minmax(300px,380px)_1fr]">
        <Card className={cn("overflow-hidden", selected && "hidden lg:block")}>
          {list.error ? (
            <ErrorState error={list.error} onRetry={() => list.mutate()} />
          ) : !list.data ? (
            <LoadingState />
          ) : items.length === 0 ? (
            <EmptyState
              title={`No ${classificationLabel(tab).toLowerCase()} replies`}
              description={tab === "positive" ? "Positive replies land here and are forwarded to your inbox." : undefined}
            />
          ) : (
            <>
              <ul className="divide-y">
                {items.map((r) => {
                  const Icon = CHANNEL_ICON[r.channel];
                  return (
                    <li key={r.id}>
                      <button
                        type="button"
                        onClick={() => setSelectedId(r.id)}
                        className={cn(
                          "flex w-full flex-col gap-1 px-4 py-3 text-left transition-colors hover:bg-muted/50",
                          r.id === selectedId && "bg-muted",
                        )}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium">
                            <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                            <span className="truncate">{r.company_name || r.domain || r.from_name || r.from_address || "Unknown sender"}</span>
                          </span>
                          <span className="shrink-0 text-xs text-muted-foreground">{relativeTime(r.created_at)}</span>
                        </div>
                        <div className="line-clamp-2 text-sm text-muted-foreground">{r.body || r.raw_body}</div>
                        <div className="flex flex-wrap items-center gap-1.5">
                          {r.variant_label && <span className="text-xs text-muted-foreground">Variant {r.variant_label}</span>}
                          {r.call_booked && (
                            <span className="inline-flex items-center gap-1 text-xs text-emerald-700">
                              <CalendarCheck className="h-3 w-3" /> Call booked
                            </span>
                          )}
                          {r.is_forwarded && <span className="text-xs text-muted-foreground">· Forwarded</span>}
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
              <Pagination total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
            </>
          )}
        </Card>

        <div className={cn(!selected && "hidden lg:block")}>
          {selected ? (
            <ReplyDetail
              key={selected.id}
              reply={selected}
              onBack={() => setSelectedId(null)}
              onChanged={async (updated) => {
                await list.mutate();
                if (updated.classification !== tab) setSelectedId(null);
              }}
            />
          ) : (
            <Card>
              <EmptyState icon={Mail} title="Select a reply" description="Pick a reply on the left to read it and act on it." />
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

function ReplyDetail({ reply, onBack, onChanged }: { reply: Reply; onBack: () => void; onChanged: (r: Reply) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showRaw, setShowRaw] = useState(false);
  const Icon = CHANNEL_ICON[reply.channel];

  async function act(fn: () => Promise<Reply>, msg: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await fn();
      setNotice(msg);
      onChanged(r);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="flex flex-col gap-4 p-5">
      <button type="button" onClick={onBack} className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground lg:hidden">
        <ArrowLeft className="h-4 w-4" /> Back to list
      </button>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold">{reply.company_name || reply.domain || "Unknown company"}</h2>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
            <Icon className="h-3.5 w-3.5" />
            <span className="truncate">
              {reply.from_name ? `${reply.from_name} · ` : ""}
              {reply.from_address ?? "unknown"}
            </span>
            <span>· {formatDateTime(reply.created_at)}</span>
          </div>
        </div>
        <ClassificationBadge value={reply.classification} />
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-md border bg-muted/30 p-3 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-muted-foreground">Campaign</dt>
          <dd className="truncate">{reply.campaign_name ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Variant</dt>
          <dd>{reply.variant_label ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Domain</dt>
          <dd className="truncate">
            {reply.domain ? (
              <a href={`https://${reply.domain}`} target="_blank" rel="noreferrer noopener" className="hover:underline">
                {reply.domain}
              </a>
            ) : (
              "—"
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Channel</dt>
          <dd className="capitalize">{reply.channel}</dd>
        </div>
      </dl>

      {reply.subject && <div className="text-sm font-medium">{reply.subject}</div>}
      <div className="whitespace-pre-wrap break-words rounded-md border bg-background p-4 text-sm leading-relaxed">
        {reply.body || <span className="text-muted-foreground">(empty message)</span>}
      </div>
      {reply.channel === "voicemail" && reply.recording_url && (
        <a
          href={reply.recording_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-primary underline-offset-4 hover:underline"
        >
          <Voicemail className="h-4 w-4" /> Listen to recording
        </a>
      )}
      {reply.raw_body && reply.raw_body !== reply.body && (
        <div>
          <button type="button" className="text-xs text-muted-foreground hover:text-foreground" onClick={() => setShowRaw((s) => !s)}>
            {showRaw ? "Hide" : "Show"} full original message
          </button>
          {showRaw && (
            <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/30 p-3 text-xs">
              {reply.raw_body}
            </pre>
          )}
        </div>
      )}

      <div className="flex items-start gap-2 rounded-md border p-3 text-sm">
        <Bot className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div>
          <div>
            Classified as <strong>{classificationLabel(reply.classification)}</strong> by{" "}
            {reply.classified_by === "llm" ? "AI" : reply.classified_by === "rules" ? "rules" : "you"}
            {reply.confidence_score !== null && reply.classified_by !== "manual"
              ? ` (${formatPercent(reply.confidence_score, 0)} confidence)`
              : ""}
            .
          </div>
          {reply.explanation && <div className="text-muted-foreground">{reply.explanation}</div>}
        </div>
      </div>

      <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:flex-wrap sm:items-center">
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Reclassify</span>
          <NativeSelect
            value={reply.classification ?? ""}
            disabled={busy}
            onChange={(e) => {
              const v = e.target.value;
              if (isClassification(v) && v !== reply.classification) {
                void act(() => api.updateReply(reply.id, { classification: v }), `Moved to ${classificationLabel(v)}.`);
              }
            }}
          >
            {reply.classification === null && (
              <option value="" disabled>
                Sorting…
              </option>
            )}
            {CLASSIFICATIONS.map((c) => (
              <option key={c} value={c}>
                {classificationLabel(c)}
              </option>
            ))}
          </NativeSelect>
        </label>
        <Button
          variant={reply.call_booked ? "secondary" : "outline"}
          size="sm"
          disabled={busy}
          onClick={() =>
            act(
              () => api.updateReply(reply.id, { call_booked: !reply.call_booked }),
              reply.call_booked ? "Call booked mark removed." : "Marked as call booked.",
            )
          }
        >
          <CalendarCheck /> {reply.call_booked ? "Call booked ✓" : "Mark call booked"}
        </Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => act(() => api.forwardReply(reply.id), "Forwarded to your inboxes.")}>
          <Forward /> {reply.is_forwarded ? "Forward again" : "Forward"}
        </Button>
        {reply.submission_id && (
          <Button asChild variant="ghost" size="sm">
            <Link href={`/proofs?submission=${reply.submission_id}`}>
              <Images /> See the form we sent
            </Link>
          </Button>
        )}
      </div>
      {notice && <div className="text-sm text-emerald-700">{notice}</div>}
      <InlineError error={error} />
    </Card>
  );
}

export default function InboxPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <InboxInner />
    </Suspense>
  );
}
