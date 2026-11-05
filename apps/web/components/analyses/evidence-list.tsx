import { ArrowUpRight, BookOpen, CircleCheck, CircleMinus, CircleX } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { formatDate } from "@/lib/format";
import { EVIDENCE_STANCE_META } from "@/lib/pipeline";
import type { EvidenceItem } from "@/lib/types";

const STANCE_ICON = {
  supports_yes: CircleCheck,
  supports_no: CircleX,
  neutral: CircleMinus,
} as const;

export function EvidenceList({ evidence }: { evidence: EvidenceItem[] }) {
  return (
    <ol className="divide-y divide-line/50">
      {evidence.map((item, index) => {
        const meta = EVIDENCE_STANCE_META[item.stance];
        const Icon = STANCE_ICON[item.stance];
        return (
          <li key={item.evidenceId} className="px-5 py-4">
            <div className="flex items-start gap-3">
              <span className="mono mt-0.5 shrink-0 rounded border border-line bg-zinc-950/60 px-1.5 py-0.5 text-[9px] text-zinc-500">
                {String(index + 1).padStart(2, "0")}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={meta.tone}>
                    <Icon className="size-3" aria-hidden="true" />
                    {meta.label}
                  </Badge>
                  <span className="text-[9px] uppercase tracking-[0.12em] text-zinc-600">
                    {item.authorityTier}
                  </span>
                  <span className="text-zinc-800">·</span>
                  <span className="mono text-[9px] text-zinc-600">
                    relevance {(item.relevanceScore * 100).toFixed(0)}%
                  </span>
                </div>
                <p className="mt-2 text-[12px] leading-relaxed text-zinc-300">“{item.snippet}”</p>
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
                  <a
                    href={item.sourceUrl}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="inline-flex items-center gap-1 text-[10px] text-sky-400/90 transition-colors hover:text-sky-300"
                  >
                    <BookOpen className="size-3" aria-hidden="true" />
                    {item.sourceCitation}
                    <ArrowUpRight className="size-2.5" aria-hidden="true" />
                  </a>
                  <span className="text-[9px] text-zinc-600">
                    Published {formatDate(item.sourceTimestamp)}
                  </span>
                  <span className="mono text-[9px] text-zinc-700">{item.evidenceId}</span>
                </div>
                <div className="mt-2 h-0.5 w-full overflow-hidden rounded-full bg-zinc-800/80">
                  <div
                    className={cn(
                      "h-full rounded-full",
                      item.stance === "supports_yes"
                        ? "bg-emerald-500/60"
                        : item.stance === "supports_no"
                          ? "bg-rose-500/60"
                          : "bg-zinc-500/60",
                    )}
                    style={{ width: `${item.relevanceScore * 100}%` }}
                  />
                </div>
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
