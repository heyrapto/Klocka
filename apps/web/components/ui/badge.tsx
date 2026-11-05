import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { TONE_CLASSES, type Tone } from "@/lib/pipeline";

export function Badge({
  children,
  tone = "neutral",
  dot = false,
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  dot?: boolean;
  className?: string;
}) {
  const t = TONE_CLASSES[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium whitespace-nowrap",
        t.text,
        t.bg,
        t.border,
        className,
      )}
    >
      {dot ? <span className={cn("size-1.5 rounded-full", t.dot)} aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export function MonoTag({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "mono inline-flex items-center rounded border border-line bg-zinc-950/60 px-1.5 py-0.5 text-[10px] text-zinc-400",
        className,
      )}
    >
      {children}
    </span>
  );
}
