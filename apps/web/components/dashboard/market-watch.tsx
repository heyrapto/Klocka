import Link from "next/link";
import { ArrowUpRight, Landmark } from "lucide-react";

import { Sparkline } from "@/components/charts/sparkline";
import { Panel, PanelHeader } from "@/components/ui/panel";
import { cn } from "@/lib/cn";
import { formatCents, formatCompactUsd } from "@/lib/format";
import { PLATFORM_LABELS } from "@/lib/pipeline";
import type { Market } from "@/lib/types";

export function MarketWatch({ markets }: { markets: Market[] }) {
  return (
    <Panel className="overflow-hidden">
      <PanelHeader
        eyebrow="Live contracts"
        title="Market watch"
        description="Executable venue prices, not screenshot values."
        actions={
          <Link
            href="/markets"
            className="text-[10px] text-zinc-500 transition-colors hover:text-sky-300"
          >
            All markets
          </Link>
        }
        icon={<Landmark className="size-3.5" />}
      />
      <ul className="divide-y divide-line/50">
        {markets.slice(0, 4).map((market) => {
          const mid = (market.bestBidYes + market.bestAskYes) / 2;
          return (
            <li key={market.id}>
              <Link
                href={`/markets/${market.id}`}
                className="group block px-5 py-3.5 transition-colors hover:bg-zinc-800/25"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="line-clamp-2 text-[12px] font-medium leading-snug text-zinc-200 transition-colors group-hover:text-sky-300">
                      {market.title}
                    </p>
                    <div className="mt-1 flex items-center gap-2 text-[9px] text-zinc-600">
                      <span className="uppercase tracking-wider">{PLATFORM_LABELS[market.platform]}</span>
                      <span>·</span>
                      <span className="mono">{formatCompactUsd(market.liquidityUsd)} liq</span>
                    </div>
                  </div>
                  <ArrowUpRight className="mt-0.5 size-3.5 shrink-0 text-zinc-700 transition-colors group-hover:text-sky-300" />
                </div>
                <div className="mt-2.5 flex items-end justify-between gap-3">
                  <div className="flex items-baseline gap-2">
                    <span className="mono tnum text-[15px] font-medium text-zinc-100">
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
                  <Sparkline
                    values={market.priceHistory}
                    id={`watch-${market.id}`}
                    width={88}
                    height={26}
                    tone={market.change24h >= 0 ? "positive" : "negative"}
                  />
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
