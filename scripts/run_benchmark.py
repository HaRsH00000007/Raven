#!/usr/bin/env python
"""Standalone benchmark of the form engine over a CSV of domains. No database, Redis or API needed.

    cd backend && .venv/Scripts/python ../scripts/run_benchmark.py            # dry run (default)
    python scripts/run_benchmark.py --csv scripts/seed_domains.csv --concurrency 6 --limit 20

Dry run (default) finds the contact form, fills it and takes the "pre" screenshot, but NEVER clicks
submit. Real submissions contact real companies, so they need both --submit and --yes-really-submit.

Writes benchmark_results.json (per-domain results + summary) and prints a summary table.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.models.enums import SubmissionStatus  # noqa: E402
from app.services.form_rules import normalize_domain  # noqa: E402
from app.workers.form_probe import FormEngine, ProbeOptions, ProbeResult, RobotsChecker, SenderProfile  # noqa: E402

DEFAULT_MESSAGE = "Are you hiring recruiters this quarter?\n\n- {sender_name}, {sender_company}"


def load_domains(path: Path) -> list[tuple[str, str | None]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    has_header = "domain" in header
    dcol = header.index("domain") if has_header else 0
    ncol = header.index("company_name") if "company_name" in header else None
    out, seen = [], set()
    for r in rows[1:] if has_header else rows:
        if len(r) <= dcol:
            continue
        d = normalize_domain(r[dcol])
        if d and d not in seen:
            seen.add(d)
            out.append((d, r[ncol].strip() if ncol is not None and len(r) > ncol else None))
    return out


async def run(args: argparse.Namespace) -> dict:
    targets = load_domains(Path(args.csv))
    if args.limit:
        targets = targets[: args.limit]
    shots = Path(args.screenshots)
    shots.mkdir(parents=True, exist_ok=True)
    dry_run = not (args.submit and args.yes_really_submit)
    sem = asyncio.Semaphore(args.concurrency)
    robots = RobotsChecker(args.ua_token)
    results: list[dict] = []
    started = time.monotonic()

    async with FormEngine(headless=not args.headed) as engine:

        async def one(i: int, domain: str, name: str | None) -> None:
            async with sem:
                run_id = f"bench-{i:03d}-{domain.replace('.', '_')}"
                sender = SenderProfile(
                    full_name=args.sender_name,
                    email=args.reply_email or f"r-{uuid.uuid4()}@{args.reply_domain}",
                    message=args.message.format(sender_name=args.sender_name, sender_company=args.sender_company, company_name=name or domain),
                    company=args.sender_company,
                    phone=args.phone,
                    title=args.sender_title,
                    website=args.website,
                )
                opts = ProbeOptions(run_id=run_id, screenshot_dir=shots, dry_run=dry_run, ua_token=args.ua_token)
                try:
                    res = await asyncio.wait_for(engine.probe(domain, sender, opts, robots), timeout=args.timeout)
                except asyncio.TimeoutError:
                    res = ProbeResult(status=SubmissionStatus.FAILED_ERROR, detail=f"Timed out after {args.timeout}s")
                except Exception as e:  # keep the benchmark going no matter what one site does
                    res = ProbeResult(status=SubmissionStatus.FAILED_ERROR, detail=f"{type(e).__name__}: {e}"[:300])
                row = {
                    "domain": domain,
                    "company_name": name,
                    "status": res.status.value,
                    "detail": res.detail,
                    "contact_url": res.contact_url,
                    "fields_filled": res.fields_filled,
                    "captcha_kinds": res.captcha_kinds,
                    "pre_screenshot": str(shots / res.pre_screenshot) if res.pre_screenshot else None,
                    "post_screenshot": str(shots / res.post_screenshot) if res.post_screenshot else None,
                    "duration_s": res.duration_s,
                }
                results.append(row)
                print(f"[{len(results):>3}/{len(targets)}] {domain:<28} {res.status.value:<24} {res.detail[:70]}", flush=True)

        await asyncio.gather(*(one(i, d, n) for i, (d, n) in enumerate(targets)))
    await robots.aclose()

    elapsed = time.monotonic() - started
    counts = Counter(r["status"] for r in results)
    n = len(results) or 1
    fillable = counts.get("DRY_RUN", 0) + counts.get("SUCCESS_VERIFIED", 0) + counts.get("SUBMITTED_UNVERIFIED", 0)
    summary = {
        "mode": "dry_run" if dry_run else "submit",
        "domains": len(results),
        "elapsed_s": round(elapsed, 1),
        "avg_seconds_per_site": round(sum(r["duration_s"] for r in results) / n, 2),
        "sites_per_minute": round(len(results) / elapsed * 60, 1) if elapsed else None,
        "status_counts": dict(counts.most_common()),
        "fillable_rate": round(fillable / n, 4),
        "verified_rate": round(counts.get("SUCCESS_VERIFIED", 0) / n, 4),
        "captcha_rate": round(counts.get("SKIPPED_CAPTCHA", 0) / n, 4),
        "no_form_rate": round(counts.get("FAILED_NO_FORM", 0) / n, 4),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "results": sorted(results, key=lambda r: r["domain"]),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", default=str(ROOT / "scripts" / "seed_domains.csv"))
    p.add_argument("--out", default=str(ROOT / "benchmark_results.json"))
    p.add_argument("--screenshots", default=str(ROOT / "benchmark_screenshots"))
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--timeout", type=int, default=120, help="seconds per site")
    p.add_argument("--headed", action="store_true", help="show the browser window")
    p.add_argument("--sender-name", default="Alex Example")
    p.add_argument("--sender-company", default="Example Co")
    p.add_argument("--sender-title", default="Partnerships")
    p.add_argument("--phone", default=None)
    p.add_argument("--website", default="https://example.com")
    p.add_argument("--message", default=DEFAULT_MESSAGE, help="supports {sender_name} {sender_company} {company_name}")
    p.add_argument("--reply-domain", default="replies.example.com")
    p.add_argument("--reply-email", default=None, help="fixed reply address (default: unique r-<uuid>@reply-domain)")
    p.add_argument("--ua-token", default="RavenFormBot/1.0")
    p.add_argument("--submit", action="store_true", help="actually click submit (contacts real companies)")
    p.add_argument("--yes-really-submit", action="store_true", help="required together with --submit")
    args = p.parse_args()
    if args.submit and not args.yes_really_submit:
        p.error("--submit sends real messages to real companies; add --yes-really-submit to confirm")

    report = asyncio.run(run(args))
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    s = report["summary"]
    print("\n=== Benchmark summary ===")
    for k, v in s.items():
        print(f"{k:>22}: {v}")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
