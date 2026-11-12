"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { MousePointerClick, Plus, Reply, Sparkles, Wallet } from "lucide-react";
import { DailyChart, FunnelChart } from "@/components/charts";
import { EmptyState, ErrorState, LoadingState, NativeSelect, PageHeader, statusLabel } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { keys, swrFetcher } from "@/lib/api";
import type { CampaignSummary, OverviewStats, SubmissionStatus } from "@/lib/types";
import { formatCents, formatNumber, formatPercent } from "@/lib/utils";

function Kpi({
  label,
  value,
  sub,
  icon: Icon,
}: {
  label: string;
  value: string;
  sub?: string;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <Card>
      <CardContent className="p-5">
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          {label}
          <Icon className="h-4 w-4" />
        </div>
        <div className="mt-2 text-2xl font-semibold tabular">{value}</div>
        {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function StatusBreakdown({ breakdown }: { breakdown: OverviewStats["status_breakdown"] }) {
  const entries = (Object.entries(breakdown) as [SubmissionStatus, number][])
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((s, [, n]) => s + n, 0);
  if (entries.length === 0) return <EmptyState title="No submissions yet" className="py-8" />;
  return (
    <ul className="flex flex-col gap-2.5">
      {entries.map(([status, n]) => (
        <li key={status} className="text-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="truncate">{statusLabel(status)}</span>
            <span className="tabular text-muted-foreground">
              {formatNumber(n)} · {formatPercent(n / total, 1)}
            </span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className={
                status.startsWith("SUCCESS") || status === "SUBMITTED_UNVERIFIED"
                  ? "h-full bg-emerald-500"
                  : status.startsWith("SKIPPED")
                    ? "h-full bg-amber-400"
                    : status.startsWith("FAILED")
                      ? "h-full bg-red-400"
                      : "h-full bg-gray-400"
              }
              style={{ width: `${(n / total) * 100}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

export default function OverviewPage() {
  const [campaignId, setCampaignId] = useState("");
  const campaigns = useSWR<{ items: CampaignSummary[] }>(keys.campaigns(), swrFetcher);
  const stats = useSWR<OverviewStats>(keys.overview(campaignId || undefined), swrFetcher, { refreshInterval: 30_000 });
  const s = stats.data;

  return (
    <>
      <PageHeader
        title="Overview"
        description="Forms sent, replies and what each positive reply costs."
        actions={
          <>
            <NativeSelect
              aria-label="Filter by campaign"
              value={campaignId}
              onChange={(e) => setCampaignId(e.target.value)}
              className="w-full sm:w-56"
            >
              <option value="">All campaigns</option>
              {campaigns.data?.items.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </NativeSelect>
            <Button asChild>
              <Link href="/campaigns/new">
                <Plus /> New campaign
              </Link>
            </Button>
          </>
        }
      />

      {stats.error ? (
        <Card>
          <ErrorState error={stats.error} onRetry={() => stats.mutate()} />
        </Card>
      ) : !s ? (
        <LoadingState />
      ) : (
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi
              label="Forms sent"
              value={formatNumber(s.forms_sent)}
              sub={`${formatNumber(s.forms_attempted)} attempted`}
              icon={MousePointerClick}
            />
            <Kpi
              label="Response rate"
              value={formatPercent(s.response_rate)}
              sub={`${formatNumber(s.replies)} replies`}
              icon={Reply}
            />
            <Kpi
              label="Positive leads"
              value={formatNumber(s.positive)}
              sub={`${formatPercent(s.positive_rate)} of forms sent · ${formatNumber(s.calls_booked)} calls booked`}
              icon={Sparkles}
            />
            <Kpi
              label="Cost per positive"
              value={formatCents(s.cost_per_positive_cents)}
              sub={`${formatCents(s.spend_cents)} spent · ${formatCents(s.cost_per_reply_cents)} per reply`}
              icon={Wallet}
            />
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader>
                <CardTitle>Conversion funnel</CardTitle>
                <CardDescription>From targets to calls booked. Percentages are relative to targets.</CardDescription>
              </CardHeader>
              <CardContent>
                {s.funnel.length === 0 || s.funnel[0].count === 0 ? (
                  <EmptyState
                    title="Nothing to show yet"
                    description="Create a campaign and add targets to start the funnel."
                    className="py-8"
                  />
                ) : (
                  <FunnelChart data={s.funnel} />
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Reply breakdown</CardTitle>
                <CardDescription>How AI triage sorted every reply.</CardDescription>
              </CardHeader>
              <CardContent>
                <dl className="grid grid-cols-2 gap-3 text-sm">
                  {[
                    ["Positive", s.positive, "text-emerald-700"],
                    ["Not sure", s.not_sure, "text-sky-700"],
                    ["Negative", s.negative, "text-red-700"],
                    ["Automatic", s.automatic, "text-muted-foreground"],
                  ].map(([label, n, cls]) => (
                    <div key={label as string} className="rounded-md border p-3">
                      <dt className="text-muted-foreground">{label}</dt>
                      <dd className={`mt-1 text-xl font-semibold tabular ${cls}`}>{formatNumber(n as number)}</dd>
                    </div>
                  ))}
                </dl>
                <Button asChild variant="outline" className="mt-4 w-full">
                  <Link href="/inbox">Open inbox</Link>
                </Button>
              </CardContent>
            </Card>
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader>
                <CardTitle>Last 30 days</CardTitle>
                <CardDescription>Forms sent (left axis) against replies and positive replies (right axis).</CardDescription>
              </CardHeader>
              <CardContent>
                {s.daily.length === 0 ? <EmptyState title="No activity in the last 30 days" className="py-8" /> : <DailyChart data={s.daily} />}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Submission outcomes</CardTitle>
                <CardDescription>Skipped and failed forms are never charged.</CardDescription>
              </CardHeader>
              <CardContent>
                <StatusBreakdown breakdown={s.status_breakdown} />
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
