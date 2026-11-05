"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bell, ChevronRight, Menu, Search, ShieldCheck } from "lucide-react";
import { Fragment } from "react";

import { cn } from "@/lib/cn";

const SEGMENT_LABELS: Record<string, string> = {
  analyses: "Analyses",
  forecasts: "Forecasts",
  markets: "Markets",
  models: "Models",
  research: "Research",
  settings: "Settings",
  new: "New analysis",
};

function useBreadcrumbs() {
  const pathname = usePathname();
  const segments = pathname.split("/").filter(Boolean);
  if (segments.length === 0) {
    return [{ href: "/", label: "Overview" }];
  }
  return [
    { href: "/", label: "Overview" },
    ...segments.map((segment, index) => ({
      href: `/${segments.slice(0, index + 1).join("/")}`,
      label: SEGMENT_LABELS[segment] ?? (segment.length > 16 ? segment.slice(0, 8) : segment),
    })),
  ];
}

export function Topbar({
  onMenu,
  onSearch,
}: {
  onMenu: () => void;
  onSearch: () => void;
}) {
  const breadcrumbs = useBreadcrumbs();

  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b border-line/70 bg-canvas/80 px-4 backdrop-blur-xl sm:px-6">
      <button
        type="button"
        onClick={onMenu}
        className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 lg:hidden"
        aria-label="Open navigation"
      >
        <Menu className="size-5" />
      </button>

      <nav aria-label="Breadcrumb" className="min-w-0 flex-1">
        <ol className="flex items-center gap-1.5 overflow-hidden text-[11px]">
          {breadcrumbs.map((crumb, index) => {
            const last = index === breadcrumbs.length - 1;
            return (
              <Fragment key={crumb.href}>
                {index > 0 ? (
                  <ChevronRight className="size-3 shrink-0 text-zinc-700" aria-hidden="true" />
                ) : null}
                <li className="min-w-0 shrink-0">
                  {last ? (
                    <span aria-current="page" className="block truncate font-medium text-zinc-300">
                      {crumb.label}
                    </span>
                  ) : (
                    <Link
                      href={crumb.href}
                      className="block truncate text-zinc-500 transition-colors hover:text-zinc-300"
                    >
                      {crumb.label}
                    </Link>
                  )}
                </li>
              </Fragment>
            );
          })}
        </ol>
      </nav>

      <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
        <span className="hidden items-center gap-1.5 rounded-md border border-emerald-500/20 bg-emerald-500/8 px-2 py-1 text-[10px] text-emerald-400 xl:inline-flex">
          <ShieldCheck className="size-3" aria-hidden="true" />
          All gates nominal
        </span>

        <button
          type="button"
          onClick={onSearch}
          className={cn(
            "inline-flex h-8 items-center gap-2 rounded-md border border-line bg-surface px-2.5 text-[11px] text-zinc-500 transition-colors hover:border-zinc-700 hover:text-zinc-300",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/70",
          )}
          aria-label="Search and commands"
        >
          <Search className="size-3.5" aria-hidden="true" />
          <span className="hidden sm:inline">Search</span>
          <kbd className="mono hidden rounded border border-line bg-zinc-900 px-1 text-[9px] text-zinc-500 sm:inline">
            ⌘K
          </kbd>
        </button>

        <span className="mono hidden items-center gap-1.5 rounded-md border border-amber-500/25 bg-amber-500/8 px-2 py-1 text-[9px] uppercase tracking-[0.12em] text-amber-300 sm:inline-flex">
          <span className="size-1.5 rounded-full bg-amber-400" aria-hidden="true" />
          Demo data
        </span>

        <button
          type="button"
          className="relative rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
          aria-label="Notifications, 3 unread"
        >
          <Bell className="size-4" />
          <span className="absolute right-1.5 top-1.5 size-1.5 rounded-full bg-sky-400 ring-2 ring-canvas" aria-hidden="true" />
        </button>

        <Link
          href="/settings"
          className="flex size-7 items-center justify-center rounded-full bg-gradient-to-br from-zinc-600 to-zinc-800 text-[10px] font-semibold text-zinc-100 ring-1 ring-white/10 transition hover:ring-white/20"
          aria-label="Account settings"
        >
          DA
        </Link>
      </div>
    </header>
  );
}
