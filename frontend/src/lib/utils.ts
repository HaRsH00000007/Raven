import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const intFmt = new Intl.NumberFormat("en-US");

export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return intFmt.format(n);
}

export function formatPercent(fraction: number | null | undefined, digits = 2): string {
  if (fraction === null || fraction === undefined || Number.isNaN(fraction)) return "—";
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function formatCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: cents % 100 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(cents / 100);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diff = Math.round((Date.now() - then) / 1000);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return formatDate(iso);
}

/** Render {{placeholders}} the same way the backend does. Unknown keys are left as-is. */
export function renderTemplate(template: string, vars: Record<string, string>): string {
  return template.replace(/\{\{\s*([a-z_]+)\s*\}\}/gi, (match, key: string) => {
    const v = vars[key.toLowerCase()];
    return v !== undefined && v !== "" ? v : match;
  });
}

/** Normalize something a user typed/pasted into a bare domain, or null if it doesn't look like one. */
export function normalizeDomain(raw: string): string | null {
  let s = raw.trim().toLowerCase();
  if (!s) return null;
  s = s.replace(/^[a-z]+:\/\//, "");
  s = s.split(/[/?#]/)[0];
  s = s.replace(/:\d+$/, "");
  s = s.replace(/^www\./, "");
  s = s.replace(/\.$/, "");
  if (!/^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(s)) return null;
  return s;
}

/** Minimal RFC-4180-ish CSV parser (quotes, escaped quotes, CRLF). */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ",") {
      row.push(field);
      field = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += c;
    }
  }
  if (field !== "" || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.some((cell) => cell.trim() !== ""));
}

export interface ParsedTargets {
  valid: { domain: string; company_name: string | null }[];
  invalid: string[];
  duplicates: number;
}

/** Mirror of the backend CSV rule: column domain/website/url, else first column; optional company_name/name. */
export function parseTargetsCsv(text: string): ParsedTargets {
  const rows = parseCsv(text);
  if (rows.length === 0) return { valid: [], invalid: [], duplicates: 0 };
  const header = rows[0].map((h) => h.trim().toLowerCase());
  const domainCol = ["domain", "website", "url"].map((k) => header.indexOf(k)).find((i) => i >= 0);
  const nameCol = ["company_name", "name"].map((k) => header.indexOf(k)).find((i) => i >= 0);
  const hasHeader = domainCol !== undefined || nameCol !== undefined || normalizeDomain(rows[0][0] ?? "") === null;
  const dataRows = hasHeader ? rows.slice(1) : rows;
  const dCol = domainCol ?? 0;
  return collectTargets(
    dataRows.map((r) => ({ raw: r[dCol] ?? "", name: nameCol !== undefined ? (r[nameCol] ?? "").trim() || null : null })),
  );
}

export function parseTargetsText(text: string): ParsedTargets {
  return collectTargets(
    text
      .split(/[\n,;\t ]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .map((raw) => ({ raw, name: null })),
  );
}

function collectTargets(entries: { raw: string; name: string | null }[]): ParsedTargets {
  const seen = new Set<string>();
  const valid: ParsedTargets["valid"] = [];
  const invalid: string[] = [];
  let duplicates = 0;
  for (const { raw, name } of entries) {
    if (!raw.trim()) continue;
    const d = normalizeDomain(raw);
    if (!d) {
      invalid.push(raw.trim());
      continue;
    }
    if (seen.has(d)) {
      duplicates++;
      continue;
    }
    seen.add(d);
    valid.push({ domain: d, company_name: name });
  }
  return { valid, invalid, duplicates };
}

export function errorText(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}
