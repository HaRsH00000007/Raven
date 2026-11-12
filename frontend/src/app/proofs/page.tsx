"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import useSWR from "swr";
import { ExternalLink, ImageOff, Images, Search } from "lucide-react";
import {
  ClassificationBadge,
  EmptyState,
  ErrorState,
  LoadingState,
  NativeSelect,
  PageHeader,
  Pagination,
  statusLabel,
  SubmissionStatusBadge,
} from "@/components/common";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { keys, screenshotUrl, swrFetcher } from "@/lib/api";
import { SUBMISSION_STATUSES, type CampaignSummary, type Paginated, type Submission, type SubmissionStatus } from "@/lib/types";
import { formatDateTime, relativeTime } from "@/lib/utils";

const LIMIT = 50;

function isStatus(v: string | null): v is SubmissionStatus {
  return v !== null && (SUBMISSION_STATUSES as readonly string[]).includes(v);
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function ProofsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const initialStatus = params.get("status");
  const [campaignId, setCampaignId] = useState(params.get("campaign_id") ?? "");
  const [status, setStatus] = useState<SubmissionStatus | "">(isStatus(initialStatus) ? initialStatus : "");
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<string | null>(params.get("submission"));
  const q = useDebounced(search.trim(), 300);

  useEffect(() => setOffset(0), [q, status, campaignId]);

  useEffect(() => {
    const qs = new URLSearchParams();
    if (campaignId) qs.set("campaign_id", campaignId);
    if (status) qs.set("status", status);
    if (q) qs.set("q", q);
    if (openId) qs.set("submission", openId);
    const s = qs.toString();
    router.replace(s ? `/proofs?${s}` : "/proofs", { scroll: false });
  }, [campaignId, status, q, openId, router]);

  const campaigns = useSWR<{ items: CampaignSummary[] }>(keys.campaigns(), swrFetcher);
  const list = useSWR<Paginated<Submission>>(
    keys.submissions({ campaign_id: campaignId || undefined, status, q: q || undefined, limit: LIMIT, offset }),
    swrFetcher,
    { refreshInterval: 15_000, keepPreviousData: true },
  );

  return (
    <>
      <PageHeader title="Proofs" description="A screenshot of exactly what each company received, before and after submit." />

      <Card>
        <div className="flex flex-col gap-2 border-b p-3 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search domain…" className="pl-9" aria-label="Search domain" />
          </div>
          <NativeSelect aria-label="Filter by status" value={status} onChange={(e) => setStatus(isStatus(e.target.value) ? e.target.value : "")} className="sm:w-56">
            <option value="">All statuses</option>
            {SUBMISSION_STATUSES.map((s) => (
              <option key={s} value={s}>
                {statusLabel(s)}
              </option>
            ))}
          </NativeSelect>
          <NativeSelect aria-label="Filter by campaign" value={campaignId} onChange={(e) => setCampaignId(e.target.value)} className="sm:w-56">
            <option value="">All campaigns</option>
            {campaigns.data?.items.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </NativeSelect>
        </div>

        {list.error ? (
          <ErrorState error={list.error} onRetry={() => list.mutate()} />
        ) : !list.data ? (
          <LoadingState />
        ) : list.data.items.length === 0 ? (
          <EmptyState icon={Images} title="No submissions match" description="Launch a campaign (a dry run works too) to see proofs here." />
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Company</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Campaign</TableHead>
                  <TableHead>Variant</TableHead>
                  <TableHead>Reply</TableHead>
                  <TableHead>When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.data.items.map((s) => (
                  <TableRow key={s.id} className="cursor-pointer" onClick={() => setOpenId(s.id)}>
                    <TableCell>
                      <div className="font-medium">{s.domain}</div>
                      {s.company_name && <div className="text-xs text-muted-foreground">{s.company_name}</div>}
                    </TableCell>
                    <TableCell>
                      <SubmissionStatusBadge status={s.status} />
                    </TableCell>
                    <TableCell className="max-w-[200px] truncate">{s.campaign_name}</TableCell>
                    <TableCell>{s.variant_label}</TableCell>
                    <TableCell>{s.latest_classification ? <ClassificationBadge value={s.latest_classification} /> : <span className="text-muted-foreground">—</span>}</TableCell>
                    <TableCell className="whitespace-nowrap text-muted-foreground">{relativeTime(s.submitted_at ?? s.created_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pagination total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>

      <Dialog open={openId !== null} onOpenChange={(o) => !o && setOpenId(null)}>
        <DialogContent className="max-w-6xl">{openId && <ProofDetail id={openId} fallback={list.data?.items.find((s) => s.id === openId)} />}</DialogContent>
      </Dialog>
    </>
  );
}

function Screenshot({ id, which, available }: { id: string; which: "pre" | "post"; available: boolean }) {
  const [failed, setFailed] = useState(false);
  const url = screenshotUrl(id, which);
  return (
    <figure className="flex min-w-0 flex-col gap-2">
      <figcaption className="flex items-center justify-between text-sm font-medium">
        {which === "pre" ? "Before submit · form filled in" : "After submit · page response"}
        {available && !failed && (
          <a href={url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs font-normal text-muted-foreground hover:text-foreground">
            Open <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </figcaption>
      {available && !failed ? (
        <a href={url} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-md border bg-muted/30">
          {/* eslint-disable-next-line @next/next/no-img-element -- authenticated proxy image, not optimizable */}
          <img src={url} alt={`${which === "pre" ? "Pre" : "Post"}-submit screenshot`} className="max-h-[60vh] w-full object-contain object-top" onError={() => setFailed(true)} />
        </a>
      ) : (
        <div className="flex h-48 flex-col items-center justify-center gap-2 rounded-md border border-dashed text-sm text-muted-foreground">
          <ImageOff className="h-5 w-5" />
          {failed ? "Screenshot could not be loaded" : "No screenshot"}
        </div>
      )}
    </figure>
  );
}

function ProofDetail({ id, fallback }: { id: string; fallback?: Submission }) {
  const { data, error } = useSWR<Submission>(keys.submission(id), swrFetcher, { fallbackData: fallback });
  if (error && !data) return <ErrorState error={error} />;
  if (!data) return <LoadingState />;
  const s = data;
  const fields = Object.entries(s.fields_filled ?? {});
  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex flex-wrap items-center gap-2">
          {s.domain} <SubmissionStatusBadge status={s.status} />
        </DialogTitle>
        <DialogDescription>
          {s.campaign_name} · Variant {s.variant_label} · {formatDateTime(s.submitted_at ?? s.created_at)}
          {s.dry_run ? " · dry run (not submitted)" : ""}
        </DialogDescription>
      </DialogHeader>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Screenshot id={s.id} which="pre" available={s.has_pre_screenshot} />
        <Screenshot id={s.id} which="post" available={s.has_post_screenshot} />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="flex flex-col gap-2 text-sm">
          <div className="font-medium">Details</div>
          <dl className="grid grid-cols-[120px_1fr] gap-y-1.5">
            <dt className="text-muted-foreground">Contact page</dt>
            <dd className="min-w-0 truncate">
              {s.contact_url ? (
                <a href={s.contact_url} target="_blank" rel="noreferrer noopener" className="hover:underline">
                  {s.contact_url}
                </a>
              ) : (
                "—"
              )}
            </dd>
            <dt className="text-muted-foreground">Reply address</dt>
            <dd className="min-w-0 truncate font-mono text-xs leading-5">{s.unique_reply_email}</dd>
            <dt className="text-muted-foreground">Outcome</dt>
            <dd>{statusLabel(s.status)}</dd>
            <dt className="text-muted-foreground">Detail</dt>
            <dd className="break-words">{s.detail ?? "—"}</dd>
            <dt className="text-muted-foreground">Replies</dt>
            <dd className="flex items-center gap-2">
              {s.reply_count}
              {s.latest_classification && <ClassificationBadge value={s.latest_classification} />}
            </dd>
          </dl>
        </div>
        <div className="flex flex-col gap-2 text-sm">
          <div className="font-medium">Fields filled</div>
          {fields.length === 0 ? (
            <div className="text-muted-foreground">No fields were filled.</div>
          ) : (
            <dl className="divide-y rounded-md border">
              {fields.map(([k, v]) => (
                <div key={k} className="grid grid-cols-[110px_1fr] gap-2 px-3 py-2">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd className="whitespace-pre-wrap break-words">{v}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </div>
    </>
  );
}

export default function ProofsPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <ProofsInner />
    </Suspense>
  );
}
