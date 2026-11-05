import { ArrowDownRight, ArrowUpRight, Shield, ShieldCheck, ShieldX } from "lucide-react";

import { cn } from "@/lib/cn";
import { RECOMMENDATION_META, TONE_CLASSES } from "@/lib/pipeline";
import type { Recommendation } from "@/lib/types";

const ICONS: Record<Recommendation, typeof Shield> = {
  BUY_YES: ShieldCheck,
  BUY_NO: ShieldX,
  SELL_YES: ArrowDownRight,
  SELL_NO: ArrowUpRight,
  NO_TRADE: Shield,
};

export function RecommendationBadge({
  recommendation,
  size = "sm",
  description,
  className,
}: {
  recommendation: Recommendation;
  size?: "sm" | "md" | "lg";
  description?: string;
  className?: string;
}) {
  const meta = RECOMMENDATION_META[recommendation];
  const tone = TONE_CLASSES[meta.tone];
  const Icon = ICONS[recommendation];
  const label = recommendation.replaceAll("_", " ");
  const caption = description ?? meta.description;

  if (size === "sm") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] font-semibold tracking-wide",
          tone.text,
          tone.bg,
          tone.border,
          className,
        )}
        title={caption}
      >
        <Icon className="size-3.5" aria-hidden="true" />
        {label}
      </span>
    );
  }

  if (size === "md") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-2 rounded-lg border px-3 py-2",
          tone.text,
          tone.bg,
          tone.border,
          className,
        )}
      >
        <Icon className="size-4" aria-hidden="true" />
        <span className="mono text-xs font-semibold tracking-[0.08em]">{label}</span>
      </span>
    );
  }

  return (
    <div
      className={cn(
        "flex items-start gap-4 rounded-xl border p-4",
        tone.bg,
        tone.border,
        className,
      )}
    >
      <span
        className={cn(
          "mt-0.5 flex size-10 shrink-0 items-center justify-center rounded-lg border",
          tone.border,
          "bg-zinc-950/40",
        )}
      >
        <Icon className={cn("size-5", tone.text)} aria-hidden="true" />
      </span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("mono text-lg font-semibold tracking-[0.06em]", tone.text)}>
            {label}
          </span>
          <span className="text-[10px] uppercase tracking-[0.14em] text-zinc-500">
            Recommendation
          </span>
        </div>
        <p className="mt-1 max-w-md text-xs leading-relaxed text-zinc-400">{caption}</p>
      </div>
    </div>
  );
}
