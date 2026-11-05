import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { TONE_CLASSES, type Tone } from "@/lib/pipeline";

export function Metric({
  label,
  value,
  unit,
  hint,
  tone = "neutral",
  size = "md",
  className,
}: {
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const valueSize =
    size === "lg"
      ? "text-3xl sm:text-4xl"
      : size === "sm"
        ? "text-lg"
        : "text-2xl";

  return (
    <div className={cn("min-w-0", className)}>
      <div className="text-[10px] font-medium uppercase tracking-[0.15em] text-zinc-500">
        {label}
      </div>
      <div className="mt-1.5 flex items-baseline gap-1.5">
        <span
          className={cn(
            "mono tnum font-semibold tracking-tight",
            valueSize,
            tone === "neutral" ? "text-zinc-100" : TONE_CLASSES[tone].text,
          )}
        >
          {value}
        </span>
        {unit ? (
          <span className="text-xs font-medium text-zinc-500">{unit}</span>
        ) : null}
      </div>
      {hint ? (
        <div className="mt-1 text-[11px] leading-relaxed text-zinc-500">{hint}</div>
      ) : null}
    </div>
  );
}

export function MetricRow({
  children,
  className,
  columns = 3,
}: {
  children: ReactNode;
  className?: string;
  columns?: 2 | 3 | 4;
}) {
  const grid =
    columns === 2
      ? "sm:grid-cols-2"
      : columns === 3
        ? "sm:grid-cols-2 lg:grid-cols-3"
        : "sm:grid-cols-2 lg:grid-cols-4";
  return (
    <div className={cn("grid grid-cols-1 gap-px overflow-hidden rounded-lg bg-line/60", grid, className)}>
      {children}
    </div>
  );
}

export function MetricCell({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={cn("bg-surface px-4 py-3.5", className)}>{children}</div>;
}
