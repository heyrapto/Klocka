import Link from "next/link";
import { ArrowUpRight, CircleAlert } from "lucide-react";

import { Sparkline } from "@/components/charts/sparkline";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { formatCents, formatCompactUsd, formatDate } from "@/lib/format";
import { PLATFORM_LABELS } from "@/lib/pipeline";
import type { Market } from "@/lib/types";

export function MarketCard({ market }: { market: Market }) {
  const mid = (market.bestBidYes + market.bestAskYes) / 2;
  const spreadPct = market.bestAskYes - market.bestBidYes;
  return (
    <Link
      href={`/markets/${market.id}`}
      className="group flex flex-col rounded-xl border border-line/90 bg-surface/80 p-4 transition-colors hover:border-zinc-700 hover:bg-zinc-900/70"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Badge tone={market.platform === "polymarket" ? "info" : "neutral"}>
            {PLATFORM_LABELS[market.platform]}
          </Badge>
          <Badge tone="neutral">{market.category}</Badge>
        </div>
        <ArrowUpRight className="size-3.5 shrink-0 text-zinc-700 transition-colors group-hover:text-sky-300" aria-hidden="true" />
      </div>

      <h3 className="mt-3 line-clamp-2 text-[13px] font-medium leading-snug text-zinc-100 transition-colors group-hover:text-sky-200">
        {market.title}
      </h3>

      <div className="mt-4 flex items-end justify-between gap-3">
        <div>
          <div className="flex items-baseline gap-2">
            <span className="mono tnum text-2xl font-semibold tracking-tight text-zinc-50">
              {formatCents(mid)}
            </span>
            <span
              className={cn(
                "mono text-[10px]",
                market.change24h >= 0 ? "text-emerald-400" : "text-rose-400",
              )}
            >
              {market.change24h >= 0 ? "+" : ""}
              {market.change24h.toFixed(1)}%
            </span>
          </div>
          <p className="mt-0.5 text-[9px] uppercase tracking-[0.12em] text-zinc-600">
            Market-implied YES
          </p>
        </div>
        <Sparkline
          values={market.priceHistory}
          id={`market-${market.id}`}
          width={110}
          height={34}
          tone={market.change24h >= 0 ? "positive" : "negative"}
        />
      </div>

      <div className="mt-4 grid grid-cols-3 gap-2 border-t border-line/60 pt-3">
        <div>
          <p className="text-[8px] uppercase tracking-[0.12em] text-zinc-600">Bid / ask</p>
          <p className="mono mt-0.5 text-[10px] text-zinc-400">
            {formatCents(market.bestBidYes)} / {formatCents(market.bestAskYes)}
          </p>
        </div>
        <div>
          <p className="text-[8px] uppercase tracking-[0.12em] text-zinc-600">Spread</p>
          <p className="mono mt-0.5 text-[10px] text-zinc-400">{(spreadPct * 100).toFixed(1)}¢</p>
        </div>
        <div>
          <p className="text-[8px] uppercase tracking-[0.12em] text-zinc-600">Liquidity</p>
          <p className="mono mt-0.5 text-[10px] text-zinc-400">{formatCompactUsd(market.liquidityUsd)}</p>
        </div>
      </div>

      <div className="mt-2.5 flex items-center justify-between text-[9px] text-zinc-600">
        <span>Closes {formatDate(market.closesAt)}</span>
        {market.riskFlags.length > 0 ? (
          <span className="inline-flex items-center gap-1 text-amber-400/80">
            <CircleAlert className="size-2.5" aria-hidden="true" />
            {market.riskFlags.length} risk flag{market.riskFlags.length === 1 ? "" : "s"}
          </span>
        ) : (
          <span className="text-emerald-400/70">No risk flags</span>
        )}
      </div>
    </Link>
  );
}
