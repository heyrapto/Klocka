import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { RecommendationBadge } from "@/components/ui/recommendation-badge";
import { cn } from "@/lib/cn";
import { formatProbability, formatSignedPercent } from "@/lib/format";
import { PLATFORM_LABELS } from "@/lib/pipeline";
import type { Forecast, Market } from "@/lib/types";

export function ForecastTable({
  items,
  markets,
}: {
  items: Forecast[];
  markets: Map<string, Market>;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px] border-collapse text-left">
        <thead>
          <tr className="border-b border-line/70 text-[9px] uppercase tracking-[0.14em] text-zinc-600">
            <th scope="col" className="px-5 py-2.5 font-medium">Market</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Recommendation</th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">P(YES)</th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">Net edge</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Outcome</th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">Brier</th>
            <th scope="col" className="px-5 py-2.5 text-right font-medium">Forecasted</th>
            <th scope="col" className="w-10 px-3 py-2.5">
              <span className="sr-only">Open</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((forecast) => {
            const market = markets.get(forecast.marketId);
            const edge = forecast.decisionMetrics.estimatedNetEdgeYes;
            const required = forecast.decisionMetrics.minimumRequiredEdge;
            return (
              <tr
                key={forecast.id}
                className="group border-b border-line/45 transition-colors last:border-0 hover:bg-zinc-800/25"
              >
                <td className="max-w-[360px] px-5 py-3">
                  <Link
                    href={`/forecasts/${forecast.id}`}
                    className="block truncate text-[13px] font-medium text-zinc-200 transition-colors group-hover:text-sky-300"
                  >
                    {market?.title ?? "Unknown market"}
                  </Link>
                  <div className="mt-1 flex items-center gap-2 text-[10px] text-zinc-500">
                    <span>{market ? PLATFORM_LABELS[market.platform] : "—"}</span>
                    <span className="text-zinc-700">·</span>
                    <span className="mono">{forecast.modelVersion}</span>
                    <span className="text-zinc-700">·</span>
                    <span className="mono">{forecast.id.slice(0, 8)}</span>
                  </div>
                </td>
                <td className="px-3 py-3">
                  <RecommendationBadge recommendation={forecast.recommendation} />
                </td>
                <td className="px-3 py-3 text-right">
                  <span className="mono tnum text-[13px] text-zinc-200">
                    {formatProbability(forecast.probabilityYes)}
                  </span>
                  <div className="mono text-[9px] text-zinc-600">
                    {formatProbability(forecast.uncertaintyInterval[0])}–
                    {formatProbability(forecast.uncertaintyInterval[1])}
                  </div>
                </td>
                <td className="px-3 py-3 text-right">
                  <span
                    className={cn(
                      "mono tnum text-[12px]",
                      edge >= required ? "text-emerald-400" : edge < 0 ? "text-rose-400" : "text-zinc-400",
                    )}
                  >
                    {formatSignedPercent(edge)}
                  </span>
                  <div className="mono text-[9px] text-zinc-600">req {formatSignedPercent(required)}</div>
                </td>
                <td className="px-3 py-3">
                  <Badge
                    tone={
                      forecast.resolvedOutcome === "YES"
                        ? "info"
                        : forecast.resolvedOutcome === "NO"
                          ? "negative"
                          : forecast.resolvedOutcome === "VOID"
                            ? "caution"
                            : "neutral"
                    }
                  >
                    {forecast.resolvedOutcome}
                  </Badge>
                </td>
                <td className="px-3 py-3 text-right">
                  <span className="mono text-[11px] text-zinc-400">
                    {forecast.brierContribution !== null ? forecast.brierContribution.toFixed(3) : "—"}
                  </span>
                </td>
                <td className="whitespace-nowrap px-5 py-3 text-right">
                  <span className="mono text-[10px] text-zinc-500">
                    {forecast.forecastedAt.slice(0, 16).replace("T", " ")}
                  </span>
                  <div className="text-[9px] text-zinc-700">UTC</div>
                </td>
                <td className="px-3 py-3">
                  <Link
                    href={`/forecasts/${forecast.id}`}
                    className="flex size-7 items-center justify-center rounded-md text-zinc-600 transition-colors hover:bg-zinc-800 hover:text-sky-300"
                    aria-label={`Open forecast ${forecast.id}`}
                  >
                    <ArrowUpRight className="size-3.5" />
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
