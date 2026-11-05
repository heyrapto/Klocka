import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export function Panel({
  children,
  className,
  as: Tag = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article" | "aside";
}) {
  return (
    <Tag
      className={cn(
        "relative rounded-xl border border-line/90 bg-surface/80 shadow-[0_1px_0_rgba(255,255,255,0.03)_inset,0_18px_40px_-24px_rgba(0,0,0,0.9)] backdrop-blur-sm",
        className,
      )}
    >
      {children}
    </Tag>
  );
}

export function PanelHeader({
  children,
  className,
  actions,
  description,
  title,
  eyebrow,
  icon,
}: {
  children?: ReactNode;
  className?: string;
  actions?: ReactNode;
  description?: ReactNode;
  title?: ReactNode;
  eyebrow?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4 border-b border-line/70 px-5 py-4",
        className,
      )}
    >
      <div className="min-w-0">
        {eyebrow ? (
          <p className="mb-1.5 text-[10px] font-medium uppercase tracking-[0.16em] text-zinc-500">
            {eyebrow}
          </p>
        ) : null}
        {title ? (
          <div className="flex items-center gap-2">
            {icon ? <span className="text-zinc-500">{icon}</span> : null}
            <h2 className="text-sm font-semibold tracking-tight text-zinc-100">{title}</h2>
          </div>
        ) : null}
        {description ? (
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-zinc-500">{description}</p>
        ) : null}
        {children}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function PanelBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={cn("px-5 py-4", className)}>{children}</div>;
}

export function PanelFooter({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-3 border-t border-line/70 px-5 py-3",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function SectionLabel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <p
      className={cn(
        "text-[10px] font-medium uppercase tracking-[0.16em] text-zinc-500",
        className,
      )}
    >
      {children}
    </p>
  );
}
