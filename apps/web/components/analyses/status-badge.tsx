import { Check, CircleAlert, CircleX, Clock, LoaderCircle, ShieldQuestion } from "lucide-react";

import { cn } from "@/lib/cn";
import { getStageLabel, isFailureStatus, isTerminalStatus } from "@/lib/pipeline";
import type { AnalysisStatus } from "@/lib/types";

export function StatusBadge({
  status,
  className,
  showLabel = true,
}: {
  status: AnalysisStatus;
  className?: string;
  showLabel?: boolean;
}) {
  const failed = isFailureStatus(status);
  const complete = status === "COMPLETED";
  const confirmation = status === "NEEDS_USER_CONFIRMATION";
  const terminal = isTerminalStatus(status);

  const tone = failed
    ? "border-rose-500/25 bg-rose-500/10 text-rose-300"
    : confirmation
      ? "border-amber-500/25 bg-amber-500/10 text-amber-300"
      : complete
        ? "border-zinc-700 bg-zinc-800/70 text-zinc-300"
        : terminal
          ? "border-zinc-700 bg-zinc-800/70 text-zinc-400"
          : "border-sky-500/25 bg-sky-500/10 text-sky-300";

  const Icon = failed
    ? CircleX
    : confirmation
      ? ShieldQuestion
      : complete
        ? Check
        : terminal
          ? CircleAlert
          : status === "RECEIVED"
            ? Clock
            : LoaderCircle;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[10px] font-medium whitespace-nowrap",
        tone,
        className,
      )}
    >
      <Icon
        className={cn("size-3", !terminal && !failed && !confirmation && "animate-spin")}
        aria-hidden="true"
      />
      {showLabel ? getStageLabel(status) : null}
    </span>
  );
}
