import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

import { RecommendationBadge } from "@/components/ui/recommendation-badge";
import { StatusBadge } from "@/components/analyses/status-badge";
import { cn } from "@/lib/cn";
import { DEMO_NOW, getForecastByAnalysisId } from "@/lib/demo-data";
import { formatProbability, formatRelative, formatSignedPercent } from "@/lib/format";
import { PLATFORM_LABELS } from "@/lib/pipeline";
import type { Analysis } from "@/lib/types";

export function AnalysisTable({
  items,
  className,
  showEdge = true,
}: {
  items: Analysis[];
  className?: string;
  showEdge?: boolean;
}) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full min-w-[760px] border-collapse text-left">
        <thead>
          <tr className="border-b border-line/70 text-[9px] uppercase tracking-[0.14em] text-zinc-600">
            <th scope="col" className="px-5 py-2.5 font-medium">Market</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Status</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Recommendation</th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">P(YES)</th>
            {showEdge ? (
              <th scope="col" className="px-3 py-2.5 text-right font-medium">Net edge</th>
            ) : null}
            <th scope="col" className="px-3 py-2.5 text-right font-medium">Completed</th>
            <th scope="col" className="w-10 px-3 py-2.5">
              <span className="sr-only">Open</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((analysis) => {
            const forecast = getForecastByAnalysisId(analysis.id);
            return (
              <tr
                key={analysis.id}
                className="group border-b border-line/45 transition-colors last:border-0 hover:bg-zinc-800/25"
              >
                <td className="max-w-[360px] px-5 py-3">
                  <Link
                    href={`/analyses/${analysis.id}`}
                    className="block truncate text-[13px] font-medium text-zinc-200 transition-colors group-hover:text-sky-300"
                  >
                    {analysis.marketTitle}
                  </Link>
                  <div className="mt-1 flex items-center gap-2 text-[10px] text-zinc-500">
                    <span>{PLATFORM_LABELS[analysis.platform]}</span>
                    <span className="text-zinc-700">·</span>
                    <span className="mono">{analysis.id.slice(0, 8)}</span>
                    {analysis.durationSeconds ? (
                      <>
                        <span className="text-zinc-700">·</span>
                        <span>{analysis.durationSeconds.toFixed(1)}s</span>
                      </>
                    ) : null}
                  </div>
                </td>
                <td className="px-3 py-3">
                  <StatusBadge status={analysis.status} />
                </td>
                <td className="px-3 py-3">
                  {analysis.recommendation ? (
                    <RecommendationBadge recommendation={analysis.recommendation} />
                  ) : (
                    <span className="text-[11px] text-zinc-600">—</span>
                  )}
                </td>
                <td className="px-3 py-3 text-right">
                  {forecast ? (
                    <div>
                      <span className="mono tnum text-[13px] text-zinc-200">
                        {formatProbability(forecast.probabilityYes)}
                      </span>
                      <div className="mono text-[9px] text-zinc-600">
                        {formatProbability(forecast.uncertaintyInterval[0])}–
                        {formatProbability(forecast.uncertaintyInterval[1])}
                      </div>
                    </div>
                  ) : (
                    <span className="text-[11px] text-zinc-600">—</span>
                  )}
                </td>
                {showEdge ? (
                  <td className="px-3 py-3 text-right">
                    {forecast ? (
                      <div>
                        <span
                          className={cn(
                            "mono tnum text-[12px]",
                            forecast.decisionMetrics.estimatedNetEdgeYes >=
                              forecast.decisionMetrics.minimumRequiredEdge
                              ? "text-emerald-400"
                              : forecast.decisionMetrics.estimatedNetEdgeYes < 0
                                ? "text-rose-400"
                                : "text-zinc-400",
                          )}
                        >
                          {formatSignedPercent(forecast.decisionMetrics.estimatedNetEdgeYes)}
                        </span>
                        <div className="mono text-[9px] text-zinc-600">
                          req {formatSignedPercent(forecast.decisionMetrics.minimumRequiredEdge)}
                        </div>
                      </div>
                    ) : (
                      <span className="text-[11px] text-zinc-600">—</span>
                    )}
                  </td>
                ) : null}
                <td className="whitespace-nowrap px-3 py-3 text-right text-[11px] text-zinc-500">
                  {analysis.completedAt ? formatRelative(analysis.completedAt, DEMO_NOW) : "—"}
                </td>
                <td className="px-3 py-3">
                  <Link
                    href={`/analyses/${analysis.id}`}
                    className="flex size-7 items-center justify-center rounded-md text-zinc-600 transition-colors hover:bg-zinc-800 hover:text-sky-300"
                    aria-label={`Open analysis for ${analysis.marketTitle}`}
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
