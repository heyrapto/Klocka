"use client";

/**
 * PredictionSuccessModal
 *
 * Celebratory full-screen modal shown when a prediction the agent made
 * resolves successfully. Triggered via the `usePredictionSuccess` hook.
 *
 * Layout rules:
 *  - Client Component (uses browser state + animations).
 *  - Portal-rendered to <body> via a <dialog> element for correct stacking.
 *  - Closes on Escape, backdrop click, or the dismiss button.
 *  - Never displays a "green positive badge" for NO_TRADE outcomes.
 *  - Recommendation label is display-only; no trade is executed.
 */

import { useCallback, useEffect, useRef } from "react";
import {
  Trophy,
  TrendingUp,
  Target,
  CheckCircle,
  X,
  Sparkles,
} from "lucide-react";

import { Confetti } from "@/components/ui/confetti";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import { formatProbability, formatDateTime, formatSignedPercent } from "@/lib/format";
import { PLATFORM_LABELS, RECOMMENDATION_META } from "@/lib/pipeline";
import type { Forecast, Market } from "@/lib/types";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface PredictionSuccessModalProps {
  /** Whether the modal is visible */
  open: boolean;
  /** Call to dismiss */
  onClose: () => void;
  /** The resolved forecast */
  forecast: Forecast;
  /** The associated market (optional — graceful fallback if absent) */
  market?: Market;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatPill({
  label,
  value,
  className,
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-0.5 rounded-xl border border-white/10 bg-white/5 px-5 py-3.5",
        className,
      )}
    >
      <span className="mono tnum text-xl font-semibold tracking-tight text-white">
        {value}
      </span>
      <span className="text-[9px] uppercase tracking-[0.14em] text-white/50">{label}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function PredictionSuccessModal({
  open,
  onClose,
  forecast,
  market,
}: PredictionSuccessModalProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  // ── Open / close the native <dialog> ──────────────────────────────────────
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open) {
      if (!dialog.open) dialog.showModal();
    } else {
      if (dialog.open) dialog.close();
    }
  }, [open]);

  // ── Escape key (native dialog already handles this, but we sync state) ────
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    function handleCancel(event: Event) {
      event.preventDefault(); // prevent default close so we control animation
      onClose();
    }
    dialog.addEventListener("cancel", handleCancel);
    return () => dialog.removeEventListener("cancel", handleCancel);
  }, [onClose]);

  // ── Backdrop click ─────────────────────────────────────────────────────────
  const handleBackdropClick = useCallback(
    (event: React.MouseEvent<HTMLDialogElement>) => {
      if (event.target === dialogRef.current) onClose();
    },
    [onClose],
  );

  // ── Derived display values ─────────────────────────────────────────────────
  const recMeta = RECOMMENDATION_META[forecast.recommendation];
  const resolvedYes = forecast.resolvedOutcome === "YES";
  const edge = forecast.decisionMetrics.estimatedNetEdgeYes;
  const platformLabel = market ? PLATFORM_LABELS[market.platform] : null;

  // "Correct" means: BUY_YES + resolved YES, BUY_NO + resolved NO
  const wasCorrect =
    (forecast.recommendation === "BUY_YES" && resolvedYes) ||
    (forecast.recommendation === "BUY_NO" && forecast.resolvedOutcome === "NO");

  if (!open) return null;

  return (
    <>
      {/* Full-viewport confetti layer (pointer-events: none) */}
      <Confetti active={open} />

      {/* Native <dialog> for correct stacking context & focus trap */}
      <dialog
        ref={dialogRef}
        onClick={handleBackdropClick}
        aria-labelledby="success-modal-title"
        aria-describedby="success-modal-desc"
        style={
          {
            "--tw-backdrop": "blur(2px)",
          } as React.CSSProperties
        }
        className={cn(
          // Reset dialog defaults
          "m-auto max-h-[90dvh] w-full max-w-lg overflow-y-auto rounded-2xl border-0 p-0",
          // Backdrop (Tailwind v4 — class-based ::backdrop)
          "backdrop:bg-zinc-950/80 backdrop:backdrop-blur-sm",
          // Animation
          "open:animate-in open:fade-in open:zoom-in-95 open:duration-300",
        )}
      >
        {/* ── Gradient header ─────────────────────────────────────────────── */}
        <div className="relative overflow-hidden rounded-t-2xl bg-gradient-to-br from-emerald-900 via-emerald-800 to-teal-900 px-6 pb-10 pt-8">
          {/* Decorative radial glow */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_60%_0%,rgba(52,211,153,0.35)_0%,transparent_70%)]"
          />

          {/* Dismiss button */}
          <button
            type="button"
            onClick={onClose}
            className="absolute right-4 top-4 flex size-8 items-center justify-center rounded-full text-white/50 transition-colors hover:bg-white/10 hover:text-white"
            aria-label="Close"
          >
            <X className="size-4" aria-hidden="true" />
          </button>

          {/* Trophy icon */}
          <div className="mb-4 flex justify-center">
            <div className="flex size-16 items-center justify-center rounded-2xl bg-white/10 shadow-[0_0_40px_rgba(52,211,153,0.4)] ring-1 ring-white/20">
              <Trophy className="size-8 text-emerald-300" aria-hidden="true" />
            </div>
          </div>

          {/* Headline */}
          <div className="text-center">
            <div className="mb-1 flex items-center justify-center gap-2">
              <Sparkles className="size-3.5 text-emerald-400" aria-hidden="true" />
              <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-emerald-400">
                Prediction resolved
              </span>
              <Sparkles className="size-3.5 text-emerald-400" aria-hidden="true" />
            </div>
            <h2
              id="success-modal-title"
              className="text-2xl font-semibold text-white"
            >
              {wasCorrect ? "Nailed it!" : "Resolved successfully"}
            </h2>
            <p
              id="success-modal-desc"
              className="mt-1.5 text-[13px] leading-relaxed text-white/60"
            >
              The agent&apos;s forecast resolved{" "}
              <span className="font-medium text-emerald-300">
                {forecast.resolvedOutcome}
              </span>{" "}
              — in line with the recommendation.
            </p>
          </div>

          {/* Stats row */}
          <div className="mt-6 grid grid-cols-3 gap-2.5">
            <StatPill
              label="P(YES)"
              value={formatProbability(forecast.probabilityYes)}
            />
            <StatPill
              label="Net edge"
              value={formatSignedPercent(edge)}
            />
            <StatPill
              label="Brier"
              value={
                forecast.brierContribution !== null
                  ? forecast.brierContribution.toFixed(3)
                  : "—"
              }
            />
          </div>
        </div>

        {/* ── Body ────────────────────────────────────────────────────────── */}
        <div className="rounded-b-2xl bg-zinc-900 px-6 py-5">
          {/* Market title */}
          <div className="mb-4">
            {platformLabel ? (
              <span className="mb-1.5 block text-[9px] uppercase tracking-[0.14em] text-zinc-500">
                {platformLabel}
              </span>
            ) : null}
            <p className="line-clamp-3 text-[14px] font-medium leading-snug text-zinc-100">
              {market?.title ?? forecast.targetDefinition}
            </p>
          </div>

          {/* Detail grid */}
          <div className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-xl border border-zinc-800 bg-zinc-950/60 px-4 py-4">
            <Detail
              icon={<Target className="size-3 text-zinc-500" />}
              label="Recommendation"
            >
              <Badge tone={recMeta.tone}>{recMeta.label}</Badge>
            </Detail>

            <Detail
              icon={<CheckCircle className="size-3 text-emerald-500" />}
              label="Resolved outcome"
            >
              <Badge
                tone={
                  resolvedYes
                    ? "positive"
                    : forecast.resolvedOutcome === "NO"
                      ? "negative"
                      : "neutral"
                }
              >
                {forecast.resolvedOutcome}
              </Badge>
            </Detail>

            <Detail
              icon={<TrendingUp className="size-3 text-zinc-500" />}
              label="Uncertainty interval"
            >
              <span className="mono tnum text-[11px] text-zinc-300">
                {formatProbability(forecast.uncertaintyInterval[0])}–
                {formatProbability(forecast.uncertaintyInterval[1])}
              </span>
            </Detail>

            <Detail icon={null} label="Forecasted at">
              <span className="mono text-[10px] text-zinc-400">
                {formatDateTime(forecast.forecastedAt)}
              </span>
            </Detail>
          </div>

          {/* Model & calibration versions */}
          <div className="mt-3 flex flex-wrap gap-2">
            <span className="mono rounded border border-zinc-800 bg-zinc-950/60 px-1.5 py-0.5 text-[9px] text-zinc-500">
              model {forecast.modelVersion}
            </span>
            <span className="mono rounded border border-zinc-800 bg-zinc-950/60 px-1.5 py-0.5 text-[9px] text-zinc-500">
              calib {forecast.calibrationVersion}
            </span>
            <span className="mono rounded border border-zinc-800 bg-zinc-950/60 px-1.5 py-0.5 text-[9px] text-zinc-500">
              {forecast.id.slice(0, 8)}
            </span>
          </div>

          {/* Disclaimer */}
          <p className="mt-4 text-[9px] leading-relaxed text-zinc-600">
            Past resolution does not guarantee future performance. This display is
            informational only — no position was opened or closed by the system.
            All probabilities are point-in-time calibrated estimates.
          </p>

          {/* CTA row */}
          <div className="mt-5 flex items-center justify-end gap-2.5 border-t border-zinc-800 pt-4">
            <Button variant="ghost" size="sm" onClick={onClose}>
              Dismiss
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={onClose}
              className="bg-emerald-500 text-zinc-950 hover:bg-emerald-400 border-emerald-400/60"
            >
              <Trophy className="size-3.5" aria-hidden="true" />
              Awesome!
            </Button>
          </div>
        </div>
      </dialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// Detail helper
// ---------------------------------------------------------------------------

function Detail({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-1">
        {icon}
        <span className="text-[9px] uppercase tracking-[0.12em] text-zinc-600">
          {label}
        </span>
      </div>
      <div>{children}</div>
    </div>
  );
}
