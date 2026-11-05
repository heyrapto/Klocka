"use client";

import { Check, CircleX, LoaderCircle, Minus, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/cn";
import { formatTime } from "@/lib/format";
import { getStageLabel } from "@/lib/pipeline";
import type { AnalysisStage } from "@/lib/types";

export function StageTimeline({ stages }: { stages: AnalysisStage[] }) {
  const firstActive =
    stages.findIndex((stage) => stage.status === "in_progress" || stage.status === "failed") >= 0
      ? stages.findIndex((stage) => stage.status === "in_progress" || stage.status === "failed")
      : Math.max(0, stages.length - 1);
  const [selected, setSelected] = useState(firstActive);
  const current = stages[selected];

  return (
    <div>
      <div className="overflow-x-auto pb-1">
        <ol className="flex min-w-[980px] items-start">
          {stages.map((stage, index) => {
            const isSelected = index === selected;
            const failed = stage.status === "failed";
            const inProgress = stage.status === "in_progress";
            const completed = stage.status === "completed";
            const Icon = failed ? CircleX : inProgress ? LoaderCircle : completed ? Check : Minus;
            return (
              <li key={`${stage.stage}-${index}`} className="flex flex-1 items-start">
                <button
                  type="button"
                  onClick={() => setSelected(index)}
                  className="group flex min-w-0 flex-1 flex-col items-center gap-2 rounded-md px-1 py-1 text-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/70"
                  aria-pressed={isSelected}
                  aria-label={`${getStageLabel(stage.stage)}: ${stage.status}`}
                >
                  <span className="flex w-full items-center">
                    <span
                      className={cn(
                        "h-px flex-1",
                        index === 0
                          ? "bg-transparent"
                          : completed || failed
                            ? failed
                              ? "bg-rose-500/40"
                              : "bg-sky-500/40"
                            : "bg-line",
                      )}
                      aria-hidden="true"
                    />
                    <span
                      className={cn(
                        "z-10 flex size-7 shrink-0 items-center justify-center rounded-full border transition-colors",
                        failed
                          ? "border-rose-500/50 bg-rose-500/15 text-rose-300"
                          : completed
                            ? "border-sky-500/40 bg-sky-500/10 text-sky-300"
                            : inProgress
                              ? "border-amber-500/50 bg-amber-500/10 text-amber-300"
                              : "border-line bg-zinc-900 text-zinc-600",
                        isSelected && "ring-2 ring-sky-400/40 ring-offset-2 ring-offset-surface",
                      )}
                    >
                      <Icon
                        className={cn("size-3.5", inProgress && "animate-spin")}
                        aria-hidden="true"
                      />
                    </span>
                    <span
                      className={cn(
                        "h-px flex-1",
                        index === stages.length - 1
                          ? "bg-transparent"
                          : completed
                            ? "bg-sky-500/40"
                            : "bg-line",
                      )}
                      aria-hidden="true"
                    />
                  </span>
                  <span
                    className={cn(
                      "block max-w-[92px] text-[9px] font-medium leading-tight",
                      isSelected ? "text-zinc-100" : failed ? "text-rose-300" : "text-zinc-500",
                    )}
                  >
                    {getStageLabel(stage.stage)}
                  </span>
                  <span className="mono block text-[8px] text-zinc-700">
                    {stage.completedAt ? formatTime(stage.completedAt).replace(" UTC", "") : stage.startedAt ? "running" : "—"}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      </div>

      {current ? (
        <div className="mt-3 flex items-start gap-2.5 rounded-lg border border-line/70 bg-zinc-950/50 px-3.5 py-3">
          {current.status === "failed" ? (
            <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-rose-400" aria-hidden="true" />
          ) : (
            <Check className="mt-0.5 size-3.5 shrink-0 text-sky-400" aria-hidden="true" />
          )}
          <div className="min-w-0">
            <p className="text-[11px] text-zinc-300">
              <span className="font-medium text-zinc-200">{getStageLabel(current.stage)}</span>
              <span className="text-zinc-600"> · {current.status.replaceAll("_", " ")}</span>
            </p>
            <p className="mt-0.5 text-[11px] leading-relaxed text-zinc-500">{current.message}</p>
          </div>
        </div>
      ) : null}
    </div>
  );
}
