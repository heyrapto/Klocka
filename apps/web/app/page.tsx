"use client";

import React, { useEffect, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  Clock,
  ExternalLink,
  Loader2,
  RefreshCw,
  Scale,
  ShieldCheck,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Upload,
  XCircle,
  Zap,
} from "lucide-react";
import { CalibrationCard } from "@/components/analyses/calibration-card";
import { IntervalTimer } from "@/components/analyses/interval-timer";
import { usePredictionSuccess } from "@/components/ui/prediction-success-context";
import type { CelebratePayload } from "@/components/ui/prediction-success-context";
import type { Forecast } from "@/lib/types";

interface DecisionMetrics {
  executable_ask_yes?: number | null;
  executable_ask_no?: number | null;
  effective_cost_yes?: number | null;
  effective_cost_no?: number | null;
  estimated_net_edge_yes?: number | null;
  estimated_net_edge_no?: number | null;
  minimum_required_edge?: number;
  spread?: number;
  slippage_bps?: number;
  quarter_kelly_fraction?: number;
}

interface EvidenceItem {
  evidence_id: string;
  evidence_type: string;
  stance: string;
  relevance_score: number;
  snippet: string;
  source_citation: string;
  source_url?: string | null;
}

interface MarketSummary {
  id: string;
  platform: string;
  platform_market_id: string;
  title: string;
  url?: string | null;
  category: string;
  underlying_symbol?: string | null;
  resolves_at?: string | null;
  resolution_source?: string | null;
}

interface TimingProvenance {
  initial_seconds_remaining?: number;
  exact_resolves_at?: string;
  decision_seconds_remaining?: number;
  pipeline_latency_seconds?: number;
  interval_phase?: string;
}

interface ForecastDetail {
  id: string;
  probability_yes: number;
  probability_no: number;
  uncertainty_interval: number[];
  recommendation: "BUY_YES" | "BUY_NO" | "NO_TRADE";
  decision_metrics: DecisionMetrics;
  explanation: {
    reasoning?: string;
    key_drivers?: string[];
    decision_reason?: string;
  };
  market: MarketSummary;
  evidence?: EvidenceItem[];
  timing_provenance?: TimingProvenance;
}

interface SpecialistContext {
  category?: string;
  crypto_research?: {
    live_spot_reference?: { price?: number; pair?: string; source?: string; note?: string };
    interval_study?: {
      recent_interval_trend?: string;
      momentum_oscillator_rsi_15m?: string;
      order_book_depth_ratio_bids_vs_asks?: string;
    };
    sentiment_and_social_signals?: {
      social_media_prediction_consensus?: string;
    };
  };
  prior_learnings?: string[];
  [key: string]: unknown;
}

interface AnalysisDetail {
  analysis_id: string;
  status: string;
  created_at: string;
  completed_at?: string | null;
  events?: Array<{ id: number }>;
  extraction?: {
    platform?: string;
    market_title?: string;
    asset_or_entity?: string;
    overall_confidence?: number;
    extraction_warnings?: string[];
    outcomes?: Array<{ label?: string; displayed_price?: number }> | null;
    [key: string]: unknown;
  } | null;
  verification?: {
    status?: string;
    score?: number;
    discrepancies?: string[];
    candidates?: Array<{
      candidate_id: string;
      platform: string;
      title: string;
      similarity_score: number;
    }>;
  } | null;
  forecast?: (ForecastDetail & {
    specialist_context?: SpecialistContext;
  }) | null;
  error?: {
    code: string;
    message: string;
  } | null;
}

interface FeedbackResult {
  actual_outcome: string;
  was_correct: boolean;
  prediction_made: string;
  postmortem_analysis: string;
  key_learnings: string[];
  visual_observations?: string | null;
  discrepancy_classification?: string | null;
}

const STAGE_INFO: Record<string, { label: string; desc: string; step: number }> = {
  IMAGE_VALIDATION: {
    label: "Image Validation",
    desc: "Checking dimensions, file integrity, and stripping metadata...",
    step: 1,
  },
  SCREENSHOT_EXTRACTION: {
    label: "Vision Extraction",
    desc: "Reading contract terms, prices, and visible market state...",
    step: 2,
  },
  MARKET_IDENTIFICATION: {
    label: "Market Discovery",
    desc: "Finding the matching live market contract...",
    step: 3,
  },
  MARKET_VERIFICATION: {
    label: "Contract Verification",
    desc: "Cross-verifying exact title, expiration date, and token identities...",
    step: 4,
  },
  DATA_COLLECTION: {
    label: "Order Book Ingestion",
    desc: "Checking live prices, liquidity, and spread...",
    step: 5,
  },
  // The backend pipeline performs research and feature building as one stage
  // (there is no separate RESEARCH emit), so progress mirrors the emitter.
  FEATURE_BUILDING: {
    label: "Evidence Research & Feature Building",
    desc: "Ranking documentary evidence and building interval, liquidity, and momentum features...",
    step: 6,
  },
  FORECASTING: {
    label: "Calibrated Forecasting",
    desc: "Computing Bayesian outcome probability P(YES) and confidence bounds...",
    step: 7,
  },
  DECISION_EVALUATION: {
    label: "EV & Risk Gating",
    desc: "Deducting slippage and fees to finalize trade recommendation...",
    step: 8,
  },
};

// Keys are declared in true pipeline execution order (mirrors backend PIPELINE_STAGES).
const STAGE_ORDER = Object.keys(STAGE_INFO);

const TOTAL_PIPELINE_STAGES = STAGE_ORDER.length;

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * Adapts the page's snake_case ForecastDetail into the camelCase Forecast
 * shape consumed by PredictionSuccessModal. Only the fields rendered by the
 * modal are mapped; the rest fall back to safe sentinel values so the modal
 * always has something to display.
 */
function buildCelebratePayload(
  fd: NonNullable<AnalysisDetail["forecast"]>,
): CelebratePayload {
  const celebrateForecast: Forecast = {
    id: fd.id,
    analysisId: "",
    marketId: fd.market?.id ?? "",
    forecastedAt: new Date().toISOString(),
    targetDefinition: fd.market?.title ?? "",
    probabilityYes: Number(fd.probability_yes),
    probabilityNo: Number(fd.probability_no),
    uncertaintyInterval: [
      Number(fd.uncertainty_interval?.[0] ?? 0),
      Number(fd.uncertainty_interval?.[1] ?? 1),
    ],
    recommendation: fd.recommendation,
    decisionMetrics: {
      executableAskYes: fd.decision_metrics?.executable_ask_yes ?? 0,
      executableAskNo: fd.decision_metrics?.executable_ask_no ?? 0,
      effectiveCostYes: fd.decision_metrics?.effective_cost_yes ?? 0,
      effectiveCostNo: fd.decision_metrics?.effective_cost_no ?? 0,
      estimatedNetEdgeYes: fd.decision_metrics?.estimated_net_edge_yes ?? 0,
      estimatedNetEdgeNo: fd.decision_metrics?.estimated_net_edge_no ?? 0,
      minimumRequiredEdge: fd.decision_metrics?.minimum_required_edge ?? 0,
      spread: fd.decision_metrics?.spread ?? 0,
      slippageBps: fd.decision_metrics?.slippage_bps ?? 0,
      quarterKellyFraction: fd.decision_metrics?.quarter_kelly_fraction ?? 0,
      takerFeeRate: (fd.decision_metrics as Record<string, number> | null)?.taker_fee_rate ?? 0,
    },
    dataQuality: {
      marketVerified: false,
      criticalFeedsFresh: false,
      researchSufficient: false,
      missingFeatures: [],
      warnings: [],
      feeds: [],
    },
    modelVersion: "",
    calibrationVersion: "",
    promptVersion: "",
    decisionPolicyVersion: "",
    inputSnapshot: {
      snapshotId: "",
      marketDataSnapshotId: "",
      featureSetVersion: "",
      screenshotSha256: "",
      evidencePackId: "",
    },
    explanation: {
      summary: fd.explanation?.decision_reason ?? fd.explanation?.reasoning ?? "",
      supportingSignals: fd.explanation?.key_drivers ?? [],
      contradictingSignals: [],
      risks: [],
      invalidationConditions: [],
    },
    evidence: [],
    // The feedback API confirms was_correct === true, so the outcome matches
    // the recommendation direction.
    resolvedOutcome:
      fd.recommendation === "BUY_YES"
        ? "YES"
        : fd.recommendation === "BUY_NO"
          ? "NO"
          : "YES",
    brierContribution: null,
  };

  return { forecast: celebrateForecast };
}

export default function PredictionMarketHome() {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [hint, setHint] = useState("");
  const [loading, setLoading] = useState(false);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [currentStage, setCurrentStage] = useState<string | null>(null);
  const [latestProgressMessage, setLatestProgressMessage] = useState<string>("");
  const [analysisDetail, setAnalysisDetail] = useState<AnalysisDetail | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Post-settlement feedback state
  const [feedbackOutcome, setFeedbackOutcome] = useState<string>("UP");
  const [feedbackNotes, setFeedbackNotes] = useState("");
  const [submittingFeedback, setSubmittingFeedback] = useState(false);
  const [autoSettling, setAutoSettling] = useState(false);
  const [calibrationCounter, setCalibrationCounter] = useState(0);
  const [feedbackResult, setFeedbackResult] = useState<FeedbackResult | null>(null);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  const [settlementFile, setSettlementFile] = useState<File | null>(null);
  const [settlementPreview, setSettlementPreview] = useState<string | null>(null);
  const settlementInputRef = useRef<HTMLInputElement>(null);

  const { celebrate } = usePredictionSuccess();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  // Highest pipeline step confirmed by server events so the bar never regresses
  // (guards against late SSE replays or unknown stage names).
  const progressStepRef = useRef(0);

  // Restore active analysis session on page refresh so user never loses their forecast
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const params = new URLSearchParams(window.location.search);
      const urlId = params.get("id") || params.get("analysis_id");
      const activeId = urlId || localStorage.getItem("klocka_active_analysis_id");
      if (activeId && !analysisId) {
        setAnalysisId(activeId);
        fetchFinalResult(activeId);
      }
    } catch (err) {
      console.debug("Session restore skipped", err);
    }
  }, []);

  const advanceStage = (stage: string) => {
    const step = STAGE_INFO[stage]?.step ?? 0;
    if (step > 0 && step >= progressStepRef.current) {
      progressStepRef.current = step;
      setCurrentStage(stage);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const selected = e.target.files[0];
      setFile(selected);
      setPreviewUrl(URL.createObjectURL(selected));
      setErrorMsg(null);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const dropped = e.dataTransfer.files[0];
      setFile(dropped);
      setPreviewUrl(URL.createObjectURL(dropped));
      setErrorMsg(null);
    }
  };

  const resetAnalysis = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    if (typeof window !== "undefined") {
      try {
        localStorage.removeItem("klocka_active_analysis_id");
        window.history.replaceState(null, "", window.location.pathname);
      } catch {}
    }
    setFile(null);
    setPreviewUrl(null);
    setHint("");
    setSettlementFile(null);
    if (settlementPreview) URL.revokeObjectURL(settlementPreview);
    setSettlementPreview(null);
    setLoading(false);
    setAnalysisId(null);
    progressStepRef.current = 0;
    setCurrentStage(null);
    setLatestProgressMessage("");
    setAnalysisDetail(null);
    setErrorMsg(null);
    setFeedbackResult(null);
    setFeedbackError(null);
    setFeedbackNotes("");
  };

  const startAnalysis = async () => {
    if (!file && !hint) {
      setErrorMsg("Please upload a prediction market screenshot to begin.");
      return;
    }

    setLoading(true);
    setErrorMsg(null);
    setAnalysisDetail(null);
    progressStepRef.current = 1;
    setCurrentStage("IMAGE_VALIDATION");
    setLatestProgressMessage("Initializing analysis and validating screenshot format...");

    const formData = new FormData();
    if (file) {
      formData.append("file", file);
    }
    if (hint.trim()) {
      formData.append("hint", hint.trim());
    }

    try {
      const res = await fetch(`${API_BASE}/api/v1/analyses`, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.error?.message || errJson.detail || "Failed to start analysis");
      }

      const data = await res.json();
      setAnalysisId(data.analysis_id);

      // Persist active analysis ID so session survives page reloads and tab closures
      if (typeof window !== "undefined") {
        try {
          localStorage.setItem("klocka_active_analysis_id", data.analysis_id);
          window.history.replaceState(null, "", `?id=${data.analysis_id}`);
        } catch {}
      }

      const eventsUrl = data.events_url.startsWith("http")
        ? data.events_url
        : `${API_BASE}${data.events_url}`;
      setupEventSource(data.analysis_id, eventsUrl);
    } catch (err: unknown) {
      setLoading(false);
      setErrorMsg(err instanceof Error ? err.message : "Connection failed");
    }
  };

  const setupEventSource = (id: string, sseUrl: string, afterSequence = 0) => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const separator = sseUrl.includes("?") ? "&" : "?";
    const sse = new EventSource(`${sseUrl}${separator}after=${afterSequence}`);
    eventSourceRef.current = sse;

    sse.addEventListener("analysis.stage", (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        if (typeof payload.stage === "string") {
          advanceStage(payload.stage);
        }
        if (payload.message) {
          setLatestProgressMessage(payload.message);
        }
      } catch (err) {
        console.error("SSE parse error", err);
      }
    });

    sse.addEventListener("analysis.completed", async () => {
      advanceStage("DECISION_EVALUATION");
      sse.close();
      setLoading(false);
      await fetchFinalResult(id);
    });

    sse.addEventListener("analysis.needs_confirmation", async () => {
      sse.close();
      setLoading(false);
      await fetchFinalResult(id);
    });

    sse.addEventListener("analysis.failed", async (e: MessageEvent) => {
      sse.close();
      setLoading(false);
      try {
        const payload = JSON.parse(e.data);
        setErrorMsg(payload.message || "Analysis failed.");
      } catch {
        setErrorMsg("Analysis failed.");
      }
      await fetchFinalResult(id);
    });

    sse.onerror = () => {
      fetchFinalResult(id);
    };
  };

  const fetchFinalResult = async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/analyses/${id}`);
      if (res.ok) {
        const detail = await res.json();
        setAnalysisDetail(detail);
        if (detail.status === "FAILED" || detail.status === "MARKET_NOT_FOUND" || detail.status === "EXTRACTION_FAILED") {
          setErrorMsg(detail.error?.message || "Market could not be verified or extraction failed.");
        }
      }
    } catch (err) {
      console.error("Failed to fetch detail", err);
    }
  };

  const confirmCandidate = async (candidateId: string) => {
    if (!analysisId) return;
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/analyses/${analysisId}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidate_id: candidateId }),
      });
      if (res.ok) {
        const lastEventId = Math.max(
          0,
          ...(analysisDetail?.events ?? []).map((event) => event.id),
        );
        setupEventSource(
          analysisId,
          `${API_BASE}/api/v1/analyses/${analysisId}/events`,
          lastEventId,
        );
      }
    } catch (err) {
      console.error("Confirm failed", err);
      setLoading(false);
    }
  };

  const handleSettlementFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const selected = e.target.files[0];
      setSettlementFile(selected);
      setSettlementPreview(URL.createObjectURL(selected));
    }
  };

  const removeSettlementFile = () => {
    setSettlementFile(null);
    if (settlementPreview) URL.revokeObjectURL(settlementPreview);
    setSettlementPreview(null);
    if (settlementInputRef.current) settlementInputRef.current.value = "";
  };

  const submitFeedback = async () => {
    if (!analysisId) return;
    setSubmittingFeedback(true);
    setFeedbackError(null);
    try {
      let res: Response;
      if (settlementFile) {
        const formData = new FormData();
        formData.append("actual_outcome", feedbackOutcome);
        if (feedbackNotes.trim()) formData.append("notes", feedbackNotes.trim());
        formData.append("file", settlementFile);
        res = await fetch(`${API_BASE}/api/v1/analyses/${analysisId}/feedback`, {
          method: "POST",
          body: formData,
        });
      } else {
        res = await fetch(`${API_BASE}/api/v1/analyses/${analysisId}/feedback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            actual_outcome: feedbackOutcome,
            notes: feedbackNotes.trim() || null,
          }),
        });
      }
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || "Failed to submit post-settlement feedback");
      }
      const data: FeedbackResult = await res.json();
      setFeedbackResult(data);
      setCalibrationCounter((c) => c + 1);
      if (data.was_correct) {
        const fd = analysisDetail?.forecast;
        if (fd) {
          celebrate(buildCelebratePayload(fd));
        }
      }
    } catch (err: unknown) {
      setFeedbackError(err instanceof Error ? err.message : "Error submitting feedback");
    } finally {
      setSubmittingFeedback(false);
    }
  };

  const autoSettleTrade = async () => {
    if (!analysisId) return;
    setAutoSettling(true);
    setFeedbackError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/analyses/${analysisId}/auto-settle`, {
        method: "POST",
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || "Auto-settlement failed. You can still report settlement manually below.");
      }
      const data: FeedbackResult = await res.json();
      setFeedbackResult(data);
      setCalibrationCounter((c) => c + 1);
      if (data.was_correct) {
        const fd = analysisDetail?.forecast;
        if (fd) {
          celebrate(buildCelebratePayload(fd));
        }
      }
    } catch (err: unknown) {
      setFeedbackError(err instanceof Error ? err.message : "Error auto-settling trade");
    } finally {
      setAutoSettling(false);
    }
  };

  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  const forecast = analysisDetail?.forecast;
  const metrics = forecast?.decision_metrics;
  const stageMeta = currentStage ? STAGE_INFO[currentStage] : null;
  const progressPercent = stageMeta
    ? Math.round((stageMeta.step / TOTAL_PIPELINE_STAGES) * 100)
    : 0;

  // Dynamic outcome terminology: adapt for crypto (BUY UP / BUY DOWN) vs binary (BUY YES / BUY NO)
  const rawOutcomes = analysisDetail?.extraction?.outcomes;
  const outcomeLabels: string[] = Array.isArray(rawOutcomes)
    ? rawOutcomes.map((o: unknown) => {
        if (typeof o === "string") return o;
        if (typeof o === "object" && o && "label" in o) return String((o as { label?: string }).label || "");
        return "";
      })
    : [];

  const isUpMarket =
    outcomeLabels.some((l) => /up|higher/i.test(l)) ||
    forecast?.market?.category === "crypto";

  const affirmativeLabel = isUpMarket ? "UP" : "YES";
  const negativeLabel = isUpMarket ? "DOWN" : "NO";
  const buyAffirmativeText = isUpMarket ? "BUY UP" : "BUY YES";
  const buyNegativeText = isUpMarket ? "BUY DOWN" : "BUY NO";

  // Determine which UX state to render:
  // State 1: "upload" (idle, no analysis running, no results)
  // State 2: "processing" (loading spinner with progress text)
  // State 3: "result" (completed forecast or confirmation modal)
  const isProcessing = loading;
  const hasResult = !loading && (Boolean(forecast) || analysisDetail?.status === "NEEDS_USER_CONFIRMATION");
  const hasErrorOnly = !loading && Boolean(errorMsg) && !hasResult;

  return (
    <div className="min-h-screen bg-canvas text-zinc-900 font-sans selection:bg-accent/15 selection:text-zinc-900">
      {/* Top Header */}
      <header className="sticky top-0 z-50 border-b border-line/80 bg-canvas/80 backdrop-blur-md">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href="/" className="group flex items-baseline gap-2.5" aria-label="Klocka Prediction home">
              <span className="font-brand text-[27px] leading-none tracking-[-0.01em] text-zinc-950">
                Klocka<span className="text-accent">.</span>
              </span>
            </Link>
          </div>

          <div className="flex items-center gap-3 text-xs font-mono">
            {hasResult && (
              <button
                type="button"
                onClick={resetAnalysis}
                className="flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-zinc-600 transition-colors hover:border-zinc-300 hover:text-zinc-900"
              >
                <ArrowLeft className="size-3.5" />
                Upload New Image
              </button>
            )}
            <span className="hidden items-center gap-1.5 text-zinc-500 sm:flex">
              <span className="size-2 rounded-full bg-emerald-400 animate-pulse" />
              Live market data connected
            </span>
          </div>
        </div>
      </header>

      {/* Main Container */}
      <main className="max-w-5xl mx-auto px-4 sm:px-6 py-10">
        {/* ========================================================= */}
        {/* STATE 1: UPLOAD ONLY (Focus entirely on the image upload) */}
        {/* ========================================================= */}
        {!isProcessing && !hasResult && (
          <div className="max-w-xl mx-auto space-y-6">
            <div className="text-center space-y-2">
              <span className="inline-flex rounded-full border border-accent/20 bg-accent/10 px-2.5 py-1 font-mono text-[11px] font-semibold uppercase tracking-widest text-accent">
                Trade smarter
              </span>
              <h1 className="pt-2 text-2xl font-bold tracking-tight text-zinc-950 sm:text-3xl">
                Upload Market Screenshot
              </h1>
              <p className="mx-auto max-w-md text-sm text-zinc-500">
                Upload a market screenshot. We identify the contract, compare it with live data, and return a probability with the key risks.
              </p>
            </div>

            <div className="space-y-6 rounded-2xl border border-line bg-surface p-6 shadow-[0_18px_48px_-24px_rgba(23,23,23,0.16)] sm:p-8">
              {/* Dropzone */}
              <div
                onDragOver={(e) => e.preventDefault()}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                className={`group relative cursor-pointer rounded-xl border-2 border-dashed transition-all p-8 flex flex-col items-center justify-center text-center overflow-hidden ${
                  previewUrl
                    ? "border-accent/40 bg-raised/70"
                    : "border-line bg-raised/40 hover:border-accent/40 hover:bg-accent/[0.03]"
                }`}
              >
                <input
                  type="file"
                  ref={fileInputRef}
                  onChange={handleFileChange}
                  accept="image/png,image/jpeg,image/webp"
                  className="hidden"
                />

                {previewUrl ? (
                  <div className="space-y-4 w-full">
                    <div className="relative flex max-h-64 w-full items-center justify-center overflow-hidden rounded-lg border border-line bg-raised">
                      <Image
                        src={previewUrl}
                        alt="Screenshot preview"
                        width={600}
                        height={350}
                        className="object-contain max-h-64 w-auto"
                      />
                    </div>
                    <div className="flex items-center justify-between px-1 text-xs text-zinc-500">
                      <span className="max-w-[220px] truncate font-mono text-zinc-700">
                        {file?.name || "Uploaded Image"}
                      </span>
                      <span className="font-medium text-accent group-hover:underline">
                        Change image
                      </span>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="mb-3 flex size-14 items-center justify-center rounded-full bg-raised text-zinc-500 transition-colors group-hover:bg-accent/10 group-hover:text-accent">
                      <Upload className="size-6" />
                    </div>
                    <p className="text-sm font-semibold text-zinc-800">
                      Drag & drop screenshot here, or <span className="text-accent">browse</span>
                    </p>
                    <p className="mt-1.5 text-xs text-zinc-500">
                      Supports PNG, JPEG, WebP up to 10MB
                    </p>
                  </>
                )}
              </div>

              {/* Optional Reference Link */}
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-zinc-700">
                  Optional Market Reference / Slug
                </label>
                <input
                  type="text"
                  placeholder="e.g. will-btc-reach-100k-in-2026"
                  value={hint}
                  onChange={(e) => setHint(e.target.value)}
                  className="h-10 w-full rounded-lg border border-line bg-surface px-3.5 text-xs text-zinc-800 transition-colors placeholder:text-zinc-400 focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-accent/15"
                />
                <p className="text-[11px] text-zinc-500">
                  Used for disambiguation if image quality is degraded. Contract identity is always verified before analysis.
                </p>
              </div>

              {/* Error banner if present */}
              {errorMsg && (
                <div className="flex items-start gap-2.5 rounded-lg border border-rose-200 bg-rose-50 p-3.5 text-xs text-rose-700">
                  <AlertTriangle className="size-4 shrink-0 mt-0.5" />
                  <span>{errorMsg}</span>
                </div>
              )}

              {/* Action Button */}
              <button
                type="button"
                onClick={startAnalysis}
                disabled={!file && !hint}
                className="flex h-12 w-full cursor-pointer items-center justify-center gap-2 rounded-xl bg-accent-strong text-sm font-bold text-white shadow-[0_10px_24px_-10px_rgba(217,45,36,0.6)] transition-all hover:bg-accent-strong-hover disabled:cursor-not-allowed disabled:bg-zinc-200 disabled:text-zinc-400 disabled:shadow-none"
              >
                <Scale className="size-4" />
                Analyze Prediction Market
              </button>
            </div>

            {/* Subtle Product Boundary Note */}
            <div className="flex items-center justify-center gap-2 text-[11px] text-zinc-500">
              <ShieldCheck className="size-3.5 shrink-0 text-accent/80" />
              <span>Analysis & decision support only · Never executes trades or holds funds</span>
            </div>
          </div>
        )}

        {/* ========================================================= */}
        {/* STATE 2: PROCESSING (Loading spinner + text explaining)   */}
        {/* ========================================================= */}
        {isProcessing && (
          <div className="max-w-md mx-auto my-12 text-center space-y-8">
            <div className="space-y-6 rounded-3xl border border-line bg-surface p-10 shadow-[0_18px_48px_-24px_rgba(23,23,23,0.16)]">
              {/* Spinner */}
              <div className="relative size-24 mx-auto flex items-center justify-center">
                <div className="absolute inset-0 animate-spin rounded-full border-4 border-accent/15 border-t-accent" />
                <div className="absolute inset-2 animate-spin rounded-full border-4 border-zinc-200 border-b-zinc-400 [animation-duration:1.8s]" />
                <Scale className="size-8 animate-pulse text-accent" />
              </div>

              {/* Progress Text Below Spinner */}
              <div className="space-y-2">
                <div className="inline-flex items-center gap-2 rounded-full border border-accent/20 bg-accent/10 px-3 py-1 font-mono text-xs text-accent">
                  <span className="size-1.5 animate-ping rounded-full bg-accent" />
                  {stageMeta
                    ? `Step ${stageMeta.step} of ${TOTAL_PIPELINE_STAGES}: ${stageMeta.label}`
                    : "Executing Pipeline"}
                </div>

                <h3 className="text-lg font-semibold text-zinc-900">
                  {stageMeta?.label || "Processing Market Contract..."}
                </h3>

                <p className="flex min-h-[36px] items-center justify-center px-4 text-xs leading-relaxed text-zinc-500">
                  {latestProgressMessage || stageMeta?.desc || "Cross-referencing multimodal signals and order books..."}
                </p>
              </div>

              {/* Step indicator bar */}
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-raised">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-accent-strong to-accent transition-all duration-500"
                  style={{
                    width: `${progressPercent}%`,
                  }}
                />
              </div>

              {/* Cancel Button */}
              <button
                type="button"
                onClick={resetAnalysis}
                className="pt-2 text-xs font-medium text-zinc-500 transition-colors hover:text-zinc-900"
              >
                Cancel and start over
              </button>
            </div>
          </div>
        )}

        {/* ========================================================= */}
        {/* STATE 2b: ERROR ONLY */}
        {/* ========================================================= */}
        {hasErrorOnly && (
          <div className="max-w-lg mx-auto my-12">
            <div className="space-y-4 rounded-2xl border border-rose-200 bg-rose-50/60 p-8 text-center shadow-[0_18px_48px_-24px_rgba(23,23,23,0.16)]">
              <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-rose-100 text-rose-600">
                <XCircle className="size-6" />
              </div>
              <h3 className="text-lg font-semibold text-zinc-900">Analysis Could Not Complete</h3>
              <p className="rounded-lg border border-rose-200 bg-rose-100/60 p-3 font-mono text-xs text-rose-700">
                {errorMsg}
              </p>
              <button
                type="button"
                onClick={resetAnalysis}
                className="cursor-pointer rounded-xl border border-line bg-surface px-5 py-2.5 text-xs font-medium text-zinc-800 transition-colors hover:bg-raised"
              >
                ← Return to Upload
              </button>
            </div>
          </div>
        )}

        {/* ========================================================= */}
        {/* STATE 3: RESULTS (Displayed only when analysis is done)   */}
        {/* ========================================================= */}
        {hasResult && (
          <div className="space-y-6 animate-fade-in">
            {/* User Confirmation Required State */}
            {analysisDetail?.status === "NEEDS_USER_CONFIRMATION" && (
              <div className="space-y-4 rounded-2xl border border-amber-200 bg-amber-50/70 p-6 shadow-[0_18px_48px_-24px_rgba(23,23,23,0.16)]">
                <div className="flex items-center gap-2 text-sm font-semibold text-amber-700">
                  <AlertTriangle className="size-5" />
                  Contract Discrepancy — User Confirmation Required
                </div>
                <p className="text-xs text-zinc-600">
                  Multiple matching contracts were found. Please confirm which one you are evaluating:
                </p>
                <div className="space-y-2">
                  {analysisDetail.verification?.candidates?.map((c) => (
                    <div
                      key={c.candidate_id}
                      className="flex items-center justify-between gap-3 rounded-lg border border-line bg-surface p-3.5 text-xs"
                    >
                      <div>
                        <div className="font-medium text-zinc-900">{c.title}</div>
                        <div className="text-[11px] text-zinc-500">
                          Venue: <span className="uppercase text-accent">{c.platform}</span> · Match Confidence:{" "}
                          {(c.similarity_score * 100).toFixed(0)}%
                        </div>
                      </div>
                      <button
                        onClick={() => confirmCandidate(c.candidate_id)}
                        className="shrink-0 rounded-lg bg-accent-strong px-3.5 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-accent-strong-hover"
                      >
                        Confirm This Market
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Completed Forecast Cockpit */}
            {forecast && (
              <div className="space-y-6 rounded-2xl border border-line bg-surface p-6 shadow-[0_18px_48px_-24px_rgba(23,23,23,0.16)] sm:p-8">
                {/* Live Interval Countdown & Timing Provenance */}
                {(forecast.timing_provenance || forecast.market?.resolves_at) && (
                  <IntervalTimer
                    resolvesAt={forecast.timing_provenance?.exact_resolves_at || forecast.market?.resolves_at}
                    pipelineLatencySeconds={forecast.timing_provenance?.pipeline_latency_seconds}
                    initialSecondsRemaining={forecast.timing_provenance?.initial_seconds_remaining}
                    decisionSecondsRemaining={forecast.timing_provenance?.decision_seconds_remaining}
                    onScrollToSettlement={() => {
                      document.getElementById("settlement-training")?.scrollIntoView({ behavior: "smooth" });
                    }}
                  />
                )}

                {/* Result Top Line */}
                <div className="flex flex-col justify-between gap-4 border-b border-line pb-6 sm:flex-row sm:items-center">
                  <div>
                    <span className="text-xs font-mono uppercase tracking-wider text-zinc-500">
                      Recommendation Decision
                    </span>
                    <div className="mt-1 flex items-center gap-3">
                      {forecast.recommendation === "BUY_YES" && (
                        <span className="flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3.5 py-1 text-sm font-bold tracking-wide text-emerald-700">
                          <TrendingUp className="size-4" />
                          {buyAffirmativeText}
                        </span>
                      )}
                      {forecast.recommendation === "BUY_NO" && (
                        <span className="flex items-center gap-1.5 rounded-full border border-rose-200 bg-rose-50 px-3.5 py-1 text-sm font-bold tracking-wide text-rose-700">
                          <TrendingDown className="size-4" />
                          {buyNegativeText}
                        </span>
                      )}
                      {forecast.recommendation === "NO_TRADE" && (
                        <span className="rounded-full border border-line bg-raised px-3.5 py-1 text-sm font-semibold tracking-wide text-zinc-600">
                          NO TRADE (Abstain)
                        </span>
                      )}
                      <span className="text-xs font-medium text-zinc-500">
                        {forecast.explanation?.decision_reason || "Disciplined risk gate applied."}
                      </span>
                    </div>
                  </div>

                  <div className="sm:border-l sm:border-line sm:pl-6 sm:text-right">
                    <span className="text-xs font-mono uppercase tracking-wider text-zinc-500">
                      Calibrated Model Probability
                    </span>
                    <div className="mt-0.5 font-mono text-2xl font-bold text-zinc-900">
                      P({affirmativeLabel}) = {(Number(forecast.probability_yes) * 100).toFixed(1)}%
                    </div>
                    <div className="font-mono text-[11px] text-zinc-500">
                      90% CI: [
                      {forecast.uncertainty_interval
                        ? `${(Number(forecast.uncertainty_interval[0]) * 100).toFixed(0)}% - ${(Number(forecast.uncertainty_interval[1]) * 100).toFixed(0)}%`
                        : "N/A"}
                      ]
                    </div>
                  </div>
                </div>

                {/* Probability Distribution Bar */}
                <div className="space-y-1.5">
                  <div className="flex justify-between text-xs font-medium">
                    <span className="text-emerald-600">
                      {affirmativeLabel}: {(Number(forecast.probability_yes) * 100).toFixed(1)}%
                    </span>
                    <span className="text-rose-600">
                      {negativeLabel}: {(Number(forecast.probability_no) * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div className="flex h-3 w-full overflow-hidden rounded-full bg-raised">
                    <div
                      style={{ width: `${Number(forecast.probability_yes) * 100}%` }}
                      className="bg-emerald-500 transition-all duration-700"
                    />
                    <div
                      style={{ width: `${Number(forecast.probability_no) * 100}%` }}
                      className="bg-rose-500 transition-all duration-700"
                    />
                  </div>
                </div>

                {/* Verified Market Specification */}
                <div className="space-y-2 rounded-xl border border-line bg-raised/60 p-4">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-mono font-semibold uppercase tracking-wider text-accent">
                      Venue: {forecast.market?.platform}
                    </span>
                    {forecast.market?.url && (
                      <a
                        href={forecast.market.url}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-900"
                      >
                        View Official Contract <ExternalLink className="size-3" />
                      </a>
                    )}
                  </div>
                  <h4 className="text-sm font-semibold text-zinc-900">
                    {forecast.market?.title}
                  </h4>
                  {forecast.market?.resolves_at && (
                    <p className="text-[11px] text-zinc-500 flex items-center gap-1.5">
                      <Clock className="size-3" />
                      Resolution: {new Date(forecast.market.resolves_at).toUTCString()}
                    </p>
                  )}
                </div>

                {/* Specialist Domain Study (15-Minute Interval Patterns & Momentum) */}
                {forecast.specialist_context && (
                  <div className="space-y-3 rounded-xl border border-line bg-raised/60 p-4">
                    <div className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-2 font-mono font-semibold uppercase tracking-wider text-amber-700">
                        <span>Domain Interval Study & Microstructure</span>
                        <span className="rounded border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-normal text-amber-700">
                          {forecast.specialist_context.category?.toUpperCase() || "CRYPTO"} SPECIALIST
                        </span>
                      </div>
                      {forecast.specialist_context.crypto_research?.live_spot_reference?.price && (
                        <span className="font-mono text-xs text-zinc-700">
                          Spot: ${Number(forecast.specialist_context.crypto_research.live_spot_reference.price).toLocaleString()}
                        </span>
                      )}
                    </div>

                    {forecast.specialist_context.crypto_research?.interval_study && (
                      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs">
                        <div className="rounded border border-line bg-surface p-2.5">
                          <div className="text-[10px] font-mono text-zinc-500 uppercase">15m Trend Pattern</div>
                          <div className="mt-0.5 font-semibold capitalize text-zinc-800">
                            {forecast.specialist_context.crypto_research.interval_study.recent_interval_trend?.replace(/_/g, " ")}
                          </div>
                        </div>
                        <div className="rounded border border-line bg-surface p-2.5">
                          <div className="text-[10px] font-mono text-zinc-500 uppercase">15m RSI Oscillator</div>
                          <div className="mt-0.5 font-semibold text-emerald-600">
                            {forecast.specialist_context.crypto_research.interval_study.momentum_oscillator_rsi_15m}
                          </div>
                        </div>
                        <div className="col-span-2 rounded border border-line bg-surface p-2.5 sm:col-span-1">
                          <div className="text-[10px] font-mono text-zinc-500 uppercase">Order Flow Imbalance</div>
                          <div className="mt-0.5 truncate font-semibold text-zinc-800">
                            {forecast.specialist_context.crypto_research.interval_study.order_book_depth_ratio_bids_vs_asks}
                          </div>
                        </div>
                      </div>
                    )}

                    {forecast.specialist_context.crypto_research?.sentiment_and_social_signals && (
                      <div className="space-y-1 rounded border border-line bg-surface p-2.5 text-xs text-zinc-600">
                        <div className="text-[10px] font-mono uppercase text-zinc-500">Social Media & Sentiment Consensus</div>
                        <div>{forecast.specialist_context.crypto_research.sentiment_and_social_signals.social_media_prediction_consensus}</div>
                      </div>
                    )}
                  </div>
                )}

                {/* EV Decision Metrics Grid */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                  <div className="rounded-lg border border-line bg-raised/40 p-3">
                    <div className="text-zinc-500 font-mono text-[10px] uppercase">Ask YES</div>
                    <div className="mt-1 font-mono text-sm font-semibold text-zinc-800">
                      ${Number(metrics?.executable_ask_yes ?? 0).toFixed(3)}
                    </div>
                  </div>
                  <div className="rounded-lg border border-line bg-raised/40 p-3">
                    <div className="text-zinc-500 font-mono text-[10px] uppercase">Effective Cost</div>
                    <div className="mt-1 font-mono text-sm font-semibold text-zinc-800">
                      ${Number(metrics?.effective_cost_yes ?? 0).toFixed(3)}
                    </div>
                  </div>
                  <div className="rounded-lg border border-line bg-raised/40 p-3">
                    <div className="text-zinc-500 font-mono text-[10px] uppercase">Net Edge (YES)</div>
                    <div
                      className={`text-sm font-mono font-semibold mt-1 ${
                        Number(metrics?.estimated_net_edge_yes ?? 0) > 0
                          ? "text-emerald-600"
                          : "text-zinc-500"
                      }`}
                    >
                      {Number(metrics?.estimated_net_edge_yes ?? 0) > 0 ? "+" : ""}
                      {(Number(metrics?.estimated_net_edge_yes ?? 0) * 100).toFixed(2)}%
                    </div>
                  </div>
                  <div className="rounded-lg border border-line bg-raised/40 p-3">
                    <div className="text-zinc-500 font-mono text-[10px] uppercase">Quarter-Kelly</div>
                    <div className="mt-1 font-mono text-sm font-semibold text-zinc-800">
                      {(Number(metrics?.quarter_kelly_fraction ?? 0) * 100).toFixed(1)}%
                    </div>
                  </div>
                </div>

                {/* Analysis reasoning */}
                {forecast.explanation?.reasoning && (
                  <div className="space-y-2">
                    <h5 className="font-mono text-xs font-semibold uppercase tracking-wider text-zinc-600">
                      Analysis reasoning
                    </h5>
                    <p className="rounded-lg border border-line bg-raised/50 p-3.5 text-xs leading-relaxed text-zinc-600">
                      {forecast.explanation.reasoning}
                    </p>
                  </div>
                )}

                {/* Itemized Evidence Citations */}
                {forecast.evidence && forecast.evidence.length > 0 && (
                  <div className="space-y-2">
                    <h5 className="font-mono text-xs font-semibold uppercase tracking-wider text-zinc-600">
                      Documentary Evidence Citations
                    </h5>
                    <div className="space-y-2">
                      {forecast.evidence.map((ev, i) => (
                        <div
                          key={i}
                          className="space-y-1 rounded-lg border border-line bg-raised/40 p-3 text-xs"
                        >
                          <div className="flex items-center justify-between text-[11px]">
                            <span className="font-semibold text-zinc-700">{ev.source_citation}</span>
                            <span className="font-mono text-zinc-500 text-[10px]">
                              Relevance: {(Number(ev.relevance_score) * 100).toFixed(0)}%
                            </span>
                          </div>
                          <p className="text-[11px] italic text-zinc-600">&ldquo;{ev.snippet}&rdquo;</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Real-time Adaptive Calibration Matrix */}
                <CalibrationCard
                  symbol={forecast.market?.underlying_symbol || "BTC"}
                  apiBase={API_BASE}
                  refreshTrigger={calibrationCounter}
                />

                {/* Continuous Improvement & Post-Settlement Feedback Cockpit */}
                <div id="settlement-training" className="space-y-4 rounded-xl border border-line bg-raised/40 p-5 scroll-mt-8">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <RefreshCw className="size-4 text-accent" />
                      <h5 className="font-mono text-xs font-semibold uppercase tracking-wider text-zinc-800">
                        Trade Completed? Train AI with Actual Settlement
                      </h5>
                    </div>
                    <span className="text-[10px] font-mono text-zinc-500">
                      Self-improving feedback loop
                    </span>
                  </div>

                  <p className="text-xs text-zinc-500">
                    Once the market candle closes, report whether it resolved YES or NO. The AI records postmortem learnings into SQLite memory to avoid repeating mistakes and continuously improve future accuracy.
                  </p>

                  {!feedbackResult && (
                    <div className="flex flex-col gap-2 rounded-lg border border-accent/20 bg-accent/5 p-3.5 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <div className="flex items-center gap-1.5 font-mono text-xs font-bold text-zinc-900">
                          <Zap className="size-3.5 text-amber-500" />
                          <span>1-Click Auto-Settle via Real-Time Spot Price</span>
                        </div>
                        <p className="mt-0.5 text-[11px] text-zinc-500">
                          Pulls verified spot settlement from a global exchange mesh, compares against strike, and trains AI automatically.
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={autoSettleTrade}
                        disabled={autoSettling || submittingFeedback}
                        className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-zinc-900 px-3.5 py-2 text-xs font-semibold text-white shadow hover:bg-zinc-800 disabled:opacity-50 transition cursor-pointer"
                      >
                        {autoSettling ? (
                          <>
                            <Loader2 className="size-3.5 animate-spin" />
                            <span>Resolving Spot...</span>
                          </>
                        ) : (
                          <>
                            <Sparkles className="size-3.5 text-amber-400" />
                            <span>Auto-Detect &amp; Learn</span>
                          </>
                        )}
                      </button>
                    </div>
                  )}

                  {feedbackResult ? (
                    <div className="space-y-3 rounded-lg border border-accent/20 bg-accent/[0.04] p-4">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          {forecast?.recommendation === "NO_TRADE" ? (
                            // Abstention outcome — never show green (AGENTS.md rule 6)
                            <span className="rounded border border-indigo-200 bg-indigo-50 px-2.5 py-0.5 text-xs font-semibold text-indigo-700">
                              {feedbackResult.was_correct
                                ? "Abstention Audited (Lean Aligned)"
                                : "Disciplined Abstention (Capital Preserved)"}
                            </span>
                          ) : feedbackResult.was_correct ? (
                            <span className="rounded border border-emerald-200 bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700">
                              Prediction Correct (Aligned)
                            </span>
                          ) : (
                            <span className="rounded border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-xs font-semibold text-amber-700">
                              Discrepancy Analyzed
                            </span>
                          )}
                          {feedbackResult.discrepancy_classification && (
                            <span className="rounded border border-line bg-raised px-2 py-0.5 text-[10px] font-mono font-semibold uppercase tracking-wider text-zinc-600">
                              {feedbackResult.discrepancy_classification.replace(/_/g, " ")}
                            </span>
                          )}
                          <span className="font-mono text-xs text-zinc-700">
                            Settled as: {feedbackResult.actual_outcome}
                          </span>
                        </div>
                      </div>

                      {feedbackResult.visual_observations && (
                        <div className="space-y-1 rounded border border-sky-200 bg-sky-50/70 p-3 text-xs text-sky-950">
                          <div className="flex items-center gap-1.5 font-mono text-[10px] font-bold uppercase tracking-wider text-sky-800">
                            <span>📷</span> Settlement Screenshot Visual Inspection
                          </div>
                          <p className="leading-relaxed">{feedbackResult.visual_observations}</p>
                        </div>
                      )}

                      <div className="space-y-1">
                        <div className="text-[10px] font-mono uppercase text-zinc-500">AI Postmortem Reflection</div>
                        <p className="rounded border border-line bg-surface p-3 text-xs leading-relaxed text-zinc-700">
                          {feedbackResult.postmortem_analysis}
                        </p>
                      </div>

                      {feedbackResult.key_learnings && feedbackResult.key_learnings.length > 0 && (
                        <div className="space-y-1.5">
                          <div className="text-[10px] font-mono uppercase text-zinc-500">Learnings Injected into SQLite Long-Term Memory</div>
                          <ul className="list-inside list-disc space-y-1 text-xs text-zinc-500">
                            {feedbackResult.key_learnings.map((l, i) => (
                              <li key={i}>{l}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <div className="flex items-center gap-4">
                        <label className="text-xs font-medium text-zinc-700">Actual Settlement:</label>
                        <div className="flex gap-2">
                          <button
                            type="button"
                            onClick={() => setFeedbackOutcome(affirmativeLabel)}
                            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                              feedbackOutcome === affirmativeLabel || (affirmativeLabel === "UP" && feedbackOutcome === "YES")
                                ? "bg-emerald-600 text-white shadow-md shadow-emerald-600/20"
                                : "border border-line bg-surface text-zinc-500 hover:text-zinc-900"
                            }`}
                          >
                            Resolved {affirmativeLabel}
                          </button>
                          <button
                            type="button"
                            onClick={() => setFeedbackOutcome(negativeLabel)}
                            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                              feedbackOutcome === negativeLabel || (negativeLabel === "DOWN" && feedbackOutcome === "NO")
                                ? "bg-rose-600 text-white shadow-md shadow-rose-600/20"
                                : "border border-line bg-surface text-zinc-500 hover:text-zinc-900"
                            }`}
                          >
                            Resolved {negativeLabel}
                          </button>
                        </div>
                      </div>

                      <div>
                        <input
                          type="text"
                          placeholder="Optional notes: e.g. target reached in minute 8, unexpected news spike, etc."
                          value={feedbackNotes}
                          onChange={(e) => setFeedbackNotes(e.target.value)}
                          className="h-9 w-full rounded-lg border border-line bg-surface px-3 text-xs text-zinc-800 placeholder:text-zinc-400 focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-accent/15"
                        />
                      </div>

                      {/* Optional Post-Trade Settlement Screenshot Uploader */}
                      <div className="flex flex-col gap-2 rounded-lg border border-dashed border-line bg-surface/40 p-3">
                        <div className="flex items-center justify-between">
                          <span className="text-[11px] font-mono uppercase tracking-wider text-zinc-500">
                            Visual Trade Postmortem (Optional)
                          </span>
                          {settlementFile && (
                            <button
                              type="button"
                              onClick={removeSettlementFile}
                              className="text-[11px] font-mono text-rose-500 hover:text-rose-600"
                            >
                              Remove
                            </button>
                          )}
                        </div>
                        <p className="text-[11px] text-zinc-500">
                          Upload a screenshot of the closed candle chart or contract settlement screen. The AI will inspect the visible wicks, volume, and closing price to diagnose where things went wrong.
                        </p>
                        <input
                          ref={settlementInputRef}
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          onChange={handleSettlementFileChange}
                          className="hidden"
                        />
                        {!settlementFile ? (
                          <button
                            type="button"
                            onClick={() => settlementInputRef.current?.click()}
                            className="inline-flex w-fit items-center gap-1.5 rounded-md border border-line bg-surface px-3 py-1.5 text-xs font-medium text-zinc-600 hover:border-accent hover:text-zinc-900 transition-colors"
                          >
                            <Upload className="size-3 text-zinc-400" />
                            Attach Settlement Screenshot
                          </button>
                        ) : (
                          <div className="flex items-center gap-2.5 rounded border border-line bg-surface p-2 text-xs">
                            {settlementPreview && (
                              <img
                                src={settlementPreview}
                                alt="Settlement Preview"
                                className="size-8 rounded object-cover border border-line"
                              />
                            )}
                            <div className="min-w-0 flex-1">
                              <p className="truncate font-mono text-[11px] font-semibold text-zinc-800">{settlementFile.name}</p>
                              <p className="text-[10px] text-zinc-500">{(settlementFile.size / 1024).toFixed(1)} KB • Multimodal postmortem ready</p>
                            </div>
                          </div>
                        )}
                      </div>

                      {feedbackError && (
                        <p className="text-xs text-rose-600">{feedbackError}</p>
                      )}

                      <button
                        type="button"
                        onClick={submitFeedback}
                        disabled={submittingFeedback}
                        className="flex cursor-pointer items-center gap-2 rounded-lg bg-accent-strong px-4 py-2 text-xs font-semibold text-white transition-colors hover:bg-accent-strong-hover disabled:bg-zinc-200 disabled:text-zinc-400"
                      >
                        {submittingFeedback ? (
                          <>
                            <Loader2 className="size-3 animate-spin" />
                            Analyzing Settlement & Retraining AI...
                          </>
                        ) : (
                          <>
                            <RefreshCw className="size-3" />
                            Submit Settlement & Retrain AI
                          </>
                        )}
                      </button>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
