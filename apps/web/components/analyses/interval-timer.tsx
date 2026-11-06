"use client";

import { Clock, Hourglass, Zap } from "lucide-react";
import { useEffect, useState } from "react";

interface IntervalTimerProps {
  resolvesAt?: string | null;
  pipelineLatencySeconds?: number | null;
  initialSecondsRemaining?: number | null;
  decisionSecondsRemaining?: number | null;
}

function formatDuration(totalSeconds: number): string {
  if (totalSeconds <= 0) return "00:00";
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = Math.floor(totalSeconds % 60);

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export function IntervalTimer({
  resolvesAt,
  pipelineLatencySeconds,
  initialSecondsRemaining,
  decisionSecondsRemaining,
}: IntervalTimerProps) {
  const [secondsRemaining, setSecondsRemaining] = useState<number | null>(() => {
    if (!resolvesAt) return decisionSecondsRemaining ?? null;
    const diff = Math.floor((new Date(resolvesAt).getTime() - Date.now()) / 1000);
    return Math.max(0, diff);
  });

  useEffect(() => {
    if (!resolvesAt) return;

    function update() {
      const diff = Math.floor((new Date(resolvesAt!).getTime() - Date.now()) / 1000);
      setSecondsRemaining(Math.max(0, diff));
    }

    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [resolvesAt]);

  if (secondsRemaining === null) return null;

  const isExpired = secondsRemaining <= 0;
  const isPinRisk = !isExpired && secondsRemaining <= 60;
  const isLate = !isExpired && !isPinRisk && secondsRemaining <= 180;

  // Visual status
  let statusBadge = {
    label: "Active Trading Window",
    badgeClass: "border-emerald-200 bg-emerald-50 text-emerald-700",
    dotClass: "bg-emerald-500",
    clockColor: "text-emerald-600",
  };

  if (isExpired) {
    statusBadge = {
      label: "Candle Closed • Awaiting Settlement",
      badgeClass: "border-violet-200 bg-violet-50 text-violet-700",
      dotClass: "bg-violet-500",
      clockColor: "text-violet-600",
    };
  } else if (isPinRisk) {
    statusBadge = {
      label: "Final Minute • High Gamma Risk",
      badgeClass: "border-rose-200 bg-rose-50 text-rose-700 animate-pulse",
      dotClass: "bg-rose-500",
      clockColor: "text-rose-600",
    };
  } else if (isLate) {
    statusBadge = {
      label: "Late Interval • Theta Accelerating",
      badgeClass: "border-amber-200 bg-amber-50 text-amber-700",
      dotClass: "bg-amber-500",
      clockColor: "text-amber-600",
    };
  }

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-line bg-surface/80 p-3.5 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-center gap-3">
        <div className="flex size-9 items-center justify-center rounded-lg border border-line bg-raised">
          <Hourglass className={`size-4 ${statusBadge.clockColor} ${!isExpired ? "animate-pulse" : ""}`} />
        </div>
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-lg font-bold tracking-tight text-zinc-900">
              {formatDuration(secondsRemaining)}
            </span>
            <span className="text-xs font-mono text-zinc-500">remaining</span>
            <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${statusBadge.badgeClass}`}>
              <span className={`size-1.5 rounded-full ${statusBadge.dotClass}`} />
              {statusBadge.label}
            </span>
          </div>
          {resolvesAt && (
            <p className="mt-0.5 text-[11px] font-mono text-zinc-500">
              Contract resolves at {new Date(resolvesAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })} UTC
            </p>
          )}
        </div>
      </div>

      {pipelineLatencySeconds != null && (
        <div className="flex items-center gap-1.5 rounded-lg border border-line/70 bg-raised/50 px-2.5 py-1 text-[11px] font-mono text-zinc-600">
          <Zap className="size-3 text-amber-500" />
          <span>Latency: {pipelineLatencySeconds.toFixed(1)}s</span>
          {initialSecondsRemaining != null && (
            <span className="text-zinc-500">
              ({formatDuration(initialSecondsRemaining)} at capture)
            </span>
          )}
        </div>
      )}
    </div>
  );
}
