"use client";

import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { CampaignStatus, Classification, SubmissionStatus } from "@/lib/types";
import { cn, errorText } from "@/lib/utils";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function LoadingState({ label = "Loading…", className }: { label?: string; className?: string }) {
  return (
    <div className={cn("flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground", className)}>
      <Loader2 className="h-4 w-4 animate-spin" /> {label}
    </div>
  );
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 py-12 text-center", className)}>
      <AlertTriangle className="h-6 w-6 text-destructive" />
      <div className="max-w-md text-sm text-muted-foreground">{errorText(error)}</div>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon: Icon = Inbox,
  className,
}: {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  icon?: React.ComponentType<{ className?: string }>;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 py-12 text-center", className)}>
      <Icon className="h-6 w-6 text-muted-foreground" />
      <div className="text-sm font-medium">{title}</div>
      {description && <div className="max-w-md text-sm text-muted-foreground">{description}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function InlineError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
      {errorText(error)}
    </div>
  );
}

const STATUS_META: Record<SubmissionStatus, { label: string; variant: BadgeProps["variant"] }> = {
  PENDING: { label: "Pending", variant: "muted" },
  SUCCESS_VERIFIED: { label: "Sent · verified", variant: "success" },
  SUBMITTED_UNVERIFIED: { label: "Sent · unverified", variant: "info" },
  DRY_RUN: { label: "Dry run", variant: "secondary" },
  SKIPPED_CAPTCHA: { label: "Skipped · CAPTCHA", variant: "warning" },
  SKIPPED_NO_SOLICITATION: { label: "Skipped · no solicitation", variant: "warning" },
  SKIPPED_ROBOTS: { label: "Skipped · robots.txt", variant: "warning" },
  SKIPPED_COOLDOWN: { label: "Skipped · cooldown", variant: "warning" },
  SKIPPED_DNC: { label: "Skipped · do not contact", variant: "warning" },
  FAILED_NO_FORM: { label: "No form found", variant: "danger" },
  FAILED_VALIDATION: { label: "Form rejected", variant: "danger" },
  FAILED_ERROR: { label: "Error", variant: "danger" },
};

export function statusLabel(s: SubmissionStatus): string {
  return STATUS_META[s]?.label ?? s;
}

export function SubmissionStatusBadge({ status }: { status: SubmissionStatus }) {
  const meta = STATUS_META[status] ?? { label: status, variant: "outline" as const };
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

const CLASS_META: Record<Classification, { label: string; variant: BadgeProps["variant"]; dot: string }> = {
  positive: { label: "Positive", variant: "success", dot: "bg-emerald-500" },
  not_sure: { label: "Not sure", variant: "info", dot: "bg-sky-500" },
  negative: { label: "Negative", variant: "danger", dot: "bg-red-500" },
  automatic: { label: "Automatic", variant: "muted", dot: "bg-gray-400" },
};

// Replies are briefly unclassified (null) between arriving and being triaged.
const UNSORTED = { label: "Sorting…", variant: "outline" as BadgeProps["variant"], dot: "bg-gray-300" };

export function classificationLabel(c: Classification | null): string {
  return (c && CLASS_META[c]?.label) || UNSORTED.label;
}

export function ClassificationBadge({ value }: { value: Classification | null }) {
  const meta = (value && CLASS_META[value]) || UNSORTED;
  return (
    <Badge variant={meta.variant}>
      <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} aria-hidden />
      {meta.label}
    </Badge>
  );
}

const CAMPAIGN_META: Record<CampaignStatus, { label: string; variant: BadgeProps["variant"] }> = {
  draft: { label: "Draft", variant: "muted" },
  running: { label: "Running", variant: "success" },
  paused: { label: "Paused", variant: "warning" },
  completed: { label: "Completed", variant: "secondary" },
};

export function CampaignStatusBadge({ status }: { status: CampaignStatus }) {
  const meta = CAMPAIGN_META[status];
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

export function Pagination({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <div className="flex items-center justify-between gap-3 border-t px-3 py-3 text-sm text-muted-foreground">
      <span className="tabular">
        {from}–{to} of {total.toLocaleString("en-US")}
      </span>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Previous
        </Button>
        <Button variant="outline" size="sm" disabled={to >= total} onClick={() => onChange(offset + limit)}>
          Next
        </Button>
      </div>
    </div>
  );
}

/** Native select styled like the inputs; used for simple filters. */
export function NativeSelect({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "h-9 rounded-md border border-input bg-background px-3 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
      {...props}
    >
      {children}
    </select>
  );
}
