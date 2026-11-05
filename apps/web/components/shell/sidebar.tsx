"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BrainCircuit,
  ChartNoAxesCombined,
  Landmark,
  LayoutDashboard,
  Library,
  ListChecks,
  ScanLine,
  Settings,
  X,
} from "lucide-react";
import { useEffect } from "react";

import { BrandLockup } from "@/components/shell/logo";
import { cn } from "@/lib/cn";
import { TONE_CLASSES } from "@/lib/pipeline";

const NAV_GROUPS = [
  {
    label: "Workspace",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard },
      { href: "/new", label: "New analysis", icon: ScanLine, accent: true },
      { href: "/analyses", label: "Analyses", icon: ListChecks, countKey: "analyses" },
      { href: "/forecasts", label: "Forecasts", icon: ChartNoAxesCombined },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { href: "/markets", label: "Markets", icon: Landmark },
      { href: "/models", label: "Models", icon: BrainCircuit },
      { href: "/research", label: "Research", icon: Library },
    ],
  },
  {
    label: "System",
    items: [{ href: "/settings", label: "Settings", icon: Settings }],
  },
] as const;

export interface FeedStatusItem {
  name: string;
  latencyMs: number;
  health: "fresh" | "degraded" | "stale";
}

export function Sidebar({
  open,
  onClose,
  counts,
  feeds,
}: {
  open: boolean;
  onClose: () => void;
  counts: { analyses: number; forecasts: number };
  feeds: FeedStatusItem[];
}) {
  const pathname = usePathname();

  useEffect(() => {
    onClose();
    // Close the mobile drawer whenever the route changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  return (
    <>
      <div
        className={cn(
          "fixed inset-0 z-40 bg-black/65 backdrop-blur-sm transition-opacity lg:hidden",
          open ? "opacity-100" : "pointer-events-none opacity-0",
        )}
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-[260px] flex-col border-r border-line/80 bg-[#0b0b0d]/95 backdrop-blur-xl transition-transform duration-300 lg:z-30 lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
        aria-label="Primary navigation"
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-line/70 px-5">
          <Link href="/" className="rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400">
            <BrandLockup />
          </Link>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200 lg:hidden"
            aria-label="Close navigation"
          >
            <X className="size-4" />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-5">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="mb-6">
              <p className="mb-2 px-2.5 text-[9px] font-medium uppercase tracking-[0.18em] text-zinc-600">
                {group.label}
              </p>
              <ul className="space-y-0.5">
                {group.items.map((item) => {
                  const active =
                    item.href === "/"
                      ? pathname === "/"
                      : pathname === item.href || pathname.startsWith(`${item.href}/`);
                  const Icon = item.icon;
                  const count =
                    "countKey" in item && (item as { countKey?: string }).countKey === "analyses"
                      ? counts.analyses
                      : "countKey" in item && (item as { countKey?: string }).countKey === "forecasts"
                        ? counts.forecasts
                        : null;
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        aria-current={active ? "page" : undefined}
                        className={cn(
                          "group relative flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] transition-colors",
                          active
                            ? "bg-zinc-800/70 text-zinc-50 shadow-[0_1px_0_rgba(255,255,255,0.04)_inset]"
                            : "text-zinc-400 hover:bg-zinc-800/40 hover:text-zinc-200",
                        )}
                      >
                        {active ? (
                          <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-sky-400" aria-hidden="true" />
                        ) : null}
                        <Icon
                          className={cn(
                            "size-4 shrink-0",
                            active ? "text-sky-300" : "text-zinc-500 group-hover:text-zinc-300",
                          )}
                          strokeWidth={1.7}
                        />
                        <span className="flex-1 truncate">{item.label}</span>
                        {"accent" in item && item.accent ? (
                          <span className="mono rounded border border-sky-400/20 bg-sky-400/10 px-1 text-[8px] uppercase tracking-wider text-sky-300">
                            new
                          </span>
                        ) : null}
                        {count !== null ? (
                          <span className="mono text-[10px] text-zinc-600">{count}</span>
                        ) : null}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="shrink-0 border-t border-line/70 px-3 py-3">
          <div className="mb-2 flex items-center justify-between px-2">
            <p className="text-[9px] font-medium uppercase tracking-[0.18em] text-zinc-600">Data plane</p>
            <span className="inline-flex items-center gap-1 text-[9px] text-emerald-400">
              <span className="size-1.5 animate-pulse-soft rounded-full bg-emerald-400" aria-hidden="true" />
              live
            </span>
          </div>
          <ul className="space-y-1">
            {feeds.map((feed) => {
              const tone = TONE_CLASSES[feed.health === "fresh" ? "positive" : feed.health === "degraded" ? "caution" : "negative"];
              return (
                <li key={feed.name} className="flex items-center justify-between gap-2 px-2 py-0.5 text-[10px]">
                  <span className="flex min-w-0 items-center gap-1.5 text-zinc-500">
                    <span className={cn("size-1.5 shrink-0 rounded-full", tone.dot)} aria-hidden="true" />
                    <span className="truncate">{feed.name}</span>
                  </span>
                  <span className="mono shrink-0 text-zinc-600">{feed.latencyMs >= 1000 ? `${(feed.latencyMs / 1000).toFixed(1)}s` : `${feed.latencyMs}ms`}</span>
                </li>
              );
            })}
          </ul>

          <div className="mt-3 flex items-center gap-2.5 rounded-lg border border-line/80 bg-zinc-900/60 p-2.5">
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-zinc-600 to-zinc-800 text-[10px] font-semibold text-zinc-200 ring-1 ring-white/10">
              DA
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[11px] font-medium text-zinc-200">Demo analyst</p>
              <p className="truncate text-[9px] text-zinc-500">developer@prediction.ai</p>
            </div>
            <span className="mono rounded border border-zinc-700 bg-zinc-800/80 px-1.5 py-0.5 text-[8px] uppercase tracking-wider text-zinc-400">
              demo
            </span>
          </div>
        </div>
      </aside>
    </>
  );
}
