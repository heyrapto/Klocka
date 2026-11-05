import Link from "next/link";
import { Activity, BrainCircuit, CircleAlert, CircleCheck, Radar } from "lucide-react";

import { Panel, PanelHeader } from "@/components/ui/panel";
import { cn } from "@/lib/cn";
import { DEMO_NOW } from "@/lib/demo-data";
import { formatRelative } from "@/lib/format";
import type { ActivityEvent } from "@/lib/types";

const KIND_META = {
  forecast: { icon: Radar, tone: "text-sky-400", ring: "border-sky-400/25 bg-sky-400/10" },
  resolution: { icon: CircleCheck, tone: "text-emerald-400", ring: "border-emerald-400/25 bg-emerald-400/10" },
  analysis: { icon: Activity, tone: "text-zinc-400", ring: "border-zinc-600 bg-zinc-800" },
  model: { icon: BrainCircuit, tone: "text-violet-300", ring: "border-violet-400/25 bg-violet-400/10" },
  warning: { icon: CircleAlert, tone: "text-amber-400", ring: "border-amber-400/25 bg-amber-400/10" },
} as const;

export function ActivityFeed({ events }: { events: ActivityEvent[] }) {
  return (
    <Panel className="overflow-hidden">
      <PanelHeader
        eyebrow="Audit stream"
        title="Recent activity"
        description="System events and forecast emissions."
        icon={<Activity className="size-3.5" />}
      />
      <ol className="px-5 py-2">
        {events.map((event, index) => {
          const meta = KIND_META[event.kind];
          const Icon = meta.icon;
          const content = (
            <div className="flex gap-3">
              <div className="relative flex flex-col items-center">
                <span
                  className={cn(
                    "z-10 flex size-6 shrink-0 items-center justify-center rounded-full border",
                    meta.ring,
                  )}
                >
                  <Icon className={cn("size-3", meta.tone)} aria-hidden="true" />
                </span>
                {index < events.length - 1 ? (
                  <span className="absolute top-6 h-[calc(100%-12px)] w-px bg-line" aria-hidden="true" />
                ) : null}
              </div>
              <div className={cn("min-w-0 flex-1", index < events.length - 1 ? "pb-4" : "pb-1")}>
                <div className="flex items-start justify-between gap-2">
                  <p className="text-[11px] font-medium leading-snug text-zinc-200">{event.title}</p>
                  <span className="mono shrink-0 text-[9px] text-zinc-600">
                    {formatRelative(event.timestamp, DEMO_NOW)}
                  </span>
                </div>
                <p className="mt-0.5 text-[10px] leading-relaxed text-zinc-500">{event.detail}</p>
              </div>
            </div>
          );
          return (
            <li key={event.id}>
              {event.href ? (
                <Link href={event.href} className="block rounded-md py-2 transition-colors hover:bg-zinc-800/25">
                  {content}
                </Link>
              ) : (
                <div className="py-2">{content}</div>
              )}
            </li>
          );
        })}
      </ol>
    </Panel>
  );
}
