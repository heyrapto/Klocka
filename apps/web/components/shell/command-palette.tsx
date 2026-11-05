"use client";

import { useRouter } from "next/navigation";
import {
  ArrowRight,
  ChartNoAxesCombined,
  CornerDownLeft,
  Landmark,
  LayoutDashboard,
  ListChecks,
  ScanLine,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/cn";

export interface CommandItem {
  href: string;
  label: string;
  hint: string;
  group: "Navigate" | "Analyses" | "Forecasts" | "Markets";
}

const GROUP_ICONS = {
  Navigate: LayoutDashboard,
  Analyses: ListChecks,
  Forecasts: ChartNoAxesCombined,
  Markets: Landmark,
} as const;

const STATIC_ITEMS: CommandItem[] = [
  { href: "/", label: "Overview", hint: "Dashboard", group: "Navigate" },
  { href: "/new", label: "New analysis", hint: "Upload a screenshot", group: "Navigate" },
  { href: "/analyses", label: "Analyses", hint: "Pipeline history", group: "Navigate" },
  { href: "/forecasts", label: "Forecasts", hint: "Immutable forecast ledger", group: "Navigate" },
  { href: "/markets", label: "Markets", hint: "Verified contracts", group: "Navigate" },
  { href: "/models", label: "Models", hint: "Calibration and performance", group: "Navigate" },
  { href: "/research", label: "Research", hint: "Point-in-time evidence", group: "Navigate" },
  { href: "/settings", label: "Settings", hint: "Profile and data sources", group: "Navigate" },
];

export function CommandPalette({
  open,
  onClose,
  items,
}: {
  open: boolean;
  onClose: () => void;
  items: CommandItem[];
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const allItems = useMemo(() => [...STATIC_ITEMS, ...items], [items]);
  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return allItems;
    return allItems.filter((item) =>
      `${item.label} ${item.hint} ${item.group}`.toLowerCase().includes(normalized),
    );
  }, [allItems, query]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    const timer = window.setTimeout(() => inputRef.current?.focus(), 20);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive((current) => Math.min(current + 1, filtered.length - 1));
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive((current) => Math.max(current - 1, 0));
      }
      if (event.key === "Enter") {
        const item = filtered[active];
        if (item) {
          event.preventDefault();
          onClose();
          router.push(item.href);
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [active, filtered, onClose, open, router]);

  if (!open) return null;

  let lastGroup = "";

  return (
    <div
      className="fixed inset-0 z-[80] flex items-start justify-center bg-black/70 px-4 pt-[12vh] backdrop-blur-sm animate-fade-in"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search and commands"
        className="w-full max-w-xl overflow-hidden rounded-xl border border-zinc-700/80 bg-[#111113] shadow-[0_40px_120px_-20px_rgba(0,0,0,0.9)]"
      >
        <div className="flex items-center gap-3 border-b border-line px-4">
          <Search className="size-4 shrink-0 text-zinc-500" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            placeholder="Search markets, analyses, forecasts, pages…"
            className="h-12 w-full bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-600"
            aria-label="Search query"
            autoComplete="off"
          />
          <kbd className="mono rounded border border-line bg-zinc-900 px-1.5 py-0.5 text-[9px] text-zinc-500">
            ESC
          </kbd>
        </div>

        <div className="max-h-[52vh] overflow-y-auto p-2">
          {filtered.length === 0 ? (
            <div className="px-3 py-10 text-center">
              <p className="text-sm text-zinc-400">No results for “{query}”</p>
              <p className="mt-1 text-xs text-zinc-600">Try a market ticker, page name, or analysis ID.</p>
            </div>
          ) : (
            filtered.map((item, index) => {
              const Icon = GROUP_ICONS[item.group] ?? ScanLine;
              const showGroup = item.group !== lastGroup;
              lastGroup = item.group;
              return (
                <div key={`${item.group}-${item.href}`}>
                  {showGroup ? (
                    <p className="px-2.5 pb-1 pt-3 text-[9px] font-medium uppercase tracking-[0.16em] text-zinc-600">
                      {item.group}
                    </p>
                  ) : null}
                  <button
                    type="button"
                    onMouseEnter={() => setActive(index)}
                    onClick={() => {
                      onClose();
                      router.push(item.href);
                    }}
                    className={cn(
                      "flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-left transition-colors",
                      index === active ? "bg-zinc-800/80" : "hover:bg-zinc-800/40",
                    )}
                  >
                    <span
                      className={cn(
                        "flex size-7 shrink-0 items-center justify-center rounded-md border",
                        index === active
                          ? "border-sky-400/30 bg-sky-400/10 text-sky-300"
                          : "border-line bg-zinc-900 text-zinc-500",
                      )}
                    >
                      <Icon className="size-3.5" aria-hidden="true" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] text-zinc-200">{item.label}</span>
                      <span className="block truncate text-[10px] text-zinc-500">{item.hint}</span>
                    </span>
                    {index === active ? (
                      <CornerDownLeft className="size-3.5 shrink-0 text-zinc-500" aria-hidden="true" />
                    ) : (
                      <ArrowRight className="size-3.5 shrink-0 text-zinc-700" aria-hidden="true" />
                    )}
                  </button>
                </div>
              );
            })
          )}
        </div>

        <div className="flex items-center justify-between border-t border-line px-4 py-2.5 text-[10px] text-zinc-600">
          <span className="flex items-center gap-3">
            <span className="flex items-center gap-1">
              <kbd className="mono rounded border border-line bg-zinc-900 px-1">↑</kbd>
              <kbd className="mono rounded border border-line bg-zinc-900 px-1">↓</kbd>
              navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="mono rounded border border-line bg-zinc-900 px-1">↵</kbd>
              open
            </span>
          </span>
          <span>Read-only demo workspace</span>
        </div>
      </div>
    </div>
  );
}
