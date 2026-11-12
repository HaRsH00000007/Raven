# Raven

Raven is an outbound lead-generation platform. It sends a short message through each target company's
own website contact form, sorts the replies with AI, and forwards the positive ones to your sales inbox.

```
Next.js dashboard ──/api/proxy──▶ FastAPI (Postgres) ──▶ Redis/ARQ ──▶ worker: Playwright form engine
                                       ▲                                   └─▶ screenshots (pre/post)
   Postmark / SendGrid / Twilio ──webhooks──┘──▶ triage (Claude or rules) ──▶ forward / do-not-contact
```

## Guardrails (built in, not configurable)

- **Honest browser.** Vanilla headless Chromium with an identifying UA token (`RavenFormBot/1.0 (+BOT_INFO_URL)`).
  No stealth plugins and no fingerprint spoofing.
- **CAPTCHAs are never solved.** reCAPTCHA, hCaptcha, Turnstile, Friendly Captcha and Cloudflare challenges lead to
  `SKIPPED_CAPTCHA`, and no credit is charged.
- **robots.txt is respected** for every URL the engine loads (RFC 9309: a 4xx means allow, a 5xx or network error
  means disallow). A blocked site is recorded as `SKIPPED_ROBOTS`.
- **"No solicitation" pages are skipped.** The site is recorded as `SKIPPED_NO_SOLICITATION` and blacklisted for the
  workspace.
- **Honeypot fields are never filled**, and **newsletter opt-ins are never ticked**. Consent checkboxes are ticked
  only when the form requires them.
- **90-day cooldown** per company, and a per-workspace **do-not-contact list**. A negative reply adds the company to
  that list automatically.

## How a submission works

1. The worker checks the do-not-contact list and the cooldown before opening a browser.
2. The engine loads the homepage and ranks the contact links. Before reading each candidate page it:
   - dismisses the cookie banner, choosing "Reject all" when offered
   - scrolls the page so lazy-loaded forms appear
   - reads forms in iframes and inside open shadow DOM
   - clicks "Send us a message"-style buttons that open a hidden form
3. It picks the form that has a message field and maps the fields with rules. When the rules can't place the email
   or message field, it asks Claude to map them (optional).
4. It fills the form. The email field gets **`r-{submission_id}@REPLY_DOMAIN`**, so every reply maps back to exactly
   one workspace, campaign, variant and company.
5. HTML5 validation runs. If a required field can't be filled, the result is `FAILED_VALIDATION`: nothing is
   submitted and nothing is charged.
6. It takes the **pre-submit screenshot** of the filled form.
7. It reserves one credit, clicks submit and waits. It then takes the **post-submit screenshot** and judges the
   outcome:
   - confirmation text or a redirect to a confirmation URL → `SUCCESS_VERIFIED`
   - visible validation errors → `FAILED_VALIDATION`
   - otherwise → `SUBMITTED_UNVERIFIED`
8. Billing: credits are kept only for `SUCCESS_VERIFIED`, plus `SUBMITTED_UNVERIFIED` when `BILL_UNVERIFIED=true`.
   Every other outcome is refunded to the ledger.

## Requirements

- **Docker Desktop**, for the full stack or just for Postgres and Redis.
- To run the code outside Docker: **Python 3.12** and **Node.js 20+**.
- No paid API keys are needed. Claude, Twilio, Postmark/SendGrid, Deepgram/OpenAI and SMTP are all optional.

## Quick start (Docker, one command)

```bash
git clone https://github.com/HaRsH00000007/Raven.git
cd Raven
cp .env.example .env          # Windows PowerShell: copy .env.example .env
docker compose up -d --build
```

| What | URL |
|---|---|
| Dashboard | http://localhost:3000 (change with `FRONTEND_PORT` in `.env`) |
| API docs (Swagger) | http://localhost:8000/docs (change with `BACKEND_PORT`) |

This starts five services:
- `postgres`
- `redis`
- `backend` (FastAPI)
- `worker` (Playwright form engine and reply sorting)
- `frontend` (Next.js)

The first build takes a few minutes.

On startup the backend runs the database migrations and creates a demo workspace for `RAVEN_API_KEY` with
`DEV_BOOTSTRAP_CREDITS` credits (5,000 by default). There is no login page. The dashboard calls the API with that
key from the server only, so the key never reaches the browser.

```bash
docker compose ps                        # all five services should be "running" or "healthy"
docker compose logs -f backend worker    # follow what the engine is doing
docker compose down                      # stop (data is kept in Docker volumes)
docker compose down -v                   # stop and wipe the database
```

> If port 3000 is already taken, set `FRONTEND_PORT=3001` in `.env` and run `docker compose up -d` again.

## Running the code without Docker (for development)

Use this when you are editing code: the backend and frontend reload on every save. Postgres and Redis still run
in Docker. **Stop the full stack first** with `docker compose down`, because both setups use the same ports.

### One-time setup

```bash
# 1. Database and queue (after a reboot, restart them with: docker start raven-pg raven-redis)
docker run -d --name raven-pg -e POSTGRES_USER=raven -e POSTGRES_PASSWORD=raven -e POSTGRES_DB=raven -p 55491:5432 postgres:16
docker run -d --name raven-redis -p 56379:6379 redis:7

# 2. Backend
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt          # macOS/Linux: .venv/bin/pip
.venv/Scripts/python -m playwright install chromium
cp .env.example .env
#   then edit backend/.env:
#   DATABASE_URL=postgresql+asyncpg://raven:raven@localhost:55491/raven
#   REDIS_URL=redis://localhost:56379/0
.venv/Scripts/alembic upgrade head                      # create the tables

# 3. Frontend
cd ../frontend
npm install
cp .env.example .env.local
#   then set RAVEN_API_KEY=rvn_dev_local_key_change_me (the same value as DEV_BOOTSTRAP_API_KEY in backend/.env)
```

### Every time: three terminals

| Terminal | Runs | Command |
|---|---|---|
| 1 | Backend API (port 8000) | `cd backend`, then `.venv/Scripts/uvicorn app.main:app --reload --port 8000` |
| 2 | Worker (fills forms, sorts replies) | `cd backend`, then `.venv/Scripts/arq app.workers.arq_worker.WorkerSettings` |
| 3 | Frontend (port 3001) | `cd frontend`, then `npm run dev -- -p 3001` |

Then open http://localhost:3001.
- Without the worker, campaigns stay "pending".
- Restart the worker after changing worker code.
- Run `alembic upgrade head` again whenever a new migration is added.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Connect call failed ('127.0.0.1', 55491)` in the backend | Postgres isn't running. Start Docker Desktop, then run `docker start raven-pg`. |
| `redis ... Timeout connecting to server` in the worker | Redis isn't running. Run `docker start raven-redis`. |
| `GET /api/proxy/... 500` in the frontend | The backend is down or can't reach Postgres. Check terminal 1. |
| `dockerDesktopLinuxEngine` errors | Docker Desktop isn't open. |
| Port already in use | The Docker stack and the terminal setup are both running. Stop one of them. |

## Testing the platform by hand

1. **Create a campaign.** Click *New campaign*.
   - Add a name, your company and a short message for variant A. Add a different message as variant B to A/B test.
   - Paste a few domains or upload `scripts/seed_domains.csv`.
   - Launch with **Dry run ON** (the default).
2. **Watch it run.** Within a minute or two each target gets an outcome, such as:
   - *Dry run*: form found and filled
   - *Skipped CAPTCHA*
   - *Skipped robots*
   - *No form*

   Dry runs never submit and never spend credits. Small-business sites work better than large enterprise ones,
   which often use CAPTCHAs.
3. **Check the proofs.** *Proofs* shows a screenshot of every filled form. Check that the name, the
   `r-…@REPLY_DOMAIN` reply address and the message landed in the right fields.
4. **Simulate replies.** Open *Settings → Simulate an inbound reply* and send:
   - an email: "Yes, interested, let's talk"
   - an SMS: "Not interested, remove us"
   - a voicemail that names one of your target companies

   Open *Inbox*. Each reply should be matched to its company, campaign and variant, and labelled positive, negative
   or not sure. A negative reply adds the company to the do-not-contact list.
5. **CRM webhook.**
   1. Paste a URL from https://webhook.site into *Settings → CRM webhook*.
   2. Click *Send test event*.
   3. Simulate another positive reply.

   Both events should arrive there, signed.
6. **Credits and stats.**
   - Credits don't drop on dry runs.
   - *Settings* shows the credit ledger with expiry dates.
   - *Overview* shows the funnel and per-variant results for the selected campaign.
7. **A real submission (optional).** Only use a form you own or have permission to contact. Create a
   one-domain campaign and turn dry run **off**. One credit is reserved, and Proofs gets screenshots from before
   and after submitting.

## Project structure

```
backend/      FastAPI app (app/api), models and Alembic migrations, services (credits, inbound, triage,
              webhooks), workers (Playwright form engine, ARQ jobs), tests with local fixture sites
frontend/     Next.js 14 dashboard (App Router, Tailwind); /api/proxy adds the API key on the server
mcp_server/   MCP server so Claude Desktop and Claude Code can manage campaigns
scripts/      Benchmark runner and a 50-domain seed CSV
docs/API.md   Full REST API contract
```

## Production checklist

- **Secrets:** set strong values for `ADMIN_TOKEN`, `INBOUND_WEBHOOK_TOKEN` and `RAVEN_API_KEY`, set
  `ENVIRONMENT=production`, and unset `DEV_BOOTSTRAP_API_KEY`. Create workspaces with
  `POST /api/v1/admin/workspaces`.
- **Replies by email:**
  1. Point the MX record for `REPLY_DOMAIN` at Postmark Inbound or SendGrid Inbound Parse, as a catch-all.
  2. Set the webhook to `{PUBLIC_BASE_URL}/api/v1/webhooks/inbound/postmark?token=…` (or `/sendgrid`).
- **Replies by phone:** buy a Twilio number and set it as the workspace phone number in Settings and as the campaign
  sender phone. Then:
  - set the SMS webhook to `/api/v1/webhooks/twilio/sms`
  - set the voice webhook to `/api/v1/webhooks/twilio/voice`
  - set `TWILIO_AUTH_TOKEN`; signatures are verified against `PUBLIC_BASE_URL`
- **Forwarding:** set `SMTP_*` and add forwarding inboxes in Settings.
- **Throughput:** each worker drives `WORKER_CONCURRENCY` browser contexts. Scale with
  `docker compose up --scale worker=N`. Chromium needs about 300–500 MB per concurrent context.

## Automated tests

```bash
cd backend
.venv/Scripts/python -m pytest                       # SQLite + local fixture sites, no internet needed
TEST_DATABASE_URL=postgresql+asyncpg://… .venv/Scripts/python -m pytest tests/test_api_flow.py   # against Postgres
```

The form-engine tests serve local fixture websites and run the real Playwright engine against them. The fixtures
cover:
- a successful submission
- a dry run
- running out of credits
- skips for CAPTCHA, no-solicitation and robots.txt
- a page with no form
- a required field the engine can't fill
- a form inside an iframe
- a form inside shadow DOM
- a cookie banner in front of a "Send us a message" modal

## Benchmark

```bash
cd backend && .venv/Scripts/python ../scripts/run_benchmark.py --concurrency 3 --phone "+1…"
```

The benchmark runs over `scripts/seed_domains.csv` and writes `benchmark_results.json`, with screenshots in
`benchmark_screenshots/`.

It is a **dry run by default**: it never clicks submit. Real submissions need both `--submit` and
`--yes-really-submit`. The default sender details are neutral placeholders; pass `--sender-name`,
`--sender-company` and `--message` for your own.

## Phase 2 features

### Multi-step forms
The engine handles forms that span several steps (pages labelled "Next", "Continue", "Step 2" and so on), up to
4 steps. On each step it:
1. fills the visible fields
2. checks those fields are valid
3. saves a screenshot of the step (`{id}_pre_step{n}.png`)
4. clicks Next and waits for the next step to render

One credit is reserved, only right before the final submit.

A **dry run only advances through steps handled in the browser**. If a step's Next button posts to the site's
server, the dry run stops on that step, because advancing would send data.

### SMS and voicemail replies
- **Workspace matching:** an inbound text or call is matched to a workspace by the number it was sent to. That can be
  the workspace's Raven number, its default sender phone, or any campaign's sender phone.
- **Company matching, in order:**
  1. the company's phone number, from a `phone` column in the target CSV
  2. an earlier reply from the same number
  3. a company mentioned in the message ("Jane from Acme Staffing"; matching tolerates spaces in a domain name)
  
  The reply is then linked to that company's latest submission, which gives the campaign and variant. The engine also
  remembers the number for next time.
- **Voicemail flow:** Twilio Voice → `/webhooks/twilio/voice` records the call → `/webhooks/twilio/recording` → the
  worker transcribes it:
  - Deepgram if `DEEPGRAM_API_KEY` is set
  - otherwise OpenAI Whisper if `OPENAI_API_KEY` is set
  - otherwise a `TranscriptionText` field or a placeholder
  
  The reply is then sorted like an email.

### Credit expiry
- Each grant expires `CREDIT_VALIDITY_DAYS` (default 60: one billing month plus one rollover month) after it is issued.
- Usage draws from the soonest-expiring grant first, and refunds return credits to the grant they came from.
- An hourly worker job, also available as `POST /api/v1/admin/expire-credits` with `X-Admin-Token`, removes each
  expired grant's unused credits and writes an `expired` ledger row.
- Admin grants accept `expires_in_days` (`null` means never).
- Grants created before this feature never expire.

### CRM webhook
Every positive reply is POSTed once to the workspace's `webhook_url` (set in Settings).
- **Headers:** `X-Raven-Timestamp`, and `X-Raven-Signature: sha256=HMAC-SHA256(webhook_secret, "<ts>.<raw body>")`.
- **Without a URL**, the payload is written to the server log.
- **Testing:** Settings has "Send test event". The payload format is in `docs/API.md`.

### Local simulator (no paid APIs needed)
Outside production, Settings → "Simulate an inbound reply" (or `POST /api/v1/simulate/inbound`) creates an email, SMS
or voicemail reply. It runs through the same matching, sorting, forwarding and CRM-webhook path as real traffic.

Real Twilio webhooks can also be posted with curl locally. Signatures are only enforced when `TWILIO_AUTH_TOKEN` is set.

```bash
curl -X POST localhost:8000/api/v1/webhooks/twilio/sms -d "From=%2B15553330000&To=%2B15550002222&Body=Yes+call+me&MessageSid=SM1"
```

### MCP server
`mcp_server/server.py` lets AI tools work with a Raven workspace. Tools:
- `list_campaigns`
- `create_campaign`
- `upload_targets`
- `get_campaign_stats`
- `get_positive_leads`

It deliberately has **no launch tool**: sending real messages stays a human decision.

```bash
cd mcp_server && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m pytest
```

Claude Desktop / Claude Code config:
```json
{ "mcpServers": { "raven": {
    "command": "<path-to-repo>/mcp_server/.venv/Scripts/python.exe",
    "args": ["<path-to-repo>/mcp_server/server.py"],
    "env": { "RAVEN_API_URL": "http://localhost:8000", "RAVEN_API_KEY": "rvn_dev_local_key_change_me" } } } }
```
Use `--transport streamable-http` to serve it over HTTP on `MCP_PORT` (default 8765).

## Known limitations

- **Multi-step dry runs stop early on some forms.** When a form's Next button posts the step to the server, a dry run
  only fills step 1, by design.
- **Some phone replies stay unmatched.** An SMS or voicemail from an unknown number that names no recently contacted
  company is stored for the workspace without a company.
- **Rules-based sorting is coarse.** Without `ANTHROPIC_API_KEY`, replies are sorted by rules, which catch clear
  yes/no answers. Anything subtler ("Tuesday at 10 works") lands in "Not sure".
- **Old grants never expire.** Credits granted before expiry tracking was added have no expiry date.
- **A worker crash can leave a submission stuck.** If a worker dies after reserving a credit but before saving the
  result, the submission stays `PENDING` with the credit used. Jobs are not retried automatically, to avoid messaging
  a company twice.
