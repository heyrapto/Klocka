import { cn } from "@/lib/cn";

export function Logo({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "relative flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-[9px] border border-sky-300/25 bg-gradient-to-br from-sky-400/20 via-zinc-900 to-zinc-950 shadow-[0_0_18px_-4px_rgba(56,189,248,0.5)]",
        className,
      )}
      aria-hidden="true"
    >
      <svg viewBox="0 0 24 24" className="size-4.5" fill="none">
        <path
          d="M4 17.5 9.2 12l3.3 3.1L20 6.5"
          stroke="#7dd3fc"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path d="M15.5 6.5H20v4.4" stroke="#38bdf8" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="9.2" cy="12" r="1.4" fill="#e0f2fe" />
      </svg>
    </span>
  );
}

export function BrandLockup({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <Logo />
      {compact ? null : (
        <div className="min-w-0">
          <div className="flex items-baseline gap-1.5">
            <span className="font-brand text-[19px] leading-none tracking-[-0.01em] text-zinc-50">Klocka</span>
            <span className="mono text-[8px] uppercase tracking-[0.16em] text-sky-400/80">v1.0</span>
          </div>
          <p className="truncate text-[9px] uppercase tracking-[0.14em] text-zinc-500">
            Prediction
          </p>
        </div>
      )}
    </div>
  );
}
