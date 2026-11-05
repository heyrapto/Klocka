"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, CircleAlert, LoaderCircle, ScanLine, ShieldQuestion, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { formatDate, formatDateTime, formatProbability } from "@/lib/format";
import { PLATFORM_LABELS } from "@/lib/pipeline";
import type { Analysis, MatchCandidate } from "@/lib/types";

type SubmitState = "idle" | "submitting" | "resuming" | "error";

export function ConfirmationPanel({
  analysis,
  resultHref,
}: {
  analysis: Analysis;
  resultHref: string;
}) {
  const router = useRouter();
  const candidates = analysis.verification.candidates;
  const [selectedId, setSelectedId] = useState(candidates[0]?.candidateId ?? "");
  const [state, setState] = useState<SubmitState>("idle");
  const [error, setError] = useState<string | null>(null);
  const selected = candidates.find((candidate) => candidate.candidateId === selectedId);

  async function handleConfirm() {
    if (!selected) return;
    setState("submitting");
    setError(null);
    try {
      const response = await fetch(`/api/analyses/${analysis.id}/market-confirmation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidate_id: selected.candidateId }),
      });
      if (!response.ok) {
        throw new Error("Confirmation could not be recorded. Please retry.");
      }
      setState("resuming");
      window.setTimeout(() => {
        router.push(resultHref);
      }, 900);
    } catch (caught) {
      setState("error");
      setError(caught instanceof Error ? caught.message : "Confirmation failed.");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/8 p-4">
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg border border-amber-500/30 bg-amber-500/10">
          <TriangleAlert className="size-4 text-amber-300" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-[13px] font-semibold text-amber-200">Market identity requires confirmation</h2>
            <Badge tone="caution">Pipeline paused</Badge>
          </div>
          <p className="mt-1 max-w-2xl text-[11px] leading-relaxed text-amber-100/70">
            The screenshot expiry could not be read with sufficient confidence, and two active contracts
            share a near-identical title. No forecast will run until you select the correct contract.
          </p>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-xl border border-line bg-surface/80 p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-zinc-500">
              Extracted from screenshot
            </p>
            <Badge tone="caution">Confidence {formatProbability(analysis.extraction.overallConfidence)}</Badge>
          </div>
          <dl className="mt-3 space-y-3">
            <div>
              <dt className="text-[9px] uppercase tracking-[0.12em] text-zinc-600">Question</dt>
              <dd className="mt-0.5 text-[12px] leading-snug text-zinc-200">{analysis.extraction.marketTitle}</dd>
            </div>
            <div>
              <dt className="text-[9px] uppercase tracking-[0.12em] text-zinc-600">Displayed expiry</dt>
              <dd className="mono mt-0.5 rounded border border-amber-500/25 bg-amber-500/8 px-2 py-1 text-[11px] text-amber-200">
                {formatDateTime(analysis.extraction.displayedExpiry)}
                <span className="ml-2 text-[8px] uppercase tracking-wider text-amber-400/70">low confidence</span>
              </dd>
            </div>
            <div>
              <dt className="text-[9px] uppercase tracking-[0.12em] text-zinc-600">Resolution text</dt>
              <dd className="mt-0.5 text-[11px] leading-relaxed text-zinc-400">
                {analysis.extraction.displayedResolutionText}
              </dd>
            </div>
            <div>
              <dt className="text-[9px] uppercase tracking-[0.12em] text-zinc-600">Displayed outcomes</dt>
              <dd className="mt-1 flex gap-2">
                {analysis.extraction.outcomes.map((outcome) => (
                  <span
                    key={outcome.label}
                    className="mono rounded border border-line bg-zinc-950/60 px-2 py-0.5 text-[10px] text-zinc-400"
                  >
                    {outcome.label} {outcome.displayedPrice !== null ? `${(outcome.displayedPrice * 100).toFixed(1)}¢` : "—"}
                  </span>
                ))}
              </dd>
            </div>
          </dl>
        </div>

        <div className="rounded-xl border border-line bg-surface/80 p-4">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-zinc-500">
              Official candidate
            </p>
            {selected ? <Badge tone="info">{PLATFORM_LABELS[selected.platform]}</Badge> : null}
          </div>
          {selected ? (
            <dl className="mt-3 space-y-3">
              <div>
                <dt className="text-[9px] uppercase tracking-[0.12em] text-zinc-600">Question</dt>
                <dd className="mt-0.5 text-[12px] leading-snug text-zinc-200">{selected.title}</dd>
              </div>
              <div>
                <dt className="text-[9px] uppercase tracking-[0.12em] text-zinc-600">Official expiry</dt>
                <dd className="mono mt-0.5 rounded border border-line bg-zinc-950/60 px-2 py-1 text-[11px] text-zinc-300">
                  {formatDateTime(selected.resolvesAt)}
                </dd>
              </div>
              <div>
                <dt className="text-[9px] uppercase tracking-[0.12em] text-zinc-600">Resolution source</dt>
                <dd className="mt-0.5 text-[11px] leading-relaxed text-zinc-400">{selected.resolutionSource}</dd>
              </div>
              <div>
                <dt className="text-[9px] uppercase tracking-[0.12em] text-zinc-600">Match score</dt>
                <dd className="mt-1 flex items-center gap-2">
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-800">
                    <div
                      className="h-full rounded-full bg-sky-400"
                      style={{ width: `${selected.similarityScore * 100}%` }}
                    />
                  </div>
                  <span className="mono text-[10px] text-zinc-400">{formatProbability(selected.similarityScore)}</span>
                </dd>
              </div>
            </dl>
          ) : null}
        </div>
      </div>

      <div className="rounded-xl border border-line bg-surface/80">
        <div className="flex items-center justify-between gap-3 border-b border-line/70 px-4 py-3">
          <div>
            <p className="text-[12px] font-semibold text-zinc-200">Select the correct contract</p>
            <p className="mt-0.5 text-[10px] text-zinc-500">
              The server re-validates that the selected candidate is still active before resuming.
            </p>
          </div>
          <ShieldQuestion className="size-4 text-zinc-600" aria-hidden="true" />
        </div>
        <div className="space-y-2 p-3">
          {candidates.map((candidate) => (
            <CandidateOption
              key={candidate.candidateId}
              candidate={candidate}
              selected={candidate.candidateId === selectedId}
              onSelect={() => setSelectedId(candidate.candidateId)}
            />
          ))}
        </div>
      </div>

      {analysis.verification.discrepancies.length > 0 ? (
        <div className="rounded-lg border border-line bg-zinc-950/40 p-4">
          <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-zinc-500">Discrepancies</p>
          <ul className="mt-2 space-y-1.5">
            {analysis.verification.discrepancies.map((item) => (
              <li key={item} className="flex gap-2 text-[11px] leading-relaxed text-zinc-400">
                <CircleAlert className="mt-0.5 size-3 shrink-0 text-amber-400" aria-hidden="true" />
                {item}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {error ? (
        <p className="flex items-center gap-1.5 text-[11px] text-rose-300" role="alert">
          <CircleAlert className="size-3.5" aria-hidden="true" />
          {error}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface/80 px-4 py-3">
        <p className="max-w-lg text-[9px] leading-relaxed text-zinc-600">
          Demo mode: confirmation is recorded through the BFF, then the scripted pipeline resumes and
          opens the matching completed run. No live platform write occurs.
        </p>
        <div className="flex items-center gap-2">
          <Link href="/new" className={buttonVariants({ variant: "ghost", size: "sm" })}>
            <ScanLine className="size-3.5" aria-hidden="true" />
            Different screenshot
          </Link>
          <Button
            variant="primary"
            size="sm"
            onClick={handleConfirm}
            disabled={!selected || state === "submitting" || state === "resuming"}
          >
            {state === "submitting" ? (
              <>
                <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
                Confirming…
              </>
            ) : state === "resuming" ? (
              <>
                <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
                Resuming pipeline…
              </>
            ) : (
              <>
                <Check className="size-3.5" aria-hidden="true" />
                Confirm & run analysis
              </>
            )}
          </Button>
        </div>
      </div>

      {state === "resuming" ? (
        <div className="flex items-center gap-2 rounded-lg border border-sky-500/25 bg-sky-500/8 px-3 py-2 text-[10px] text-sky-300" role="status">
          <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
          Verification accepted. Re-running data collection, research, forecasting, and the decision gate…
          <ArrowRight className="ml-auto size-3" aria-hidden="true" />
        </div>
      ) : null}
    </div>
  );
}

function CandidateOption({
  candidate,
  selected,
  onSelect,
}: {
  candidate: MatchCandidate;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        "flex w-full items-start gap-3 rounded-lg border p-3 text-left transition-colors",
        selected
          ? "border-sky-500/40 bg-sky-500/8"
          : "border-line bg-zinc-950/40 hover:border-zinc-700 hover:bg-zinc-800/30",
      )}
    >
      <span
        className={cn(
          "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
          selected ? "border-sky-400 bg-sky-400" : "border-zinc-600",
        )}
        aria-hidden="true"
      >
        {selected ? <Check className="size-2.5 text-zinc-950" /> : null}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-[12px] font-medium text-zinc-200">{candidate.title}</span>
          <Badge tone={candidate.similarityScore >= 0.9 ? "info" : "caution"}>
            {formatProbability(candidate.similarityScore)} match
          </Badge>
        </span>
        <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[9px] text-zinc-500">
          <span>{PLATFORM_LABELS[candidate.platform]}</span>
          <span className="text-zinc-700">·</span>
          <span>Resolves {formatDate(candidate.resolvesAt)}</span>
          <span className="text-zinc-700">·</span>
          <span className="truncate">{candidate.resolutionSource}</span>
        </span>
      </span>
    </button>
  );
}
