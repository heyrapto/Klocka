"use client";

import { Check, CircleAlert, MessageSquare, Star } from "lucide-react";
import { useState } from "react";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

const feedbackSchema = z.object({
  reportedOutcome: z.enum(["YES", "NO", "VOID", "UNKNOWN"]),
  isExtractionCorrect: z.boolean(),
  explanationUsefulnessScore: z.number().int().min(1).max(5),
  comment: z.string().max(2000),
});

type FeedbackState = "idle" | "submitting" | "success" | "error";

export function FeedbackForm({ forecastId }: { forecastId: string }) {
  const [outcome, setOutcome] = useState<"YES" | "NO" | "VOID" | "UNKNOWN">("UNKNOWN");
  const [extractionCorrect, setExtractionCorrect] = useState(true);
  const [score, setScore] = useState(4);
  const [comment, setComment] = useState("");
  const [state, setState] = useState<FeedbackState>("idle");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const parsed = feedbackSchema.safeParse({
      reportedOutcome: outcome,
      isExtractionCorrect: extractionCorrect,
      explanationUsefulnessScore: score,
      comment,
    });
    if (!parsed.success) {
      setState("error");
      setError("Please provide a valid usefulness score and a comment under 2,000 characters.");
      return;
    }

    setState("submitting");
    try {
      const response = await fetch(`/api/forecasts/${forecastId}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(parsed.data),
      });
      if (!response.ok) {
        const body: unknown = await response.json().catch(() => null);
        const message =
          body && typeof body === "object" && "error" in body && typeof body.error === "string"
            ? body.error
            : "Feedback could not be recorded. Please retry.";
        throw new Error(message);
      }
      setState("success");
    } catch (caught) {
      setState("error");
      setError(caught instanceof Error ? caught.message : "Feedback could not be recorded.");
    }
  }

  if (state === "success") {
    return (
      <div className="flex items-start gap-3 rounded-lg border border-emerald-500/25 bg-emerald-500/8 px-4 py-3.5">
        <span className="mt-0.5 flex size-6 items-center justify-center rounded-full border border-emerald-500/30 bg-emerald-500/10">
          <Check className="size-3.5 text-emerald-400" aria-hidden="true" />
        </span>
        <div>
          <p className="text-[12px] font-medium text-emerald-300">Feedback recorded</p>
          <p className="mt-0.5 text-[10px] leading-relaxed text-zinc-400">
            Stored as diagnostic feedback with your user ID and the forecast snapshot. It does not
            alter the official resolution record.
          </p>
          <button
            type="button"
            onClick={() => setState("idle")}
            className="mt-2 text-[10px] text-sky-400 hover:text-sky-300"
          >
            Submit another response
          </button>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-[10px] font-medium uppercase tracking-[0.14em] text-zinc-500">
            Reported outcome
          </span>
          <select
            value={outcome}
            onChange={(event) => setOutcome(event.target.value as typeof outcome)}
            className="h-9 w-full rounded-lg border border-line bg-zinc-950/60 px-3 text-[12px] text-zinc-200 outline-none focus:border-sky-500/50 focus:ring-2 focus:ring-sky-500/20"
          >
            <option value="UNKNOWN">Not yet known</option>
            <option value="YES">Resolved YES</option>
            <option value="NO">Resolved NO</option>
            <option value="VOID">Voided / disputed</option>
          </select>
          <span className="mt-1 block text-[9px] text-zinc-600">
            User-reported outcomes are diagnostic only.
          </span>
        </label>

        <fieldset>
          <legend className="mb-1.5 block text-[10px] font-medium uppercase tracking-[0.14em] text-zinc-500">
            Was the extraction correct?
          </legend>
          <div className="flex gap-2">
            {[
              { label: "Correct", value: true },
              { label: "Incorrect", value: false },
            ].map((option) => (
              <button
                key={option.label}
                type="button"
                onClick={() => setExtractionCorrect(option.value)}
                aria-pressed={extractionCorrect === option.value}
                className={cn(
                  "h-9 flex-1 rounded-lg border text-[11px] transition-colors",
                  extractionCorrect === option.value
                    ? "border-sky-500/40 bg-sky-500/10 text-sky-300"
                    : "border-line bg-zinc-950/60 text-zinc-500 hover:text-zinc-300",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
          <span className="mt-1 block text-[9px] text-zinc-600">
            Extraction corrections are reviewed, never auto-applied.
          </span>
        </fieldset>
      </div>

      <fieldset>
        <legend className="mb-1.5 block text-[10px] font-medium uppercase tracking-[0.14em] text-zinc-500">
          Explanation usefulness
        </legend>
        <div className="flex items-center gap-1.5">
          {[1, 2, 3, 4, 5].map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setScore(value)}
              aria-label={`${value} out of 5`}
              aria-pressed={score === value}
              className={cn(
                "flex size-8 items-center justify-center rounded-md border transition-colors",
                value <= score
                  ? "border-amber-500/30 bg-amber-500/10 text-amber-300"
                  : "border-line bg-zinc-950/60 text-zinc-700 hover:text-zinc-500",
              )}
            >
              <Star className="size-3.5" fill={value <= score ? "currentColor" : "none"} aria-hidden="true" />
            </button>
          ))}
          <span className="mono ml-1 text-[10px] text-zinc-500">{score} / 5</span>
        </div>
      </fieldset>

      <label className="block">
        <span className="mb-1.5 block text-[10px] font-medium uppercase tracking-[0.14em] text-zinc-500">
          Comment
        </span>
        <textarea
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          rows={3}
          maxLength={2000}
          placeholder="What was clear, missing, or misleading? Do not include secrets or personal data."
          className="w-full resize-y rounded-lg border border-line bg-zinc-950/60 px-3 py-2 text-[12px] leading-relaxed text-zinc-200 outline-none transition-colors placeholder:text-zinc-600 focus:border-sky-500/50 focus:ring-2 focus:ring-sky-500/20"
        />
        <span className="mt-1 flex justify-between text-[9px] text-zinc-600">
          <span>Stored with the forecast ID and user ID.</span>
          <span className="mono">{comment.length} / 2000</span>
        </span>
      </label>

      {error ? (
        <p className="flex items-start gap-1.5 text-[10px] text-rose-300" role="alert">
          <CircleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
          {error}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line/60 pt-3">
        <p className="flex items-center gap-1.5 text-[9px] text-zinc-600">
          <MessageSquare className="size-3" aria-hidden="true" />
          Feedback never changes official outcomes or model weights.
        </p>
        <Button type="submit" variant="secondary" size="sm" disabled={state === "submitting"}>
          {state === "submitting" ? "Recording…" : "Submit feedback"}
        </Button>
      </div>
    </form>
  );
}
