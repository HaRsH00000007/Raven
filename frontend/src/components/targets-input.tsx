"use client";

import { useRef, useState } from "react";
import { FileUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { parseTargetsCsv, parseTargetsText, type ParsedTargets } from "@/lib/utils";

export interface TargetsValue {
  mode: "paste" | "csv";
  text: string;
  file: File | null;
  parsed: ParsedTargets;
}

export const EMPTY_TARGETS: TargetsValue = {
  mode: "paste",
  text: "",
  file: null,
  parsed: { valid: [], invalid: [], duplicates: 0 },
};

/** Paste domains or pick a CSV; both are parsed client-side for a preview before anything is sent. */
export function TargetsInput({ value, onChange }: { value: TargetsValue; onChange: (v: TargetsValue) => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [readError, setReadError] = useState<string | null>(null);

  async function onFile(file: File | undefined) {
    setReadError(null);
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) {
      setReadError("That file is larger than 20 MB.");
      return;
    }
    try {
      const text = await file.text();
      onChange({ mode: "csv", text: "", file, parsed: parseTargetsCsv(text) });
    } catch {
      setReadError("Could not read that file.");
    }
  }

  const { parsed } = value;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="domains">Paste domains or website URLs</Label>
        <Textarea
          id="domains"
          rows={8}
          className="font-mono text-xs"
          placeholder={"acmestaffing.com\nhttps://www.brightrecruiting.io/contact\n…"}
          value={value.mode === "paste" ? value.text : ""}
          onChange={(e) => onChange({ mode: "paste", text: e.target.value, file: null, parsed: parseTargetsText(e.target.value) })}
        />
      </div>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="text-muted-foreground">or</span>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => {
            void onFile(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        <Button type="button" variant="outline" size="sm" onClick={() => fileRef.current?.click()}>
          <FileUp /> Upload CSV
        </Button>
        {value.mode === "csv" && value.file && <span className="truncate text-muted-foreground">{value.file.name}</span>}
        <span className="text-xs text-muted-foreground">
          Uses a <code>domain</code>, <code>website</code> or <code>url</code> column (else the first), plus optional{" "}
          <code>company_name</code>.
        </span>
      </div>
      {readError && <div className="text-sm text-destructive">{readError}</div>}

      {(parsed.valid.length > 0 || parsed.invalid.length > 0) && (
        <div className="rounded-lg border">
          <div className="flex flex-wrap gap-x-4 gap-y-1 border-b bg-muted/40 px-3 py-2 text-sm">
            <span>
              <strong className="tabular">{parsed.valid.length.toLocaleString("en-US")}</strong> valid domains
            </span>
            {parsed.duplicates > 0 && <span className="text-muted-foreground">{parsed.duplicates} duplicates removed</span>}
            {parsed.invalid.length > 0 && <span className="text-destructive">{parsed.invalid.length} invalid</span>}
          </div>
          <div className="grid max-h-56 grid-cols-1 gap-x-6 overflow-y-auto px-3 py-2 font-mono text-xs sm:grid-cols-2">
            {parsed.valid.slice(0, 200).map((t) => (
              <div key={t.domain} className="truncate py-0.5">
                {t.domain}
                {t.company_name && <span className="text-muted-foreground"> · {t.company_name}</span>}
              </div>
            ))}
            {parsed.valid.length > 200 && (
              <div className="py-0.5 text-muted-foreground">…and {(parsed.valid.length - 200).toLocaleString("en-US")} more</div>
            )}
          </div>
          {parsed.invalid.length > 0 && (
            <div className="border-t px-3 py-2 text-xs text-destructive">
              Skipped: {parsed.invalid.slice(0, 10).join(", ")}
              {parsed.invalid.length > 10 ? ` and ${parsed.invalid.length - 10} more` : ""}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
