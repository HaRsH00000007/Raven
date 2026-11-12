"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { mutate } from "swr";
import { Check, ChevronLeft, ChevronRight, Rocket, Save } from "lucide-react";
import { InlineError, PageHeader } from "@/components/common";
import { EMPTY_TARGETS, TargetsInput, type TargetsValue } from "@/components/targets-input";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { cleanVariants, emptyVariant, validateVariants, VariantBuilder, VariantPreview } from "@/components/variant-builder";
import { api, keys } from "@/lib/api";
import type { VariantInput } from "@/lib/types";
import { cn, formatNumber } from "@/lib/utils";

const STEPS = ["Details & sender", "Targets", "Messages", "Review & launch"] as const;

export default function NewCampaignPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [senderCompany, setSenderCompany] = useState("");
  const [senderWebsite, setSenderWebsite] = useState("");
  const [senderPhone, setSenderPhone] = useState("");
  const [targets, setTargets] = useState<TargetsValue>(EMPTY_TARGETS);
  const [variants, setVariants] = useState<VariantInput[]>([emptyVariant("A")]);
  const [dryRun, setDryRun] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [progress, setProgress] = useState<string | null>(null);

  const sample = targets.parsed.valid[0];
  const ctx = useMemo(
    () => ({
      company_name: sample?.company_name || (sample ? sample.domain.split(".")[0].replace(/^\w/, (c) => c.toUpperCase()) : "Acme Staffing"),
      domain: sample?.domain ?? "acmestaffing.com",
      sender_company: senderCompany || "Your company",
    }),
    [sample, senderCompany],
  );

  function stepError(i: number): string | null {
    if (i === 0) {
      if (!name.trim()) return "Give the campaign a name.";
      if (!senderCompany.trim()) return "Sender company is used to fill the 'Company' field on forms.";
      if (senderWebsite.trim() && !/^https?:\/\/\S+\.\S+/.test(senderWebsite.trim()))
        return "Website should start with http:// or https://";
    }
    if (i === 1 && targets.parsed.valid.length === 0) return "Add at least one valid domain.";
    if (i === 2) return validateVariants(variants);
    return null;
  }

  function next() {
    const e = stepError(step);
    if (e) {
      setError(new Error(e));
      return;
    }
    setError(null);
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  }

  async function submit(launch: boolean) {
    for (let i = 0; i < 3; i++) {
      const e = stepError(i);
      if (e) {
        setStep(i);
        setError(new Error(e));
        return;
      }
    }
    setSubmitting(true);
    setError(null);
    try {
      setProgress("Creating campaign…");
      const useCsv = targets.mode === "csv" && targets.file;
      const campaign = await api.createCampaign({
        name: name.trim(),
        sender_company: senderCompany.trim() || null,
        sender_website: senderWebsite.trim() || null,
        sender_phone: senderPhone.trim() || null,
        variants: cleanVariants(variants),
        domains: useCsv ? [] : targets.parsed.valid.map((t) => t.domain),
      });
      if (useCsv && targets.file) {
        setProgress("Uploading targets…");
        await api.addTargetsCsv(campaign.id, targets.file);
      }
      if (launch) {
        setProgress(dryRun ? "Starting dry run…" : "Launching…");
        await api.launchCampaign(campaign.id, dryRun);
      }
      await mutate(keys.campaigns());
      router.push(`/campaigns/${campaign.id}`);
    } catch (e) {
      setError(e);
      setSubmitting(false);
      setProgress(null);
    }
  }

  return (
    <>
      <PageHeader title="New campaign" description="Four steps: who it's from, who it goes to, what it says, then launch." />

      <ol className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {STEPS.map((label, i) => (
          <li key={label}>
            <button
              type="button"
              disabled={i > step || submitting}
              onClick={() => {
                setError(null);
                setStep(i);
              }}
              className={cn(
                "flex w-full items-center gap-2 rounded-md border px-3 py-2 text-left text-sm transition-colors disabled:cursor-default",
                i === step ? "border-primary bg-background font-medium" : i < step ? "bg-background hover:bg-secondary" : "bg-muted/50 text-muted-foreground",
              )}
            >
              <span
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs",
                  i < step ? "bg-emerald-600 text-white" : i === step ? "bg-primary text-primary-foreground" : "bg-muted-foreground/20",
                )}
              >
                {i < step ? <Check className="h-3 w-3" /> : i + 1}
              </span>
              <span className="truncate">{label}</span>
            </button>
          </li>
        ))}
      </ol>

      {step === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Campaign details</CardTitle>
            <CardDescription>The sender company, website and phone are entered into matching form fields.</CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <Label htmlFor="name">Campaign name</Label>
              <Input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="US staffing agencies · Nov" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="company">Sender company</Label>
              <Input id="company" value={senderCompany} onChange={(e) => setSenderCompany(e.target.value)} placeholder="Glocal RPO" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="website">Sender website (optional)</Label>
              <Input id="website" value={senderWebsite} onChange={(e) => setSenderWebsite(e.target.value)} placeholder="https://glocalrpo.com" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="phone">Sender phone (optional)</Label>
              <Input id="phone" value={senderPhone} onChange={(e) => setSenderPhone(e.target.value)} placeholder="+1 555 123 4567" />
              <p className="text-xs text-muted-foreground">Use your Raven number so calls and texts are triaged too.</p>
            </div>
          </CardContent>
        </Card>
      )}

      {step === 1 && (
        <Card>
          <CardHeader>
            <CardTitle>Targets</CardTitle>
            <CardDescription>
              Companies contacted in the last 90 days, or on your do-not-contact list, are skipped automatically.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <TargetsInput value={targets} onChange={setTargets} />
          </CardContent>
        </Card>
      )}

      {step === 2 && <VariantBuilder variants={variants} onChange={setVariants} ctx={ctx} />}

      {step === 3 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Review</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4 text-sm">
              <dl className="grid grid-cols-[140px_1fr] gap-y-2">
                <dt className="text-muted-foreground">Name</dt>
                <dd className="font-medium">{name}</dd>
                <dt className="text-muted-foreground">Sender company</dt>
                <dd>{senderCompany || "—"}</dd>
                <dt className="text-muted-foreground">Website</dt>
                <dd className="truncate">{senderWebsite || "—"}</dd>
                <dt className="text-muted-foreground">Phone</dt>
                <dd>{senderPhone || "—"}</dd>
                <dt className="text-muted-foreground">Targets</dt>
                <dd className="tabular">{formatNumber(targets.parsed.valid.length)} domains</dd>
                <dt className="text-muted-foreground">Variants</dt>
                <dd>{variants.map((v) => v.label).join(", ")} (split by weight)</dd>
              </dl>
              <div className="flex items-start justify-between gap-4 rounded-lg border p-4">
                <div>
                  <div className="font-medium">Dry run</div>
                  <p className="text-muted-foreground">
                    Fills each form and saves a screenshot, but never clicks submit. Nothing is sent and no credits are used.
                  </p>
                </div>
                <Switch checked={dryRun} onCheckedChange={setDryRun} aria-label="Dry run" />
              </div>
              {!dryRun && (
                <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-amber-900">
                  This will submit real contact forms to {formatNumber(targets.parsed.valid.length)} companies and use one credit per
                  form that sends.
                </div>
              )}
            </CardContent>
          </Card>
          <div className="flex flex-col gap-3">
            <div className="text-sm font-medium">What {ctx.domain} receives (variant {variants[0].label})</div>
            <VariantPreview variant={variants[0]} ctx={ctx} />
          </div>
        </div>
      )}

      <div className="mt-4">
        <InlineError error={error} />
      </div>

      <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between">
        <Button variant="outline" onClick={() => (step === 0 ? router.push("/campaigns") : setStep(step - 1))} disabled={submitting}>
          <ChevronLeft /> {step === 0 ? "Cancel" : "Back"}
        </Button>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          {progress && <span className="text-sm text-muted-foreground">{progress}</span>}
          {step < STEPS.length - 1 ? (
            <Button onClick={next}>
              Continue <ChevronRight />
            </Button>
          ) : (
            <>
              <Button variant="outline" onClick={() => submit(false)} disabled={submitting}>
                <Save /> Save as draft
              </Button>
              <Button onClick={() => submit(true)} disabled={submitting}>
                <Rocket /> {dryRun ? "Create & start dry run" : "Create & launch"}
              </Button>
            </>
          )}
        </div>
      </div>
    </>
  );
}
