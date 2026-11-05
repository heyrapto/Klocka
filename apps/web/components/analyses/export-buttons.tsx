"use client";

import { Check, Download, LoaderCircle } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { Forecast } from "@/lib/types";

export function DownloadSnapshotButton({
  forecast,
  label = "Export JSON",
}: {
  forecast: Forecast;
  label?: string;
}) {
  const [state, setState] = useState<"idle" | "done">("idle");

  function handleDownload() {
    const payload = {
      schema_version: "forecast_snapshot.v1",
      exported_at: new Date().toISOString(),
      forecast,
      disclaimer:
        "Analysis and decision support only. This snapshot is not an executable order or a guarantee of outcome.",
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `forecast-${forecast.id}.json`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    setState("done");
    window.setTimeout(() => setState("idle"), 1800);
  }

  return (
    <Button variant="outline" size="sm" onClick={handleDownload}>
      {state === "done" ? (
        <Check className="size-3.5 text-emerald-400" aria-hidden="true" />
      ) : (
        <Download className="size-3.5" aria-hidden="true" />
      )}
      {state === "done" ? "Exported" : label}
    </Button>
  );
}

export function CopyValueButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="mono inline-flex items-center gap-1 text-[10px] text-zinc-500 transition-colors hover:text-sky-300"
      aria-label={`Copy ${label}`}
    >
      {copied ? <Check className="size-3 text-emerald-400" aria-hidden="true" /> : null}
      {copied ? "copied" : "copy"}
    </button>
  );
}

export function SpinnerLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2">
      <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
      {children}
    </span>
  );
}
