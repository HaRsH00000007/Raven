"use client";

import Link from "next/link";
import { useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import { Images, Inbox, Pause, Pencil, Play, Plus, Rocket } from "lucide-react";
import { CampaignStatusBadge, ErrorState, InlineError, LoadingState, PageHeader } from "@/components/common";
import { EMPTY_TARGETS, TargetsInput, type TargetsValue } from "@/components/targets-input";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cleanVariants, validateVariants, VariantBuilder } from "@/components/variant-builder";
import { api, keys, swrFetcher } from "@/lib/api";
import type { CampaignDetail, TargetsResult, VariantInput } from "@/lib/types";
import { formatDateTime, formatNumber, formatPercent } from "@/lib/utils";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="mt-1 text-xl font-semibold tabular">{value}</div>
      </CardContent>
    </Card>
  );
}

export default function CampaignDetailPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const { data: c, error, mutate } = useSWR<CampaignDetail>(keys.campaign(id), swrFetcher, { refreshInterval: 10_000 });
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [launchOpen, setLaunchOpen] = useState(false);
  const [targetsOpen, setTargetsOpen] = useState(false);
  const [variantsOpen, setVariantsOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);

  async function run<T>(fn: () => Promise<T>, msg?: (r: T) => string) {
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const r = await fn();
      if (msg) setNotice(msg(r));
      await mutate();
      await globalMutate(keys.campaigns());
      return r;
    } catch (e) {
      setActionError(e);
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrorState error={error} onRetry={() => mutate()} />;
  if (!c) return <LoadingState />;

  const positiveRate = c.sent ? c.positive / c.sent : null;

  return (
    <>
      <PageHeader
        title={c.name}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <CampaignStatusBadge status={c.status} />
            <span>
              {c.sender_company ?? "No sender company"}
              {c.sender_website ? ` · ${c.sender_website}` : ""}
              {c.sender_phone ? ` · ${c.sender_phone}` : ""}
            </span>
            <span>· created {formatDateTime(c.created_at)}</span>
          </span>
        }
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => setDetailsOpen(true)}>
              <Pencil /> Edit details
            </Button>
            <Button variant="outline" size="sm" onClick={() => setTargetsOpen(true)}>
              <Plus /> Add targets
            </Button>
            {(c.status === "draft" || c.status === "completed") && (
              <Button size="sm" onClick={() => setLaunchOpen(true)} disabled={busy}>
                <Rocket /> Launch
              </Button>
            )}
            {c.status === "running" && (
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => run(() => api.pauseCampaign(id), () => "Campaign paused.")}>
                <Pause /> Pause
              </Button>
            )}
            {c.status === "paused" && (
              <Button
                size="sm"
                disabled={busy}
                onClick={() => run(() => api.resumeCampaign(id), (r) => `Resumed · ${formatNumber(r.queued)} forms queued.`)}
              >
                <Play /> Resume
              </Button>
            )}
          </>
        }
      />

      {(notice || actionError) && (
        <div className="mb-4 flex flex-col gap-2">
          {notice && <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">{notice}</div>}
          <InlineError error={actionError} />
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Targets" value={formatNumber(c.targets)} />
        <Stat label="Pending" value={formatNumber(c.pending)} />
        <Stat label="Forms sent" value={formatNumber(c.sent)} />
        <Stat label="Replies" value={formatNumber(c.replies)} />
        <Stat label="Positive" value={formatNumber(c.positive)} />
        <Stat label="Positive rate" value={formatPercent(positiveRate)} />
      </div>

      <Card className="mt-6">
        <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
          <div>
            <CardTitle>Message variants</CardTitle>
            <CardDescription>Forms are split across variants by weight. Edits apply to forms not yet sent.</CardDescription>
          </div>
          <Button variant="outline" size="sm" onClick={() => setVariantsOpen(true)}>
            <Pencil /> Edit variants
          </Button>
        </CardHeader>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Variant</TableHead>
              <TableHead>Message</TableHead>
              <TableHead className="text-right">Weight</TableHead>
              <TableHead className="text-right">Sent</TableHead>
              <TableHead className="text-right">Replies</TableHead>
              <TableHead className="text-right">Positive</TableHead>
              <TableHead className="text-right">Positive rate</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {c.variants.map((v) => {
              const best = c.variants.length > 1 && v.sent > 0 && v.positive_rate === Math.max(...c.variants.map((x) => x.positive_rate ?? 0)) && v.positive > 0;
              return (
                <TableRow key={v.id}>
                  <TableCell className="font-semibold">
                    {v.label}
                    {best && <span className="ml-2 rounded bg-emerald-100 px-1.5 py-0.5 text-xs font-medium text-emerald-800">Leading</span>}
                  </TableCell>
                  <TableCell className="max-w-md">
                    <div className="line-clamp-2 whitespace-pre-wrap text-sm">{v.message_body}</div>
                    <div className="mt-0.5 text-xs text-muted-foreground">
                      {v.sender_name}
                      {v.sender_title ? `, ${v.sender_title}` : ""}
                      {v.subject ? ` · “${v.subject}”` : ""}
                    </div>
                  </TableCell>
                  <TableCell className="text-right tabular">{v.weight}</TableCell>
                  <TableCell className="text-right tabular">{formatNumber(v.sent)}</TableCell>
                  <TableCell className="text-right tabular">{formatNumber(v.replies)}</TableCell>
                  <TableCell className="text-right tabular">{formatNumber(v.positive)}</TableCell>
                  <TableCell className="text-right tabular">{v.sent ? formatPercent(v.positive_rate) : "—"}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Card>

      <div className="mt-6 flex flex-wrap gap-2">
        <Button asChild variant="outline">
          <Link href={`/proofs?campaign_id=${c.id}`}>
            <Images /> View proofs
          </Link>
        </Button>
        <Button asChild variant="outline">
          <Link href={`/inbox?campaign_id=${c.id}`}>
            <Inbox /> View replies
          </Link>
        </Button>
      </div>

      <LaunchDialog
        open={launchOpen}
        onOpenChange={setLaunchOpen}
        targets={c.targets}
        busy={busy}
        onLaunch={async (dryRun) => {
          const r = await run(
            () => api.launchCampaign(id, dryRun),
            (res) => `${dryRun ? "Dry run started" : "Launched"} · ${formatNumber(res.queued)} forms queued.`,
          );
          if (r) setLaunchOpen(false);
        }}
      />
      <AddTargetsDialog
        open={targetsOpen}
        onOpenChange={setTargetsOpen}
        campaignId={id}
        onDone={async (r) => {
          setNotice(
            `${formatNumber(r.added)} targets added${r.duplicates ? `, ${r.duplicates} duplicates ignored` : ""}${
              r.invalid.length ? `, ${r.invalid.length} invalid` : ""
            }.`,
          );
          await mutate();
        }}
      />
      {variantsOpen && (
        <EditVariantsDialog
          campaign={c}
          onClose={() => setVariantsOpen(false)}
          onSaved={async () => {
            setNotice("Variants saved.");
            await mutate();
          }}
        />
      )}
      {detailsOpen && (
        <EditDetailsDialog
          campaign={c}
          onClose={() => setDetailsOpen(false)}
          onSaved={async () => {
            setNotice("Campaign updated.");
            await mutate();
            await globalMutate(keys.campaigns());
          }}
        />
      )}
    </>
  );
}

function LaunchDialog({
  open,
  onOpenChange,
  targets,
  busy,
  onLaunch,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  targets: number;
  busy: boolean;
  onLaunch: (dryRun: boolean) => void;
}) {
  const [dryRun, setDryRun] = useState(true);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Launch campaign</DialogTitle>
          <DialogDescription>{formatNumber(targets)} targets. Skipped and failed forms are never charged.</DialogDescription>
        </DialogHeader>
        <div className="flex items-start justify-between gap-4 rounded-lg border p-4 text-sm">
          <div>
            <div className="font-medium">Dry run</div>
            <p className="text-muted-foreground">Fill forms and screenshot them without clicking submit.</p>
          </div>
          <Switch checked={dryRun} onCheckedChange={setDryRun} aria-label="Dry run" />
        </div>
        {!dryRun && (
          <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            Real contact forms will be submitted and credits used.
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => onLaunch(dryRun)} disabled={busy}>
            <Rocket /> {dryRun ? "Start dry run" : "Launch"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AddTargetsDialog({
  open,
  onOpenChange,
  campaignId,
  onDone,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  campaignId: string;
  onDone: (r: TargetsResult) => void;
}) {
  const [value, setValue] = useState<TargetsValue>(EMPTY_TARGETS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit() {
    if (value.parsed.valid.length === 0) {
      setError(new Error("Add at least one valid domain."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r =
        value.mode === "csv" && value.file
          ? await api.addTargetsCsv(campaignId, value.file)
          : await api.addTargets(campaignId, value.parsed.valid.map((t) => t.domain));
      onDone(r);
      setValue(EMPTY_TARGETS);
      onOpenChange(false);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add targets</DialogTitle>
          <DialogDescription>If the campaign is running, new targets are queued right away.</DialogDescription>
        </DialogHeader>
        <TargetsInput value={value} onChange={setValue} />
        <InlineError error={error} />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={busy || value.parsed.valid.length === 0}>
            Add {formatNumber(value.parsed.valid.length)} targets
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditVariantsDialog({
  campaign,
  onClose,
  onSaved,
}: {
  campaign: CampaignDetail;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [variants, setVariants] = useState<VariantInput[]>(
    campaign.variants.map(({ label, sender_name, sender_title, subject, message_body, weight }) => ({
      label,
      sender_name,
      sender_title,
      subject,
      message_body,
      weight,
    })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function save() {
    const v = validateVariants(variants);
    if (v) {
      setError(new Error(v));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.replaceVariants(campaign.id, cleanVariants(variants));
      onSaved();
      onClose();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-5xl">
        <DialogHeader>
          <DialogTitle>Edit variants</DialogTitle>
          <DialogDescription>Forms already sent keep the variant they were sent with.</DialogDescription>
        </DialogHeader>
        <VariantBuilder
          variants={variants}
          onChange={setVariants}
          ctx={{ company_name: "Acme Staffing", domain: "acmestaffing.com", sender_company: campaign.sender_company ?? "Your company" }}
        />
        <InlineError error={error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy}>
            Save variants
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditDetailsDialog({
  campaign,
  onClose,
  onSaved,
}: {
  campaign: CampaignDetail;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(campaign.name);
  const [company, setCompany] = useState(campaign.sender_company ?? "");
  const [website, setWebsite] = useState(campaign.sender_website ?? "");
  const [phone, setPhone] = useState(campaign.sender_phone ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function save() {
    if (!name.trim()) {
      setError(new Error("Name is required."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.updateCampaign(campaign.id, {
        name: name.trim(),
        sender_company: company.trim() || null,
        sender_website: website.trim() || null,
        sender_phone: phone.trim() || null,
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit campaign</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          {[
            ["Name", name, setName, "c-name"],
            ["Sender company", company, setCompany, "c-company"],
            ["Sender website", website, setWebsite, "c-website"],
            ["Sender phone", phone, setPhone, "c-phone"],
          ].map(([label, val, set, htmlId]) => (
            <div key={htmlId as string} className="flex flex-col gap-1.5">
              <Label htmlFor={htmlId as string}>{label as string}</Label>
              <Input
                id={htmlId as string}
                value={val as string}
                onChange={(e) => (set as (v: string) => void)(e.target.value)}
              />
            </div>
          ))}
        </div>
        <InlineError error={error} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
