"use client";

import Image from "next/image";
import Link from "next/link";
import {
  ArrowRight,
  Check,
  CircleAlert,
  CircleCheck,
  CloudUpload,
  FileImage,
  LoaderCircle,
  Lock,
  RefreshCw,
  ScanLine,
  ShieldCheck,
  Trash2,
  WifiOff,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Panel, PanelBody, PanelFooter, PanelHeader, SectionLabel } from "@/components/ui/panel";
import { RecommendationBadge } from "@/components/ui/recommendation-badge";
import { cn } from "@/lib/cn";
import { formatProbability, formatSignedPercent } from "@/lib/format";
import { PIPELINE_STAGES, getStageLabel } from "@/lib/pipeline";
import { DEMO_SCENARIOS, type DemoScenario } from "@/lib/scenarios";
import type { AnalysisStatus, Recommendation } from "@/lib/types";

const MAX_BYTES = 10 * 1024 * 1024;
const ALLOWED_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
const SAMPLE_SRC = "/sample/polymarket-btc-100k.svg";

type UploadState =
  | "idle"
  | "uploading"
  | "streaming"
  | "completed"
  | "confirmation"
  | "failed"
  | "error";

type StageStatus = "pending" | "in_progress" | "completed" | "failed";

interface StageRow {
  stage: AnalysisStatus;
  status: StageStatus;
  message: string;
}

interface Completion {
  resultAnalysisId: string;
  recommendation: Recommendation | null;
  probabilityYes: number | null;
  netEdge: number | null;
  errorCode?: string;
  message?: string;
}

const FALLBACK_STAGES: Record<DemoScenario, AnalysisStatus[]> = {
  no_trade: [
    "IMAGE_VALIDATION",
    "SCREENSHOT_EXTRACTION",
    "MARKET_IDENTIFICATION",
    "MARKET_VERIFICATION",
    "DATA_COLLECTION",
    "RESEARCH",
    "FEATURE_BUILDING",
    "FORECASTING",
    "DECISION_EVALUATION",
  ],
  buy_yes: [
    "IMAGE_VALIDATION",
    "SCREENSHOT_EXTRACTION",
    "MARKET_IDENTIFICATION",
    "MARKET_VERIFICATION",
    "DATA_COLLECTION",
    "RESEARCH",
    "FEATURE_BUILDING",
    "FORECASTING",
    "DECISION_EVALUATION",
  ],
  buy_no: [
    "IMAGE_VALIDATION",
    "SCREENSHOT_EXTRACTION",
    "MARKET_IDENTIFICATION",
    "MARKET_VERIFICATION",
    "DATA_COLLECTION",
    "RESEARCH",
    "FEATURE_BUILDING",
    "FORECASTING",
    "DECISION_EVALUATION",
  ],
  confirm: ["IMAGE_VALIDATION", "SCREENSHOT_EXTRACTION", "MARKET_IDENTIFICATION"],
  stale: [
    "IMAGE_VALIDATION",
    "SCREENSHOT_EXTRACTION",
    "MARKET_IDENTIFICATION",
    "MARKET_VERIFICATION",
    "DATA_COLLECTION",
  ],
};

function initialStages(): StageRow[] {
  return PIPELINE_STAGES.map((stage) => ({
    stage: stage.stage,
    status: "pending",
    message: stage.description,
  }));
}

export function NewAnalysisWorkflow() {
  const [file, setFile] = useState<File | null>(null);
  const [sampleSelected, setSampleSelected] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [scenario, setScenario] = useState<DemoScenario>("no_trade");
  const [marketContext, setMarketContext] = useState("");
  const [state, setState] = useState<UploadState>("idle");
  const [stages, setStages] = useState<StageRow[]>(initialStages);
  const [completion, setCompletion] = useState<Completion | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const objectUrlRef = useRef<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const fallbackTimerRef = useRef<ReturnType<typeof setInterval> | number | null>(null);
  const fallbackTimeoutRef = useRef<ReturnType<typeof setTimeout> | number | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const resultRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    return () => {
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
      eventSourceRef.current?.close();
      if (fallbackTimerRef.current) clearInterval(fallbackTimerRef.current);
      if (fallbackTimeoutRef.current) clearTimeout(fallbackTimeoutRef.current);
    };
  }, []);

  useEffect(() => {
    if (completion) {
      resultRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [completion]);

  const running = state === "uploading" || state === "streaming";
  const activeStage = stages.find((row) => row.status === "in_progress" || row.status === "failed");
  const completedCount = stages.filter((row) => row.status === "completed").length;
  const progress = Math.round((completedCount / stages.length) * 100);

  function acceptFile(next: File) {
    setFileError(null);
    if (!ALLOWED_TYPES.has(next.type)) {
      setFileError("Unsupported format. Upload a PNG, JPEG, or WebP screenshot.");
      return;
    }
    if (next.size > MAX_BYTES) {
      setFileError("Screenshot exceeds 10 MB. Crop or compress the image and retry.");
      return;
    }
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    const url = URL.createObjectURL(next);
    objectUrlRef.current = url;
    setFile(next);
    setSampleSelected(false);
    setPreviewUrl(url);
    resetRun();
  }

  function chooseSample() {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    objectUrlRef.current = null;
    setFile(null);
    setSampleSelected(true);
    setPreviewUrl(SAMPLE_SRC);
    setFileError(null);
    resetRun();
  }

  function clearFile() {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    objectUrlRef.current = null;
    setFile(null);
    setSampleSelected(false);
    setPreviewUrl(null);
    setFileError(null);
    resetRun();
  }

  function resetStages() {
    setStages(initialStages());
  }

  function resetRun() {
    if (running) return;
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    if (fallbackTimerRef.current) {
      clearInterval(fallbackTimerRef.current);
      fallbackTimerRef.current = null;
    }
    if (fallbackTimeoutRef.current) {
      clearTimeout(fallbackTimeoutRef.current);
      fallbackTimeoutRef.current = null;
    }
    setState("idle");
    setCompletion(null);
    setError(null);
    setReconnecting(false);
    reconnectAttemptsRef.current = 0;
    resetStages();
  }

  function markStage(stage: AnalysisStatus, message: string) {
    setStages((previous) => {
      const index = previous.findIndex((row) => row.stage === stage);
      if (index === -1) return previous;
      return previous.map((row, rowIndex) => {
        if (rowIndex < index) {
          return row.status === "failed" ? row : { ...row, status: "completed" };
        }
        if (rowIndex === index) {
          return { ...row, status: "in_progress", message };
        }
        return row;
      });
    });
  }

  function completeRun(
    nextState: "completed" | "confirmation" | "failed",
    nextCompletion: Completion,
  ) {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    if (fallbackTimerRef.current) {
      clearInterval(fallbackTimerRef.current);
      fallbackTimerRef.current = null;
    }
    if (fallbackTimeoutRef.current) {
      clearTimeout(fallbackTimeoutRef.current);
      fallbackTimeoutRef.current = null;
    }
    setReconnecting(false);
    setStages((previous) =>
      previous.map((row) => {
        if (nextState === "failed") {
          return row.status === "in_progress" ? { ...row, status: "failed" } : row;
        }
        if (nextState === "confirmation") {
          return row.status === "in_progress" ? { ...row, status: "completed" } : row;
        }
        return row.status === "failed" ? row : { ...row, status: "completed" };
      }),
    );
    setState(nextState);
    setCompletion(nextCompletion);
  }

  function startFallback(selectedScenario: DemoScenario) {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    if (fallbackTimeoutRef.current) {
      clearTimeout(fallbackTimeoutRef.current);
      fallbackTimeoutRef.current = null;
    }
    const meta = DEMO_SCENARIOS[selectedScenario];
    const script = FALLBACK_STAGES[selectedScenario];
    let index = 0;
    setReconnecting(false);
    setState("streaming");
    setStages(initialStages());

    if (fallbackTimerRef.current) clearInterval(fallbackTimerRef.current);
    fallbackTimerRef.current = setInterval(() => {
      if (index < script.length) {
        const stage = script[index];
        const stageMeta = PIPELINE_STAGES.find((item) => item.stage === stage);
        markStage(stage, stageMeta?.description ?? "Processing…");
        index += 1;
        return;
      }
      if (fallbackTimerRef.current) clearInterval(fallbackTimerRef.current);
      fallbackTimerRef.current = null;
      if (selectedScenario === "confirm") {
        completeRun("confirmation", {
          resultAnalysisId: meta.analysisId,
          recommendation: null,
          probabilityYes: null,
          netEdge: null,
          message: "Two contracts share a near-identical title. Confirm the expiry to continue.",
        });
      } else if (selectedScenario === "stale") {
        completeRun("failed", {
          resultAnalysisId: meta.analysisId,
          recommendation: null,
          probabilityYes: null,
          netEdge: null,
          errorCode: "DATA_STALE",
          message:
            "Critical exchange feed exceeded the 3.0-second freshness threshold. No forecast was emitted.",
        });
      } else {
        completeRun("completed", {
          resultAnalysisId: meta.analysisId,
          recommendation: meta.recommendation,
          probabilityYes: meta.probabilityYes,
          netEdge: meta.netEdge,
        });
      }
    }, 620);
  }

  function startStream(eventsUrl: string, selectedScenario: DemoScenario) {
    const source = new EventSource(eventsUrl);
    eventSourceRef.current = source;
    const meta = DEMO_SCENARIOS[selectedScenario];
    let sawEvent = false;
    const clearFallbackTimeout = () => {
      if (fallbackTimeoutRef.current) {
        clearTimeout(fallbackTimeoutRef.current);
        fallbackTimeoutRef.current = null;
      }
    };
    fallbackTimeoutRef.current = window.setTimeout(() => {
      if (!sawEvent) startFallback(selectedScenario);
    }, 3000);

    const onOpen = () => {
      sawEvent = true;
      clearFallbackTimeout();
      setReconnecting(false);
      reconnectAttemptsRef.current = 0;
    };

    const onError = () => {
      setReconnecting(true);
      reconnectAttemptsRef.current += 1;
      if (reconnectAttemptsRef.current > 3) {
        clearFallbackTimeout();
        startFallback(selectedScenario);
      }
    };

    const onStage = (event: Event) => {
      sawEvent = true;
      clearFallbackTimeout();
      setReconnecting(false);
      try {
        const data = JSON.parse((event as MessageEvent<string>).data) as {
          stage: AnalysisStatus;
          message: string;
        };
        markStage(data.stage, data.message);
      } catch {
        setError("Received a malformed pipeline event. Falling back to the local demo timeline.");
        startFallback(selectedScenario);
      }
    };

    const onCompleted = (event: Event) => {
      try {
        const data = JSON.parse((event as MessageEvent<string>).data) as {
          result_analysis_id: string;
          recommendation: Recommendation | null;
          probability_yes: number | null;
          net_edge: number | null;
        };
        completeRun("completed", {
          resultAnalysisId: data.result_analysis_id,
          recommendation: data.recommendation,
          probabilityYes: data.probability_yes,
          netEdge: data.net_edge,
        });
      } catch {
        completeRun("completed", {
          resultAnalysisId: meta.analysisId,
          recommendation: meta.recommendation,
          probabilityYes: meta.probabilityYes,
          netEdge: meta.netEdge,
        });
      }
    };

    const onFailed = (event: Event) => {
      try {
        const data = JSON.parse((event as MessageEvent<string>).data) as {
          result_analysis_id: string;
          error_code: string;
          message: string;
        };
        completeRun("failed", {
          resultAnalysisId: data.result_analysis_id,
          recommendation: null,
          probabilityYes: null,
          netEdge: null,
          errorCode: data.error_code,
          message: data.message,
        });
      } catch {
        completeRun("failed", {
          resultAnalysisId: meta.analysisId,
          recommendation: null,
          probabilityYes: null,
          netEdge: null,
          errorCode: "DATA_STALE",
        });
      }
    };

    const onConfirmation = (event: Event) => {
      try {
        const data = JSON.parse((event as MessageEvent<string>).data) as {
          result_analysis_id: string;
        };
        completeRun("confirmation", {
          resultAnalysisId: data.result_analysis_id,
          recommendation: null,
          probabilityYes: null,
          netEdge: null,
          message: "Two contracts share a near-identical title. Confirm the expiry to continue.",
        });
      } catch {
        completeRun("confirmation", {
          resultAnalysisId: meta.analysisId,
          recommendation: null,
          probabilityYes: null,
          netEdge: null,
        });
      }
    };

    source.addEventListener("open", onOpen);
    source.addEventListener("error", onError);
    source.addEventListener("analysis.stage", onStage);
    source.addEventListener("analysis.completed", onCompleted);
    source.addEventListener("analysis.failed", onFailed);
    source.addEventListener("analysis.needs_confirmation", onConfirmation);
  }

  async function handleRun() {
    if (!file && !sampleSelected) {
      setFileError("Choose a screenshot or use the synthetic sample before running.");
      return;
    }
    setState("uploading");
    setError(null);
    setCompletion(null);
    setReconnecting(false);
    reconnectAttemptsRef.current = 0;
    resetStages();

    const form = new FormData();
    if (file) form.append("file", file);
    else form.append("sample", "polymarket-btc-100k");
    form.append("scenario", scenario);
    if (marketContext.trim()) form.append("market_context", marketContext.trim());

    try {
      const response = await fetch("/api/analyses", { method: "POST", body: form });
      const body: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        const message =
          body && typeof body === "object" && "error" in body && typeof body.error === "string"
            ? body.error
            : "The analysis could not be started.";
        throw new Error(message);
      }
      const eventsUrl =
        body && typeof body === "object" && "events_url" in body && typeof body.events_url === "string"
          ? body.events_url
          : null;
      setState("streaming");
      if (eventsUrl) startStream(eventsUrl, scenario);
      else startFallback(scenario);
    } catch (caught) {
      setState("error");
      setError(caught instanceof Error ? caught.message : "The analysis could not be started.");
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-12">
      <div className="space-y-4 xl:col-span-7">
        <Panel className="overflow-hidden">
          <PanelHeader
            eyebrow="Step 01"
            title="Screenshot"
            description="Upload a Polymarket or Bayse market page. Prices are read for identification only and are never trusted as executable quotes."
            actions={<Badge tone="neutral">PNG · JPEG · WebP</Badge>}
            icon={<FileImage className="size-3.5" />}
          />
          <PanelBody>
            {previewUrl ? (
              <div className="space-y-3">
                <div className="relative overflow-hidden rounded-lg border border-line bg-zinc-950">
                  <Image
                    src={previewUrl}
                    alt="Selected market screenshot preview"
                    width={1200}
                    height={760}
                    unoptimized={previewUrl.startsWith("blob:")}
                    className="h-auto w-full"
                    sizes="(max-width: 1280px) 100vw, 720px"
                  />
                  <div className="absolute left-3 top-3 flex gap-2">
                    <span className="mono rounded border border-sky-400/30 bg-zinc-950/85 px-2 py-0.5 text-[9px] uppercase tracking-[0.12em] text-sky-300 backdrop-blur">
                      {sampleSelected ? "Synthetic sample" : "Uploaded screenshot"}
                    </span>
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-[11px] text-zinc-300">
                      {file ? file.name : "polymarket-btc-100k.svg"}
                    </p>
                    <p className="mono text-[9px] text-zinc-600">
                      {file ? `${(file.size / 1024).toFixed(0)} KB · ${file.type}` : "1,200 × 760 · synthetic fixture"}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={() => inputRef.current?.click()} disabled={running}>
                      Replace
                    </Button>
                    <Button variant="ghost" size="sm" onClick={clearFile} disabled={running}>
                      <Trash2 className="size-3.5" aria-hidden="true" />
                      Remove
                    </Button>
                  </div>
                </div>
              </div>
            ) : (
              <div
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  const dropped = event.dataTransfer.files[0];
                  if (dropped) acceptFile(dropped);
                }}
                className={cn(
                  "grid-texture flex flex-col items-center justify-center rounded-xl border border-dashed px-6 py-12 text-center transition-colors",
                  dragging
                    ? "border-sky-400/70 bg-sky-400/5"
                    : "border-zinc-700 bg-zinc-950/30 hover:border-zinc-600",
                )}
              >
                <span className="mb-3 flex size-11 items-center justify-center rounded-lg border border-line bg-surface text-zinc-400">
                  <CloudUpload className="size-5" aria-hidden="true" />
                </span>
                <p className="text-[13px] font-medium text-zinc-200">
                  {dragging ? "Drop the screenshot to analyze" : "Drag a market screenshot here"}
                </p>
                <p className="mt-1 max-w-sm text-[11px] leading-relaxed text-zinc-500">
                  Or browse a local file. Maximum 10 MB; EXIF metadata is stripped before extraction.
                </p>
                <div className="mt-4 flex flex-wrap items-center justify-center gap-2">
                  <Button variant="secondary" size="sm" onClick={() => inputRef.current?.click()}>
                    Browse files
                  </Button>
                  <Button variant="outline" size="sm" onClick={chooseSample}>
                    Use synthetic sample
                  </Button>
                </div>
                <p className="mt-3 flex items-center gap-1.5 text-[9px] text-zinc-600">
                  <Lock className="size-3" aria-hidden="true" />
                  Private object storage · no public URLs
                </p>
              </div>
            )}
            <input
              ref={inputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="sr-only"
              onChange={(event) => {
                const selected = event.target.files?.[0];
                if (selected) acceptFile(selected);
                event.target.value = "";
              }}
              aria-label="Upload market screenshot"
            />
            {fileError ? (
              <p className="mt-3 flex items-start gap-1.5 text-[10px] text-rose-300" role="alert">
                <CircleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
                {fileError}
              </p>
            ) : null}
          </PanelBody>
        </Panel>

        <Panel className="overflow-hidden">
          <PanelHeader
            eyebrow="Step 02"
            title="Demo scenario"
            description="Scenario selection only changes the scripted demo pipeline. With a backend configured, the screenshot is sent to the real extraction and forecasting pipeline."
            actions={<Badge tone="caution">Demo only</Badge>}
            icon={<ScanLine className="size-3.5" />}
          />
          <PanelBody>
            <div className="grid gap-2 sm:grid-cols-2">
              {(Object.values(DEMO_SCENARIOS)).map((option) => {
                const selected = scenario === option.key;
                return (
                  <button
                    key={option.key}
                    type="button"
                    onClick={() => {
                      setScenario(option.key);
                      if (!running) resetRun();
                    }}
                    disabled={running}
                    aria-pressed={selected}
                    className={cn(
                      "rounded-lg border p-3 text-left transition-colors disabled:opacity-60",
                      selected
                        ? "border-sky-500/40 bg-sky-500/8"
                        : "border-line bg-zinc-950/40 hover:border-zinc-700 hover:bg-zinc-800/30",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="mono text-[9px] uppercase tracking-[0.12em] text-zinc-500">
                        {option.key.replaceAll("_", " ")}
                      </span>
                      {option.recommendation ? (
                        <RecommendationBadge recommendation={option.recommendation} />
                      ) : (
                        <Badge tone={option.tone === "caution" ? "caution" : "negative"}>{option.label}</Badge>
                      )}
                    </div>
                    <p className="mt-2 text-[11px] font-medium text-zinc-200">{option.title}</p>
                    <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">{option.detail}</p>
                  </button>
                );
              })}
            </div>
          </PanelBody>
        </Panel>

        <Panel className="overflow-hidden">
          <PanelHeader
            eyebrow="Step 03"
            title="Optional context"
            description="Add a market slug or URL if the screenshot is cropped. Context is advisory; verification still uses official APIs."
            icon={<ScanLine className="size-3.5" />}
          />
          <PanelBody>
            <label className="block">
              <span className="sr-only">Market slug or URL</span>
              <input
                type="text"
                value={marketContext}
                onChange={(event) => setMarketContext(event.target.value)}
                maxLength={500}
                placeholder="e.g. polymarket.com/event/bitcoin-above-100k-nov-15"
                className="h-9 w-full rounded-lg border border-line bg-zinc-950/60 px-3 text-[12px] text-zinc-200 outline-none transition-colors placeholder:text-zinc-600 focus:border-sky-500/50 focus:ring-2 focus:ring-sky-500/20"
                disabled={running}
              />
              <span className="mt-1 block text-[9px] text-zinc-600">
                Never used as the sole source of truth. Ambiguous matches still pause for confirmation.
              </span>
            </label>
          </PanelBody>
        </Panel>

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-surface/70 px-4 py-3">
          <p className="max-w-sm text-[9px] leading-relaxed text-zinc-600">
            The platform does not execute trades or hold funds. A NO_TRADE result is a successful
            analytical outcome, not a failure.
          </p>
          <div className="flex items-center gap-2">
            {completion ? (
              <Button variant="outline" size="sm" onClick={resetRun}>
                <RefreshCw className="size-3.5" aria-hidden="true" />
                Run another
              </Button>
            ) : null}
            <Button
              variant="primary"
              size="md"
              onClick={handleRun}
              disabled={running || (!file && !sampleSelected)}
            >
              {running ? (
                <>
                  <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
                  {state === "uploading" ? "Uploading…" : "Pipeline running…"}
                </>
              ) : (
                <>
                  <ScanLine className="size-4" aria-hidden="true" />
                  Run analysis
                </>
              )}
            </Button>
          </div>
        </div>
      </div>

      <div className="space-y-4 xl:col-span-5">
        <Panel className="overflow-hidden">
          <PanelHeader
            eyebrow="Live pipeline"
            title="Analysis progress"
            description="Stage transitions stream over SSE. Polling remains authoritative if the stream drops."
            actions={
              <span className="mono text-[10px] text-zinc-500">
                {completedCount} / {stages.length}
              </span>
            }
            icon={<ScanLine className="size-3.5" />}
          />
          <div className="px-5 pt-4">
            <div className="h-1.5 overflow-hidden rounded-full bg-zinc-800">
              <div
                className="h-full rounded-full bg-sky-400 transition-[width] duration-500"
                style={{ width: `${progress}%` }}
              />
            </div>
            <div className="mt-1.5 flex items-center justify-between text-[9px] text-zinc-600">
              <span>{state === "idle" ? "Awaiting screenshot" : activeStage ? getStageLabel(activeStage.stage) : "Starting"}</span>
              <span className="mono">{progress}%</span>
            </div>
          </div>

          {reconnecting ? (
            <div className="mx-5 mt-3 flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/8 px-3 py-2 text-[10px] text-amber-300" role="status">
              <WifiOff className="size-3.5 shrink-0" aria-hidden="true" />
              Stream interrupted. Reconnecting with replay…
            </div>
          ) : null}

          <div className="mt-3 max-h-[430px] overflow-y-auto px-5 pb-4">
            <ol className="space-y-0.5">
              {stages.map((row, index) => {
                const active = row.status === "in_progress";
                const failed = row.status === "failed";
                const completed = row.status === "completed";
                const Icon = failed ? X : active ? LoaderCircle : completed ? Check : ScanLine;
                return (
                  <li
                    key={row.stage}
                    className={cn(
                      "flex items-start gap-2.5 rounded-md px-2 py-1.5",
                      active && "bg-sky-500/5",
                      failed && "bg-rose-500/5",
                    )}
                  >
                    <span
                      className={cn(
                        "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border",
                        failed
                          ? "border-rose-500/40 bg-rose-500/10 text-rose-300"
                          : active
                            ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
                            : completed
                              ? "border-sky-500/30 bg-sky-500/10 text-sky-300"
                              : "border-line bg-zinc-900 text-zinc-700",
                      )}
                    >
                      <Icon className={cn("size-2.5", active && "animate-spin")} aria-hidden="true" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span
                          className={cn(
                            "text-[10px] font-medium",
                            failed ? "text-rose-300" : completed || active ? "text-zinc-300" : "text-zinc-600",
                          )}
                        >
                          {getStageLabel(row.stage)}
                        </span>
                        <span className="mono text-[8px] text-zinc-700">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                      </div>
                      <p
                        className={cn(
                          "mt-0.5 text-[9px] leading-relaxed",
                          active || failed ? "text-zinc-500" : "text-zinc-700",
                        )}
                      >
                        {row.message}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>

          <div aria-live="polite" role="status" className="sr-only">
            {activeStage ? `${getStageLabel(activeStage.stage)}: ${activeStage.message}` : null}
          </div>

          {state === "idle" ? (
            <PanelFooter>
              <span className="text-[10px] text-zinc-600">
                Select a screenshot and a demo scenario, then run.
              </span>
            </PanelFooter>
          ) : null}
        </Panel>

        <div ref={resultRef}>
          {completion ? <CompletionPanel completion={completion} state={state} onReset={resetRun} /> : null}
          {state === "error" ? (
            <div className="rounded-xl border border-rose-500/25 bg-rose-500/8 p-4" role="alert">
              <div className="flex items-start gap-2.5">
                <CircleAlert className="mt-0.5 size-4 shrink-0 text-rose-300" aria-hidden="true" />
                <div>
                  <p className="text-[12px] font-medium text-rose-200">Analysis could not start</p>
                  <p className="mt-1 text-[10px] leading-relaxed text-rose-100/70">{error}</p>
                  <Button variant="secondary" size="sm" className="mt-3" onClick={handleRun}>
                    <RefreshCw className="size-3.5" aria-hidden="true" />
                    Retry
                  </Button>
                </div>
              </div>
            </div>
          ) : null}
        </div>

        <Panel className="overflow-hidden">
          <PanelHeader
            eyebrow="Guardrails"
            title="What the pipeline enforces"
            description="Every run is bounded by the same non-negotiable controls."
            icon={<ShieldCheck className="size-3.5" />}
          />
          <ul className="divide-y divide-line/50">
            {[
              { title: "Verified contracts only", detail: "Critical resolution fields must match the official platform API." },
              { title: "Point-in-time evidence", detail: "Research published after the forecast cutoff is never retrieved." },
              { title: "Cost-aware abstention", detail: "Fees, spread, and slippage are deducted before any edge is reported." },
              { title: "No execution", detail: "The platform never signs orders, holds keys, or moves funds." },
            ].map((item) => (
              <li key={item.title} className="flex items-start gap-2.5 px-5 py-3">
                <CircleCheck className="mt-0.5 size-3.5 shrink-0 text-sky-400/80" aria-hidden="true" />
                <div>
                  <p className="text-[11px] text-zinc-300">{item.title}</p>
                  <p className="mt-0.5 text-[9px] leading-relaxed text-zinc-600">{item.detail}</p>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}

function CompletionPanel({
  completion,
  state,
  onReset,
}: {
  completion: Completion;
  state: UploadState;
  onReset: () => void;
}) {
  if (state === "completed") {
    return (
      <Panel className="overflow-hidden">
        <PanelHeader
          eyebrow="Result"
          title="Analysis complete"
          description="The immutable forecast snapshot is ready for inspection."
          actions={<Badge tone="positive">SSE closed</Badge>}
          icon={<CircleCheck className="size-3.5" />}
        />
        <PanelBody className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            {completion.recommendation ? (
              <RecommendationBadge recommendation={completion.recommendation} size="md" />
            ) : null}
            <div className="flex items-baseline gap-3">
              <div>
                <SectionLabel>P(YES)</SectionLabel>
                <p className="mono text-2xl font-semibold text-zinc-50">
                  {completion.probabilityYes !== null ? formatProbability(completion.probabilityYes) : "—"}
                </p>
              </div>
              <div>
                <SectionLabel>Net edge</SectionLabel>
                <p
                  className={cn(
                    "mono text-2xl font-semibold",
                    completion.netEdge !== null && completion.netEdge > 0 ? "text-emerald-400" : "text-zinc-300",
                  )}
                >
                  {completion.netEdge !== null ? formatSignedPercent(completion.netEdge) : "—"}
                </p>
              </div>
            </div>
          </div>
          <p className="text-[10px] leading-relaxed text-zinc-500">
            {completion.recommendation === "NO_TRADE"
              ? "The calibrated edge did not clear the risk-adjusted threshold. Abstention is the correct, successful outcome."
              : "The edge clears the policy threshold. Sizing remains a decision-support output, not an order."}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={`/analyses/${completion.resultAnalysisId}`}
              className={buttonVariants({ variant: "primary", size: "sm" })}
            >
              Open result cockpit
              <ArrowRight className="size-3.5" aria-hidden="true" />
            </Link>
            <Button variant="ghost" size="sm" onClick={onReset}>
              Run another
            </Button>
          </div>
        </PanelBody>
      </Panel>
    );
  }

  if (state === "confirmation") {
    return (
      <div className="rounded-xl border border-amber-500/30 bg-amber-500/8 p-4">
        <div className="flex items-start gap-2.5">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-amber-300" aria-hidden="true" />
          <div>
            <p className="text-[12px] font-medium text-amber-200">Contract confirmation required</p>
            <p className="mt-1 text-[10px] leading-relaxed text-amber-100/70">
              {completion.message ??
                "Two plausible contracts were found. No forecast will run until the correct expiry is confirmed."}
            </p>
            <Link
              href={`/analyses/${completion.resultAnalysisId}`}
              className={buttonVariants({ variant: "secondary", size: "sm", className: "mt-3" })}
            >
              Review candidates
              <ArrowRight className="size-3.5" aria-hidden="true" />
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-rose-500/25 bg-rose-500/8 p-4">
      <div className="flex items-start gap-2.5">
        <CircleAlert className="mt-0.5 size-4 shrink-0 text-rose-300" aria-hidden="true" />
        <div>
          <p className="text-[12px] font-medium text-rose-200">
            Analysis halted · {completion.errorCode ?? "FAILED"}
          </p>
          <p className="mt-1 text-[10px] leading-relaxed text-rose-100/70">
            {completion.message ?? "The run failed closed. No probability or recommendation was emitted."}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link
              href={`/analyses/${completion.resultAnalysisId}`}
              className={buttonVariants({ variant: "secondary", size: "sm" })}
            >
              Open audit trail
              <ArrowRight className="size-3.5" aria-hidden="true" />
            </Link>
            <Button variant="ghost" size="sm" onClick={onReset}>
              Try another
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
