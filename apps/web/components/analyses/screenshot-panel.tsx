"use client";

import Image from "next/image";
import { ScanSearch } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/cn";
import { confidenceLabel } from "@/lib/pipeline";
import type { EvidenceRegion } from "@/lib/types";

export function ScreenshotPanel({
  src,
  alt,
  regions,
  warnings,
}: {
  src: string;
  alt: string;
  regions: EvidenceRegion[];
  warnings: string[];
}) {
  const [selected, setSelected] = useState<string | null>(regions[0]?.field ?? null);

  return (
    <div>
      <div className="relative overflow-hidden rounded-lg border border-line bg-zinc-950">
        <Image
          src={src}
          alt={alt}
          width={1200}
          height={760}
          className="h-auto w-full"
          sizes="(max-width: 1280px) 100vw, 420px"
        />
        {regions.map((region) => {
          const active = selected === region.field;
          const low = region.confidence < 0.75;
          return (
            <button
              key={region.field}
              type="button"
              onClick={() => setSelected(region.field)}
              aria-label={`${region.label}, extraction confidence ${(region.confidence * 100).toFixed(0)}%`}
              aria-pressed={active}
              className={cn(
                "absolute rounded-[3px] border transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300",
                active
                  ? low
                    ? "border-amber-300 bg-amber-300/20 shadow-[0_0_0_1px_rgba(252,211,77,0.4)]"
                    : "border-sky-300 bg-sky-300/15 shadow-[0_0_0_1px_rgba(125,211,252,0.4)]"
                  : low
                    ? "border-amber-400/50 bg-amber-400/5 hover:bg-amber-400/15"
                    : "border-sky-400/40 bg-sky-400/5 hover:bg-sky-400/15",
              )}
              style={{
                left: `${region.x * 100}%`,
                top: `${region.y * 100}%`,
                width: `${region.width * 100}%`,
                height: `${region.height * 100}%`,
              }}
            >
              <span
                className={cn(
                  "mono absolute -top-4 left-0 rounded-sm px-1 text-[7px] uppercase tracking-wider",
                  low ? "bg-amber-300 text-amber-950" : "bg-sky-300 text-sky-950",
                )}
              >
                {(region.confidence * 100).toFixed(0)}%
              </span>
            </button>
          );
        })}
      </div>

      {regions.length > 0 ? (
        <ul className="mt-3 space-y-1">
          {regions.map((region) => {
            const active = selected === region.field;
            return (
              <li key={region.field}>
                <button
                  type="button"
                  onClick={() => setSelected(region.field)}
                  className={cn(
                    "flex w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left transition-colors",
                    active ? "bg-zinc-800/70" : "hover:bg-zinc-800/40",
                  )}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <ScanSearch className="size-3 shrink-0 text-zinc-600" aria-hidden="true" />
                    <span className="truncate text-[10px] text-zinc-400">{region.label}</span>
                  </span>
                  <span
                    className={cn(
                      "mono shrink-0 text-[9px]",
                      region.confidence < 0.75 ? "text-amber-400" : "text-zinc-500",
                    )}
                  >
                    {(region.confidence * 100).toFixed(0)}% · {confidenceLabel(region.confidence)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="mt-3 text-[10px] leading-relaxed text-zinc-600">
          No bounding-box regions were returned for this run. Text extraction is still recorded in the
          immutable analysis snapshot.
        </p>
      )}

      {warnings.length > 0 ? (
        <ul className="mt-3 space-y-1 border-t border-line/60 pt-2.5">
          {warnings.map((warning) => (
            <li key={warning} className="flex gap-2 text-[10px] leading-relaxed text-amber-300/80">
              <span className="mt-1.5 size-1 shrink-0 rounded-full bg-amber-400" aria-hidden="true" />
              {warning}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
