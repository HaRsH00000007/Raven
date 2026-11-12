"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { VARIANT_LABELS, type VariantInput, type VariantLabel } from "@/lib/types";
import { cn, renderTemplate } from "@/lib/utils";

export const PLACEHOLDERS = ["company_name", "domain", "sender_name", "sender_company"] as const;

export function emptyVariant(label: VariantLabel, base?: Partial<VariantInput>): VariantInput {
  return {
    label,
    sender_name: base?.sender_name ?? "",
    sender_title: base?.sender_title ?? null,
    subject: base?.subject ?? null,
    message_body: "",
    weight: 1,
  };
}

export function validateVariants(variants: VariantInput[]): string | null {
  if (variants.length < 1 || variants.length > 6) return "Add between one and six message variants.";
  const labels = new Set<string>();
  for (const v of variants) {
    if (labels.has(v.label)) return `Variant ${v.label} appears twice.`;
    labels.add(v.label);
    if (!v.sender_name.trim()) return `Variant ${v.label}: sender name is required.`;
    if (!v.message_body.trim()) return `Variant ${v.label}: message is required.`;
    if (!Number.isFinite(v.weight) || v.weight < 1) return `Variant ${v.label}: weight must be at least 1.`;
  }
  return null;
}

export function cleanVariants(variants: VariantInput[]): VariantInput[] {
  return variants.map((v) => ({
    label: v.label,
    sender_name: v.sender_name.trim(),
    sender_title: v.sender_title?.trim() || null,
    subject: v.subject?.trim() || null,
    message_body: v.message_body.trim(),
    weight: Math.max(1, Math.round(v.weight)),
  }));
}

export interface PreviewContext {
  company_name: string;
  domain: string;
  sender_company: string;
}

/** Shows what one company would receive in its contact form. */
export function VariantPreview({ variant, ctx }: { variant: VariantInput; ctx: PreviewContext }) {
  const vars = {
    company_name: ctx.company_name,
    domain: ctx.domain,
    sender_name: variant.sender_name,
    sender_company: ctx.sender_company,
  };
  const subject = variant.subject ? renderTemplate(variant.subject, vars) : "";
  const body = renderTemplate(variant.message_body, vars);
  const [first, ...rest] = variant.sender_name.trim().split(/\s+/);
  return (
    <div className="rounded-lg border bg-muted/30 p-4 text-sm">
      <div className="mb-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Contact form on {ctx.domain || "example.com"}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <PreviewField label="First name" value={first ?? ""} />
        <PreviewField label="Last name" value={rest.join(" ")} />
        <PreviewField label="Email" value="r-…@replies.yourdomain.com" mono className="col-span-2" />
        <PreviewField label="Company" value={ctx.sender_company} className="col-span-2" />
        {subject && <PreviewField label="Subject" value={subject} className="col-span-2" />}
        <div className="col-span-2">
          <div className="mb-1 text-xs text-muted-foreground">Message</div>
          <div className="min-h-[96px] whitespace-pre-wrap rounded-md border bg-background px-3 py-2">
            {body || <span className="text-muted-foreground">Your message appears here.</span>}
          </div>
        </div>
      </div>
      <div className="mt-2 text-xs text-muted-foreground">
        {body.length} characters{variant.sender_title ? ` · signed as ${variant.sender_title}` : ""}
      </div>
    </div>
  );
}

function PreviewField({ label, value, mono, className }: { label: string; value: string; mono?: boolean; className?: string }) {
  return (
    <div className={className}>
      <div className="mb-1 text-xs text-muted-foreground">{label}</div>
      <div className={cn("truncate rounded-md border bg-background px-3 py-1.5", mono && "font-mono text-xs leading-5")}>
        {value || <span className="text-muted-foreground">—</span>}
      </div>
    </div>
  );
}

export function VariantBuilder({
  variants,
  onChange,
  ctx,
}: {
  variants: VariantInput[];
  onChange: (v: VariantInput[]) => void;
  ctx: PreviewContext;
}) {
  const [active, setActive] = useState<VariantLabel>(variants[0]?.label ?? "A");
  const current = variants.find((v) => v.label === active) ?? variants[0];
  const usedLabels = new Set(variants.map((v) => v.label));
  const nextLabel = VARIANT_LABELS.find((l) => !usedLabels.has(l));

  function update(patch: Partial<VariantInput>) {
    if (!current) return;
    onChange(variants.map((v) => (v.label === current.label ? { ...v, ...patch } : v)));
  }

  function add() {
    if (!nextLabel) return;
    const base = current ?? variants[0];
    onChange([...variants, emptyVariant(nextLabel, base)]);
    setActive(nextLabel);
  }

  function remove(label: VariantLabel) {
    if (variants.length <= 1) return;
    const rest = variants.filter((v) => v.label !== label);
    onChange(rest);
    if (active === label) setActive(rest[0].label);
  }

  function insertPlaceholder(key: string) {
    if (!current) return;
    update({ message_body: `${current.message_body}{{${key}}}` });
  }

  const totalWeight = variants.reduce((s, v) => s + (v.weight || 0), 0) || 1;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        {variants.map((v) => (
          <button
            key={v.label}
            type="button"
            onClick={() => setActive(v.label)}
            className={cn(
              "flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors",
              v.label === active ? "border-primary bg-primary text-primary-foreground" : "bg-background hover:bg-secondary",
            )}
          >
            Variant {v.label}
            <span className={cn("text-xs tabular", v.label === active ? "text-primary-foreground/70" : "text-muted-foreground")}>
              {Math.round((v.weight / totalWeight) * 100)}%
            </span>
          </button>
        ))}
        {nextLabel && (
          <Button type="button" variant="outline" size="sm" onClick={add}>
            <Plus /> Add variant {nextLabel}
          </Button>
        )}
      </div>

      {current && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <div>
                <CardTitle>Variant {current.label}</CardTitle>
                <CardDescription>Short, plain questions get the most replies.</CardDescription>
              </div>
              {variants.length > 1 && (
                <Button type="button" variant="ghost" size="sm" onClick={() => remove(current.label)}>
                  <Trash2 /> Remove
                </Button>
              )}
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={`sn-${current.label}`}>Sender name</Label>
                  <Input
                    id={`sn-${current.label}`}
                    value={current.sender_name}
                    onChange={(e) => update({ sender_name: e.target.value })}
                    placeholder="Rohit Singh"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={`st-${current.label}`}>Sender title (optional)</Label>
                  <Input
                    id={`st-${current.label}`}
                    value={current.sender_title ?? ""}
                    onChange={(e) => update({ sender_title: e.target.value })}
                    placeholder="Partnerships"
                  />
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_120px]">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={`sub-${current.label}`}>Subject (used if the form has one)</Label>
                  <Input
                    id={`sub-${current.label}`}
                    value={current.subject ?? ""}
                    onChange={(e) => update({ subject: e.target.value })}
                    placeholder="Quick question"
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor={`w-${current.label}`}>Weight</Label>
                  <Input
                    id={`w-${current.label}`}
                    type="number"
                    min={1}
                    max={100}
                    value={current.weight}
                    onChange={(e) => update({ weight: Number(e.target.value) || 1 })}
                  />
                </div>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor={`msg-${current.label}`}>Message</Label>
                <Textarea
                  id={`msg-${current.label}`}
                  rows={7}
                  value={current.message_body}
                  onChange={(e) => update({ message_body: e.target.value })}
                  placeholder={"Are you hiring recruiters this quarter?\n\n– {{sender_name}}, {{sender_company}}"}
                />
                <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  Insert:
                  {PLACEHOLDERS.map((p) => (
                    <button
                      key={p}
                      type="button"
                      onClick={() => insertPlaceholder(p)}
                      className="rounded border bg-muted px-1.5 py-0.5 font-mono hover:bg-secondary"
                    >
                      {`{{${p}}}`}
                    </button>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
          <div className="flex flex-col gap-2">
            <div className="text-sm font-medium">Live preview</div>
            <VariantPreview variant={current} ctx={ctx} />
          </div>
        </div>
      )}
    </div>
  );
}
