import type { ReactNode } from "react";

import { Sparkline } from "@/components/charts/sparkline";
import { Panel } from "@/components/ui/panel";
import { cn } from "@/lib/cn";
import { TONE_CLASSES, type Tone } from "@/lib/pipeline";

export function StatCard({
  label,
  value,
  unit,
  hint,
  delta,
  deltaLabel,
  tone = "neutral",
  spark,
  sparkId,
  icon,
  footer,
  className,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  hint?: ReactNode;
  delta?: number;
  deltaLabel?: string;
  tone?: Tone;
  spark?: number[];
  sparkId?: string;
  icon?: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  return (
    <Panel className={cn("flex flex-col overflow-hidden p-4", className)}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-[10px] font-medium uppercase tracking-[0.15em] text-zinc-500">{label}</p>
        {icon ? <span className="text-zinc-600">{icon}</span> : null}
      </div>
      <div className="mt-2 flex items-baseline gap-1.5">
        <span className="mono tnum text-[26px] font-semibold leading-none tracking-tight text-zinc-50">
          {value}
        </span>
        {unit ? <span className="text-[11px] text-zinc-500">{unit}</span> : null}
      </div>
      <div className="mt-2 flex min-h-4 flex-wrap items-center gap-x-2 gap-y-1 text-[10px]">
        {delta !== undefined ? (
          <span
            className={cn(
              "mono inline-flex items-center gap-0.5",
              delta >= 0 ? "text-emerald-400" : "text-rose-400",
            )}
          >
            {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}%
          </span>
        ) : null}
        {deltaLabel ? <span className="text-zinc-600">{deltaLabel}</span> : null}
        {hint ? <span className="text-zinc-500">{hint}</span> : null}
      </div>
      {spark && sparkId ? (
        <div className="mt-3">
          <Sparkline values={spark} id={sparkId} width={240} height={34} tone={tone} className="w-full" />
        </div>
      ) : null}
      {footer ? <div className="mt-3 border-t border-line/60 pt-2.5">{footer}</div> : null}
      {tone !== "neutral" ? (
        <span className={cn("absolute inset-x-0 top-0 h-px", TONE_CLASSES[tone].dot, "opacity-40")} aria-hidden="true" />
      ) : null}
    </Panel>
  );
}
