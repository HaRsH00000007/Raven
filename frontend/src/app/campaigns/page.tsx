"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { Megaphone, Plus } from "lucide-react";
import { CampaignStatusBadge, EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { keys, swrFetcher } from "@/lib/api";
import type { CampaignSummary } from "@/lib/types";
import { formatDate, formatNumber, formatPercent } from "@/lib/utils";

export default function CampaignsPage() {
  const router = useRouter();
  const { data, error, mutate } = useSWR<{ items: CampaignSummary[] }>(keys.campaigns(), swrFetcher, {
    refreshInterval: 15_000,
  });

  return (
    <>
      <PageHeader
        title="Campaigns"
        description="Each campaign is a target list plus up to six message variants."
        actions={
          <Button asChild>
            <Link href="/campaigns/new">
              <Plus /> New campaign
            </Link>
          </Button>
        }
      />
      <Card>
        {error ? (
          <ErrorState error={error} onRetry={() => mutate()} />
        ) : !data ? (
          <LoadingState />
        ) : data.items.length === 0 ? (
          <EmptyState
            icon={Megaphone}
            title="No campaigns yet"
            description="Upload a list of company domains and write a short message to get started."
            action={
              <Button asChild>
                <Link href="/campaigns/new">Create your first campaign</Link>
              </Button>
            }
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Targets</TableHead>
                <TableHead className="text-right">Pending</TableHead>
                <TableHead className="text-right">Sent</TableHead>
                <TableHead className="text-right">Replies</TableHead>
                <TableHead className="text-right">Positive</TableHead>
                <TableHead className="text-right">Positive rate</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((c) => (
                <TableRow key={c.id} className="cursor-pointer" onClick={() => router.push(`/campaigns/${c.id}`)}>
                  <TableCell className="font-medium">
                    <Link href={`/campaigns/${c.id}`} className="hover:underline" onClick={(e) => e.stopPropagation()}>
                      {c.name}
                    </Link>
                    {c.sender_company && <div className="text-xs text-muted-foreground">{c.sender_company}</div>}
                  </TableCell>
                  <TableCell>
                    <CampaignStatusBadge status={c.status} />
                  </TableCell>
                  <TableCell className="text-right tabular">{formatNumber(c.targets)}</TableCell>
                  <TableCell className="text-right tabular">{formatNumber(c.pending)}</TableCell>
                  <TableCell className="text-right tabular">{formatNumber(c.sent)}</TableCell>
                  <TableCell className="text-right tabular">{formatNumber(c.replies)}</TableCell>
                  <TableCell className="text-right tabular">{formatNumber(c.positive)}</TableCell>
                  <TableCell className="text-right tabular">{c.sent ? formatPercent(c.positive / c.sent) : "—"}</TableCell>
                  <TableCell className="whitespace-nowrap text-muted-foreground">{formatDate(c.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </>
  );
}
