"use client";

import { useEffect, useState } from "react";

import { CommandPalette, type CommandItem } from "@/components/shell/command-palette";
import { Sidebar, type FeedStatusItem } from "@/components/shell/sidebar";
import { Topbar } from "@/components/shell/topbar";

export function AppShell({
  children,
  commandItems,
  feeds,
  counts,
}: {
  children: React.ReactNode;
  commandItems: CommandItem[];
  feeds: FeedStatusItem[];
  counts: { analyses: number; forecasts: number };
}) {
  const [navOpen, setNavOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.isContentEditable;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen((current) => !current);
      }
      if (event.key === "/" && !typing) {
        event.preventDefault();
        setSearchOpen(true);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  return (
    <div className="min-h-screen lg:pl-[260px]">
      <Sidebar
        open={navOpen}
        onClose={() => setNavOpen(false)}
        counts={counts}
        feeds={feeds}
      />
      <div className="flex min-h-screen flex-col">
        <Topbar onMenu={() => setNavOpen(true)} onSearch={() => setSearchOpen(true)} />
        <main className="mx-auto w-full max-w-[1560px] flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          {children}
        </main>
        <footer className="mx-auto flex w-full max-w-[1560px] flex-wrap items-center justify-between gap-2 border-t border-line/60 px-4 py-4 text-[10px] text-zinc-600 sm:px-6 lg:px-8">
          <span>
            Vector · Analysis and decision support only. No order execution, custody, or guaranteed returns.
          </span>
          <span className="mono">DEMO · fixtures 2026-11-04 · policy ev_policy_v1.2.0</span>
        </footer>
      </div>
      <CommandPalette open={searchOpen} onClose={() => setSearchOpen(false)} items={commandItems} />
    </div>
  );
}
