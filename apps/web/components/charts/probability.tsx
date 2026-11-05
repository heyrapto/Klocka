import { cn } from "@/lib/cn";
import { formatCents, formatProbability } from "@/lib/format";

export function ProbabilityInterval({
  probability,
  interval,
  marketProbability,
  className,
  showScale = true,
}: {
  probability: number;
  interval: [number, number];
  marketProbability?: number;
  className?: string;
  showScale?: boolean;
}) {
  const clamp = (value: number) => Math.min(1, Math.max(0, value));
  const left = clamp(interval[0]);
  const right = clamp(interval[1]);
  const point = clamp(probability);

  return (
    <div className={cn("w-full", className)}>
      <div className="relative h-7">
        <div className="absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 rounded-full bg-zinc-800/90 ring-1 ring-inset ring-white/5" />
        <div
          className="absolute top-1/2 h-2 -translate-y-1/2 rounded-full bg-sky-400/25 ring-1 ring-inset ring-sky-400/30"
          style={{ left: `${left * 100}%`, width: `${Math.max(0.5, (right - left) * 100)}%` }}
          aria-hidden="true"
        />
        <div
          className="absolute top-1/2 h-4 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-sky-300 shadow-[0_0_10px_rgba(56,189,248,0.6)]"
          style={{ left: `${point * 100}%` }}
          aria-hidden="true"
        />
        {marketProbability !== undefined ? (
          <div
            className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 border border-amber-300/80 bg-amber-400"
            style={{ left: `${clamp(marketProbability) * 100}%` }}
            title={`Market-implied ${formatCents(marketProbability)}`}
            aria-hidden="true"
          />
        ) : null}
      </div>
      {showScale ? (
        <div className="mono mt-0.5 flex justify-between text-[9px] text-zinc-600">
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-zinc-500">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-3 rounded-full bg-sky-300" aria-hidden="true" />
          Point estimate {formatProbability(probability)}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-3 rounded-sm bg-sky-400/25 ring-1 ring-inset ring-sky-400/30" aria-hidden="true" />
          90% interval {formatProbability(left)}–{formatProbability(right)}
        </span>
        {marketProbability !== undefined ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2 rotate-45 bg-amber-400" aria-hidden="true" />
            Market {formatCents(marketProbability)}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function EdgeMeter({
  netEdge,
  requiredEdge,
  className,
}: {
  netEdge: number;
  requiredEdge: number;
  className?: string;
}) {
  const max = Math.max(requiredEdge * 1.9, Math.abs(netEdge) * 1.25, 0.02);
  const clamped = Math.min(Math.abs(netEdge), max);
  const passes = netEdge >= requiredEdge;
  const negative = netEdge < 0;
  const fill = negative ? "bg-rose-500/70" : passes ? "bg-emerald-400/80" : "bg-zinc-500/70";

  return (
    <div className={cn("w-full", className)}>
      <div className="relative h-2.5 overflow-hidden rounded-full bg-zinc-800/90 ring-1 ring-inset ring-white/5">
        <div
          className={cn("h-full rounded-full transition-[width] duration-500", fill)}
          style={{ width: `${(clamped / max) * 100}%` }}
        />
        <div
          className="absolute inset-y-0 w-px bg-rose-300/90"
          style={{ left: `${(Math.min(requiredEdge, max) / max) * 100}%` }}
          aria-hidden="true"
        />
      </div>
      <div className="mt-2 flex items-center justify-between text-[10px] text-zinc-500">
        <span>
          Net edge{" "}
          <span className={cn("mono", negative ? "text-rose-400" : passes ? "text-emerald-400" : "text-zinc-300")}>
            {netEdge >= 0 ? "+" : ""}
            {(netEdge * 100).toFixed(1)}%
          </span>
        </span>
        <span>
          Required <span className="mono text-zinc-300">{(requiredEdge * 100).toFixed(1)}%</span>
        </span>
      </div>
    </div>
  );
}
