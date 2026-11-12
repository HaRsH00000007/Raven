# Raven API contract (v1)

Base URL: `{BACKEND_URL}/api/v1`. All JSON. Timestamps are ISO-8601 UTC strings. IDs are UUID strings.

## Auth

- Workspace endpoints: header `X-API-Key: <workspace api key>`.
- Admin endpoints: header `X-Admin-Token: <ADMIN_TOKEN>`.
- Inbound webhooks: query string `?token=<INBOUND_WEBHOOK_TOKEN>` (email) or Twilio signature (SMS/voice).

The Next.js app never exposes the key to the browser: client code calls `/api/proxy/<path>`, a Next route
handler that forwards to `{BACKEND_URL}/api/v1/<path>` adding `X-API-Key: RAVEN_API_KEY` (server env).

## Enums

`CampaignStatus`: `draft | running | paused | completed`

`SubmissionStatus`:
`PENDING | SUCCESS_VERIFIED | SUBMITTED_UNVERIFIED | DRY_RUN | SKIPPED_CAPTCHA | SKIPPED_NO_SOLICITATION | SKIPPED_ROBOTS | SKIPPED_COOLDOWN | SKIPPED_DNC | FAILED_NO_FORM | FAILED_VALIDATION | FAILED_ERROR`

"Sent" (billed) = `SUCCESS_VERIFIED`, plus `SUBMITTED_UNVERIFIED` when `BILL_UNVERIFIED=true`.

`ReplyChannel`: `email | sms | voicemail`

`Classification`: `positive | not_sure | negative | automatic`

## Workspace

`GET /workspace` →
```json
{ "id": "…", "name": "Glocal RPO", "credits_balance": 50000, "price_per_form_cents": 6,
  "forward_emails": ["sales@x.com"], "forward_not_sure": false, "forward_negative": false,
  "reply_phone_number": "+15551234567", "created_at": "…" }
```
`PATCH /workspace` body: any of `name, forward_emails, forward_not_sure, forward_negative, reply_phone_number` → same shape.

`GET /workspace/ledger?limit=50&offset=0` → `{ "items": [{ "id", "amount", "action", "submission_id", "note", "created_at" }], "total": n }`
(`action`: `grant | used | refund_skip | adjustment`)

Admin: `POST /admin/workspaces` `{ "name": "…", "credits": 5000 }` → `{ "id", "name", "api_key" }` (key shown once).
Admin: `POST /admin/workspaces/{id}/credits` `{ "amount": 50000, "note": "Nov plan" }` → workspace shape.

## Stats

`GET /stats/overview?campaign_id=<optional>` →
```json
{
  "forms_attempted": 4501, "forms_sent": 3900, "replies": 21, "positive": 2, "not_sure": 5,
  "negative": 9, "automatic": 5, "calls_booked": 1,
  "response_rate": 0.0054, "positive_rate": 0.0005,
  "credits_used": 3900, "spend_cents": 23400,
  "cost_per_positive_cents": 11700, "cost_per_reply_cents": 1114,
  "status_breakdown": { "SUCCESS_VERIFIED": 3500, "SKIPPED_CAPTCHA": 300, "…": 0 },
  "funnel": [ { "stage": "Targets", "count": 4501 }, { "stage": "Forms sent", "count": 3900 },
              { "stage": "Replies", "count": 21 }, { "stage": "Positive", "count": 2 },
              { "stage": "Calls booked", "count": 1 } ],
  "daily": [ { "date": "2026-11-01", "sent": 400, "replies": 3, "positive": 1 } ]
}
```
Rates are fractions (0–1). Cost fields are `null` when the denominator is 0. `daily` covers the last 30 days.

## Campaigns

Variant object:
```json
{ "id": "…", "label": "A", "sender_name": "Rohit Singh", "sender_title": "Partnerships",
  "subject": "Quick question", "message_body": "Are you hiring recruiters this quarter?\n\n– Rohit",
  "weight": 1 }
```
`message_body` and `subject` support `{{company_name}}`, `{{domain}}`, `{{sender_name}}`, `{{sender_company}}`.

`GET /campaigns` → `{ "items": [CampaignSummary] }`
```json
CampaignSummary = { "id", "name", "status", "sender_company", "sender_website", "sender_phone", "created_at",
  "targets": 500, "sent": 420, "replies": 3, "positive": 1, "pending": 30 }
```
`POST /campaigns` body:
```json
{ "name": "Staffing Nov", "sender_company": "Glocal RPO", "sender_website": "https://glocalrpo.com",
  "sender_phone": "+1…", "variants": [ {variant without id} ], "domains": ["acme.com", "…"] }
```
1–6 variants, labels unique within `A`–`F`. → `CampaignDetail` (201)

`GET /campaigns/{id}` → `CampaignDetail = CampaignSummary + { "variants": [Variant + { "sent", "replies", "positive", "positive_rate" }] }`

`PATCH /campaigns/{id}` body: any of `name, sender_company, sender_website, sender_phone` → CampaignDetail
`PUT /campaigns/{id}/variants` body `{ "variants": [...] }` → CampaignDetail (allowed in any status; existing submissions keep their variant)

`POST /campaigns/{id}/targets` body `{ "domains": ["a.com", "https://www.b.com/x"] }` → `{ "added": 2, "duplicates": 0, "invalid": ["not a domain"] }`
`POST /campaigns/{id}/targets/csv` multipart `file` (CSV; uses column `domain`/`website`/`url`, else first column; optional `company_name`/`name`) → same shape.

`POST /campaigns/{id}/launch` `{ "dry_run": false }` → `{ "queued": 480, "status": "running" }`
`POST /campaigns/{id}/pause` → CampaignDetail
`POST /campaigns/{id}/resume` → `{ "queued": n, "status": "running" }`

## Submissions

```json
Submission = { "id", "campaign_id", "campaign_name", "variant_id", "variant_label",
  "company_id", "domain", "company_name", "contact_url", "unique_reply_email", "status",
  "detail": "Confirmation text detected: 'Thank you'", "dry_run": false,
  "has_pre_screenshot": true, "has_post_screenshot": true,
  "fields_filled": { "email": "r-…@replies.x.com", "message": "…" },
  "created_at", "submitted_at", "reply_count": 1, "latest_classification": "positive" }
```
`GET /submissions?campaign_id=&status=&q=<domain search>&limit=50&offset=0` → `{ "items": [Submission], "total": n }`
`GET /submissions/{id}` → Submission
`GET /submissions/{id}/screenshots/pre` and `/post` → `image/png` (404 if missing)

## Replies (triage inbox)

```json
Reply = { "id", "submission_id", "workspace_id", "company_id", "domain", "company_name",
  "campaign_id", "campaign_name", "variant_label", "channel", "from_address", "from_name",
  "subject", "body": "stripped reply text", "raw_body", "classification", "confidence_score": 0.92,
  "explanation": "Asks to schedule a call", "classified_by": "llm|rules|manual",
  "is_forwarded": true, "call_booked": false, "created_at" }
```
`GET /replies?classification=positive&campaign_id=&limit=50&offset=0` → `{ "items": [Reply], "total": n, "counts": { "positive": 2, "not_sure": 5, "negative": 9, "automatic": 5 } }`
`GET /replies/{id}` → Reply
`PATCH /replies/{id}` body: any of `classification` (manual override; sets `classified_by=manual`), `call_booked` → Reply
`POST /replies/{id}/forward` → Reply (re-sends to `forward_emails`)

## Do-not-contact

`GET /do-not-contact` → `{ "items": [{ "id", "pattern", "reason", "created_at" }] }`
`POST /do-not-contact` `{ "pattern": "acme.com" | "*.gov" }` → item
`DELETE /do-not-contact/{id}` → 204

## Webhooks (no X-API-Key)

- `POST /webhooks/inbound/postmark?token=…` — Postmark inbound JSON.
- `POST /webhooks/inbound/sendgrid?token=…` — SendGrid Inbound Parse multipart.
- `POST /webhooks/twilio/sms` — Twilio form-encoded; returns TwiML.
- `POST /webhooks/twilio/voice` — returns TwiML that records a voicemail with transcription.
- `POST /webhooks/twilio/transcription` — Twilio transcription callback.

## Health

`GET /healthz` (outside `/api/v1`) → `{ "status": "ok" }`

---

# Phase 2 additions

## Workspace
`Workspace` gains: `"webhook_url": string|null, "webhook_secret": string|null, "default_sender_phone": string|null`.
`PATCH /workspace` also accepts `webhook_url` (https/http URL or null) and `default_sender_phone` (E.164 or null).
`default_sender_phone` is used as the phone on forms when a campaign has no `sender_phone`, and inbound SMS/calls to it map to this workspace.

`POST /workspace/webhook/test` → `{ "delivered": bool, "status_code": int|null, "logged_only": bool, "error": string|null, "payload": {...} }`
(sends a sample `reply.positive` event; when no `webhook_url` is set the payload is only logged: `logged_only=true`).
`POST /workspace/webhook/rotate-secret` → Workspace.

Outbound CRM webhook (on every reply classified `positive`): `POST webhook_url` with JSON
```json
{ "event": "reply.positive", "sent_at": "...", "workspace_id": "...",
  "reply": { "id", "channel", "from_address", "from_name", "subject", "body", "classification", "confidence_score", "explanation", "recording_url", "created_at" },
  "company": { "id", "domain", "company_name", "phone" } | null,
  "campaign": { "id", "name" } | null, "variant": { "id", "label" } | null,
  "submission": { "id", "contact_url", "submitted_at" } | null }
```
Headers: `X-Raven-Event: reply.positive`, `X-Raven-Timestamp: <unix>`, `X-Raven-Signature: sha256=<hex HMAC-SHA256(webhook_secret, "<timestamp>.<raw body>")>`.

## Ledger
`LedgerItem` gains `expires_at: string|null` and `remaining: int|null` (grants only). New `action`: `"expired"`.
Grants expire 60 days after they are issued (`CREDIT_VALIDITY_DAYS`); usage is taken from the oldest unexpired grant first.
Admin: `POST /admin/workspaces/{id}/credits` accepts `expires_in_days` (default 60, `null` = never).
Admin: `POST /admin/expire-credits` → `{ "expired_grants": n, "credits_expired": n }` (also runs hourly in the worker).

## Local simulator (non-production, or ALLOW_SIMULATOR=true)
`POST /simulate/inbound` body:
```json
{ "channel": "sms" | "voicemail" | "email", "from_number": "+15551234567", "from_email": "jane@acme.com",
  "body": "Yes, call me tomorrow", "company_domain": "acme.com", "submission_id": null }
```
`from_number` required for sms/voicemail, `from_email` for email; `company_domain`/`submission_id` optional hints.
Runs the same ingest + attribution + triage path as the real webhooks (synchronously) and returns the `Reply`.
For voicemail, `body` is used as the transcript (no recording).

## Company phone (attribution)
CSV uploads accept an optional `phone` column. SMS/voicemail replies are attributed by: company phone match →
earlier replies from the same number → a company domain/name mentioned in the message (recent submissions).
