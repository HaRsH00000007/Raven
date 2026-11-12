// Types mirroring docs/API.md (v1).

export type CampaignStatus = "draft" | "running" | "paused" | "completed";

export const SUBMISSION_STATUSES = [
  "PENDING",
  "SUCCESS_VERIFIED",
  "SUBMITTED_UNVERIFIED",
  "DRY_RUN",
  "SKIPPED_CAPTCHA",
  "SKIPPED_NO_SOLICITATION",
  "SKIPPED_ROBOTS",
  "SKIPPED_COOLDOWN",
  "SKIPPED_DNC",
  "FAILED_NO_FORM",
  "FAILED_VALIDATION",
  "FAILED_ERROR",
] as const;
export type SubmissionStatus = (typeof SUBMISSION_STATUSES)[number];

export type ReplyChannel = "email" | "sms" | "voicemail";

export const CLASSIFICATIONS = ["positive", "not_sure", "negative", "automatic"] as const;
export type Classification = (typeof CLASSIFICATIONS)[number];

export const VARIANT_LABELS = ["A", "B", "C", "D", "E", "F"] as const;
export type VariantLabel = (typeof VARIANT_LABELS)[number];

export interface Paginated<T> {
  items: T[];
  total: number;
}

export interface Workspace {
  id: string;
  name: string;
  credits_balance: number;
  price_per_form_cents: number;
  forward_emails: string[];
  forward_not_sure: boolean;
  forward_negative: boolean;
  reply_phone_number: string | null;
  webhook_url: string | null;
  webhook_secret: string | null;
  default_sender_phone: string | null;
  created_at: string;
}

export type WorkspaceUpdate = Partial<
  Pick<
    Workspace,
    | "name"
    | "forward_emails"
    | "forward_not_sure"
    | "forward_negative"
    | "reply_phone_number"
    | "webhook_url"
    | "default_sender_phone"
  >
>;

export interface WebhookTestResult {
  delivered: boolean;
  status_code: number | null;
  logged_only: boolean;
  error: string | null;
  payload: Record<string, unknown>;
}

export type SimulatedChannel = "sms" | "voicemail" | "email";

export interface SimulateInbound {
  channel: SimulatedChannel;
  from_number?: string | null;
  from_email?: string | null;
  body: string;
  company_domain?: string | null;
  submission_id?: string | null;
}

export type LedgerAction = "grant" | "used" | "refund_skip" | "adjustment" | "expired";

export interface LedgerEntry {
  id: string;
  amount: number;
  action: LedgerAction;
  submission_id: string | null;
  note: string | null;
  expires_at: string | null;
  remaining: number | null;
  created_at: string;
}

export interface FunnelStage {
  stage: string;
  count: number;
}

export interface DailyPoint {
  date: string;
  sent: number;
  replies: number;
  positive: number;
}

export interface OverviewStats {
  forms_attempted: number;
  forms_sent: number;
  replies: number;
  positive: number;
  not_sure: number;
  negative: number;
  automatic: number;
  calls_booked: number;
  response_rate: number | null;
  positive_rate: number | null;
  credits_used: number;
  spend_cents: number;
  cost_per_positive_cents: number | null;
  cost_per_reply_cents: number | null;
  status_breakdown: Partial<Record<SubmissionStatus, number>>;
  funnel: FunnelStage[];
  daily: DailyPoint[];
}

export interface VariantInput {
  label: VariantLabel;
  sender_name: string;
  sender_title: string | null;
  subject: string | null;
  message_body: string;
  weight: number;
}

export interface Variant extends VariantInput {
  id: string;
}

export interface VariantWithStats extends Variant {
  sent: number;
  replies: number;
  positive: number;
  positive_rate: number | null;
}

export interface CampaignSummary {
  id: string;
  name: string;
  status: CampaignStatus;
  sender_company: string | null;
  sender_website: string | null;
  sender_phone: string | null;
  created_at: string;
  targets: number;
  sent: number;
  replies: number;
  positive: number;
  pending: number;
}

export interface CampaignDetail extends CampaignSummary {
  variants: VariantWithStats[];
}

export interface CampaignCreate {
  name: string;
  sender_company: string | null;
  sender_website: string | null;
  sender_phone: string | null;
  variants: VariantInput[];
  domains: string[];
}

export type CampaignUpdate = Partial<Pick<CampaignSummary, "name" | "sender_company" | "sender_website" | "sender_phone">>;

export interface TargetsResult {
  added: number;
  duplicates: number;
  invalid: string[];
}

export interface LaunchResult {
  queued: number;
  status: CampaignStatus;
}

export interface Submission {
  id: string;
  campaign_id: string;
  campaign_name: string;
  variant_id: string;
  variant_label: VariantLabel;
  company_id: string;
  domain: string;
  company_name: string | null;
  contact_url: string | null;
  unique_reply_email: string;
  status: SubmissionStatus;
  detail: string | null;
  dry_run: boolean;
  has_pre_screenshot: boolean;
  has_post_screenshot: boolean;
  fields_filled: Record<string, string> | null;
  created_at: string;
  submitted_at: string | null;
  reply_count: number;
  latest_classification: Classification | null;
}

export interface Reply {
  id: string;
  submission_id: string | null;
  workspace_id: string;
  company_id: string | null;
  domain: string | null;
  company_name: string | null;
  campaign_id: string | null;
  campaign_name: string | null;
  variant_label: VariantLabel | null;
  channel: ReplyChannel;
  from_address: string | null;
  from_name: string | null;
  subject: string | null;
  body: string;
  raw_body: string;
  classification: Classification | null;
  recording_url: string | null;
  confidence_score: number | null;
  explanation: string | null;
  classified_by: "llm" | "rules" | "manual" | null;
  is_forwarded: boolean;
  call_booked: boolean;
  created_at: string;
}

export interface ReplyList extends Paginated<Reply> {
  counts: Record<Classification, number>;
}

export type ReplyUpdate = Partial<Pick<Reply, "classification" | "call_booked">>;

export interface DncEntry {
  id: string;
  pattern: string;
  reason: string | null;
  created_at: string;
}
