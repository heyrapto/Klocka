"use client";

import React, { useEffect, useRef, useState } from "react";
import Image from "next/image";
import {
  AlertTriangle,
  ArrowLeft,
  Clock,
  ExternalLink,
  Loader2,
  RefreshCw,
  Scale,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  Upload,
  XCircle,
} from "lucide-react";

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
  resolves_at?: string | null;
  resolution_source?: string | null;
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
  extraction?: {
    platform?: string;
    market_title?: string;
    asset_or_entity?: string;
    overall_confidence?: number;
    extraction_warnings?: string[];
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
}

const STAGE_INFO: Record<string, { label: string; desc: string; step: number }> = {
  IMAGE_VALIDATION: {
    label: "Image Validation",
    desc: "Checking dimensions, file integrity, and stripping metadata...",
    step: 1,
  },
  SCREENSHOT_EXTRACTION: {
    label: "Vision Extraction",
    desc: "Google Gemini reading contract terms, prices, and visible market state...",
    step: 2,
  },
  MARKET_IDENTIFICATION: {
    label: "Market Discovery",
    desc: "Searching live platform contracts on Polymarket & Bayse APIs...",
    step: 3,
  },
  MARKET_VERIFICATION: {
    label: "Contract Verification",
    desc: "Cross-verifying exact title, expiration date, and token identities...",
    step: 4,
  },
  DATA_COLLECTION: {
    label: "Order Book Ingestion",
    desc: "Recording real-time CLOB bids, asks, depth, and spread...",
    step: 5,
  },
  FEATURE_BUILDING: {
    label: "Live Context & Feature Building",
    desc: "Collecting live BTC context and building interval, liquidity, and momentum features...",
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

const TOTAL_PIPELINE_STAGES = Object.keys(STAGE_INFO).length;

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
  const [feedbackOutcome, setFeedbackOutcome] = useState<"YES" | "NO">("YES");
  const [feedbackNotes, setFeedbackNotes] = useState("");
  const [submittingFeedback, setSubmittingFeedback] = useState(false);
  const [feedbackResult, setFeedbackResult] = useState<FeedbackResult | null>(null);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

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
    }
    setFile(null);
    setPreviewUrl(null);
    setHint("");
    setLoading(false);
    setAnalysisId(null);
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
        setCurrentStage(payload.stage);
        if (payload.message) {
          setLatestProgressMessage(payload.message);
        }
      } catch (err) {
        console.error("SSE parse error", err);
      }
    });

    sse.addEventListener("analysis.completed", async () => {
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

  const submitFeedback = async () => {
    if (!analysisId) return;
    setSubmittingFeedback(true);
    setFeedbackError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/analyses/${analysisId}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          actual_outcome: feedbackOutcome,
          notes: feedbackNotes.trim() || null,
        }),
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || "Failed to submit post-settlement feedback");
      }
      const data = await res.json();
      setFeedbackResult(data);
    } catch (err: unknown) {
      setFeedbackError(err instanceof Error ? err.message : "Error submitting feedback");
    } finally {
      setSubmittingFeedback(false);
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

  // Determine which UX state to render:
  // State 1: "upload" (idle, no analysis running, no results)
  // State 2: "processing" (loading spinner with progress text)
  // State 3: "result" (completed forecast or confirmation modal)
  const isProcessing = loading;
  const hasResult = !loading && (Boolean(forecast) || analysisDetail?.status === "NEEDS_USER_CONFIRMATION");
  const hasErrorOnly = !loading && Boolean(errorMsg) && !hasResult;

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 font-sans selection:bg-sky-500 selection:text-white">
      {/* Top Header */}
      <header className="border-b border-zinc-800/80 bg-zinc-900/40 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="size-9 rounded-lg bg-gradient-to-tr from-sky-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-sky-500/20">
              <Scale className="size-5 text-white" />
            </div>
            <div>
              <span className="font-semibold tracking-tight text-white flex items-center gap-2">
                Prediction Intelligence
                <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded-full bg-sky-500/10 text-sky-400 border border-sky-500/20">
                  AI Cockpit
                </span>
              </span>
              <p className="text-xs text-zinc-400">
                Verified-Market Screenshot Analysis & Calibrated Probability Forecasts
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3 text-xs font-mono">
            {hasResult && (
              <button
                type="button"
                onClick={resetAnalysis}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-zinc-700 bg-zinc-800/80 hover:bg-zinc-700 text-zinc-200 transition-colors"
              >
                <ArrowLeft className="size-3.5" />
                Upload New Image
              </button>
            )}
            <span className="hidden sm:flex items-center gap-1.5 text-zinc-400">
              <span className="size-2 rounded-full bg-emerald-400 animate-pulse" />
              Polymarket & Bayse CLOB
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
              <span className="text-[11px] font-mono uppercase tracking-widest text-sky-400 font-semibold px-2.5 py-1 rounded-full bg-sky-500/10 border border-sky-500/20">
                Screenshot-Driven Probabilistic Intelligence
              </span>
              <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-white pt-2">
                Upload Market Screenshot
              </h1>
              <p className="text-sm text-zinc-400 max-w-md mx-auto">
                Drop any screenshot from Polymarket or Bayse. The AI extracts the contract, verifies order book depth, studies interval patterns, and computes an Expected Value decision.
              </p>
            </div>

            <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6 sm:p-8 shadow-2xl backdrop-blur-sm space-y-6">
              {/* Dropzone */}
              <div
                onDragOver={(e) => e.preventDefault()}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                className={`group relative cursor-pointer rounded-xl border-2 border-dashed transition-all p-8 flex flex-col items-center justify-center text-center overflow-hidden ${
                  previewUrl
                    ? "border-sky-500/40 bg-zinc-950/80"
                    : "border-zinc-800 hover:border-sky-500/50 bg-zinc-950/40 hover:bg-sky-500/[0.02]"
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
                    <div className="relative rounded-lg overflow-hidden border border-zinc-700/80 max-h-64 w-full flex items-center justify-center bg-black">
                      <Image
                        src={previewUrl}
                        alt="Screenshot preview"
                        width={600}
                        height={350}
                        className="object-contain max-h-64 w-auto"
                      />
                    </div>
                    <div className="flex items-center justify-between text-xs text-zinc-400 px-1">
                      <span className="truncate max-w-[220px] font-mono text-zinc-300">
                        {file?.name || "Uploaded Image"}
                      </span>
                      <span className="text-sky-400 font-medium group-hover:underline">
                        Change image
                      </span>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="size-14 rounded-full bg-zinc-800/80 group-hover:bg-sky-500/20 group-hover:text-sky-400 flex items-center justify-center text-zinc-400 transition-colors mb-3">
                      <Upload className="size-6" />
                    </div>
                    <p className="text-sm font-semibold text-zinc-200">
                      Drag & drop screenshot here, or <span className="text-sky-400">browse</span>
                    </p>
                    <p className="text-xs text-zinc-500 mt-1.5">
                      Supports PNG, JPEG, WebP up to 10MB
                    </p>
                  </>
                )}
              </div>

              {/* Optional Reference Link */}
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-zinc-300">
                  Optional Market Reference / Slug
                </label>
                <input
                  type="text"
                  placeholder="e.g. will-btc-reach-100k-in-2026"
                  value={hint}
                  onChange={(e) => setHint(e.target.value)}
                  className="w-full h-10 px-3.5 rounded-lg border border-zinc-800 bg-zinc-950/80 text-xs text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-sky-500/60 focus:ring-1 focus:ring-sky-500/60 transition-colors"
                />
                <p className="text-[11px] text-zinc-500">
                  Used for disambiguation if image quality is degraded. Contract identity is always verified via official platform APIs.
                </p>
              </div>

              {/* Error banner if present */}
              {errorMsg && (
                <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3.5 flex items-start gap-2.5 text-xs text-rose-300">
                  <AlertTriangle className="size-4 shrink-0 mt-0.5" />
                  <span>{errorMsg}</span>
                </div>
              )}

              {/* Action Button */}
              <button
                type="button"
                onClick={startAnalysis}
                disabled={!file && !hint}
                className="w-full h-12 rounded-xl bg-sky-500 hover:bg-sky-400 disabled:bg-zinc-800 disabled:text-zinc-600 disabled:cursor-not-allowed text-zinc-950 font-bold text-sm transition-all shadow-lg shadow-sky-500/20 flex items-center justify-center gap-2 cursor-pointer"
              >
                <Scale className="size-4" />
                Analyze Prediction Market
              </button>
            </div>

            {/* Subtle Product Boundary Note */}
            <div className="flex items-center justify-center gap-2 text-[11px] text-zinc-500">
              <ShieldCheck className="size-3.5 text-sky-400/80 shrink-0" />
              <span>Analysis & decision support only · Never executes trades or holds funds</span>
            </div>
          </div>
        )}

        {/* ========================================================= */}
        {/* STATE 2: PROCESSING (Loading spinner + text explaining)   */}
        {/* ========================================================= */}
        {isProcessing && (
          <div className="max-w-md mx-auto my-12 text-center space-y-8">
            <div className="rounded-3xl border border-zinc-800 bg-zinc-900/60 p-10 shadow-2xl backdrop-blur-sm space-y-6">
              {/* Spinner */}
              <div className="relative size-24 mx-auto flex items-center justify-center">
                <div className="absolute inset-0 rounded-full border-4 border-sky-500/20 border-t-sky-500 animate-spin" />
                <div className="absolute inset-2 rounded-full border-4 border-indigo-500/10 border-b-indigo-400 animate-spin [animation-duration:1.8s]" />
                <Scale className="size-8 text-sky-400 animate-pulse" />
              </div>

              {/* Progress Text Below Spinner */}
              <div className="space-y-2">
                <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-sky-500/10 border border-sky-500/20 text-xs font-mono text-sky-400">
                  <span className="size-1.5 rounded-full bg-sky-400 animate-ping" />
                  {stageMeta
                    ? `Step ${stageMeta.step} of ${TOTAL_PIPELINE_STAGES}: ${stageMeta.label}`
                    : "Executing Pipeline"}
                </div>

                <h3 className="text-lg font-semibold text-white">
                  {stageMeta?.label || "Processing Market Contract..."}
                </h3>

                <p className="text-xs text-zinc-400 min-h-[36px] flex items-center justify-center px-4 leading-relaxed">
                  {latestProgressMessage || stageMeta?.desc || "Cross-referencing multimodal signals and order books..."}
                </p>
              </div>

              {/* Step indicator bar */}
              <div className="w-full bg-zinc-800/80 h-1.5 rounded-full overflow-hidden">
                <div
                  className="bg-gradient-to-r from-sky-500 to-indigo-500 h-full transition-all duration-500 rounded-full"
                  style={{
                    width: `${Math.max(12, ((stageMeta?.step || 1) / TOTAL_PIPELINE_STAGES) * 100)}%`,
                  }}
                />
              </div>

              {/* Cancel Button */}
              <button
                type="button"
                onClick={resetAnalysis}
                className="text-xs text-zinc-500 hover:text-zinc-300 font-medium transition-colors pt-2"
              >
                Cancel and start over
              </button>
            </div>
          </div>
        )}

        {/* ========================================================= */}
        {/* STATE 2b: ERROR ONLY (e.g. Gemini key missing or network) */}
        {/* ========================================================= */}
        {hasErrorOnly && (
          <div className="max-w-lg mx-auto my-12">
            <div className="rounded-2xl border border-rose-500/30 bg-rose-500/[0.04] p-8 shadow-2xl text-center space-y-4">
              <div className="size-12 rounded-full bg-rose-500/10 text-rose-400 flex items-center justify-center mx-auto">
                <XCircle className="size-6" />
              </div>
              <h3 className="text-lg font-semibold text-white">Analysis Could Not Complete</h3>
              <p className="text-xs text-rose-300 bg-rose-500/10 p-3 rounded-lg border border-rose-500/20 font-mono">
                {errorMsg}
              </p>
              <button
                type="button"
                onClick={resetAnalysis}
                className="px-5 py-2.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-white font-medium text-xs transition-colors cursor-pointer"
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
              <div className="rounded-2xl border border-amber-500/40 bg-amber-500/[0.04] p-6 shadow-xl space-y-4">
                <div className="flex items-center gap-2 text-amber-400 font-semibold text-sm">
                  <AlertTriangle className="size-5" />
                  Contract Discrepancy — User Confirmation Required
                </div>
                <p className="text-xs text-zinc-300">
                  Multiple candidate markets were found on the platform API. Please confirm which contract you are evaluating:
                </p>
                <div className="space-y-2">
                  {analysisDetail.verification?.candidates?.map((c) => (
                    <div
                      key={c.candidate_id}
                      className="p-3.5 rounded-lg border border-zinc-800 bg-zinc-900 flex items-center justify-between gap-3 text-xs"
                    >
                      <div>
                        <div className="font-medium text-white">{c.title}</div>
                        <div className="text-[11px] text-zinc-500">
                          Venue: <span className="uppercase text-sky-400">{c.platform}</span> · Match Confidence:{" "}
                          {(c.similarity_score * 100).toFixed(0)}%
                        </div>
                      </div>
                      <button
                        onClick={() => confirmCandidate(c.candidate_id)}
                        className="px-3.5 py-1.5 rounded-lg bg-sky-500 hover:bg-sky-400 text-zinc-950 font-semibold text-xs transition-colors shrink-0"
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
              <div className="rounded-2xl border border-zinc-800 bg-zinc-900/60 p-6 sm:p-8 shadow-2xl space-y-6">
                {/* Result Top Line */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 border-b border-zinc-800">
                  <div>
                    <span className="text-xs font-mono uppercase tracking-wider text-zinc-500">
                      Recommendation Decision
                    </span>
                    <div className="mt-1 flex items-center gap-3">
                      {forecast.recommendation === "BUY_YES" && (
                        <span className="px-3.5 py-1 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 font-bold text-sm tracking-wide flex items-center gap-1.5">
                          <TrendingUp className="size-4" />
                          BUY YES
                        </span>
                      )}
                      {forecast.recommendation === "BUY_NO" && (
                        <span className="px-3.5 py-1 rounded-full bg-indigo-500/15 text-indigo-400 border border-indigo-500/30 font-bold text-sm tracking-wide flex items-center gap-1.5">
                          <TrendingDown className="size-4" />
                          BUY NO
                        </span>
                      )}
                      {forecast.recommendation === "NO_TRADE" && (
                        <span className="px-3.5 py-1 rounded-full bg-zinc-800 text-zinc-300 border border-zinc-700 font-semibold text-sm tracking-wide">
                          NO TRADE (Abstain)
                        </span>
                      )}
                      <span className="text-xs text-zinc-400 font-medium">
                        {forecast.explanation?.decision_reason || "Disciplined risk gate applied."}
                      </span>
                    </div>
                  </div>

                  <div className="sm:text-right sm:border-l sm:border-zinc-800 sm:pl-6">
                    <span className="text-xs font-mono uppercase tracking-wider text-zinc-500">
                      Calibrated Model Probability
                    </span>
                    <div className="text-2xl font-mono font-bold text-white mt-0.5">
                      P(YES) = {(Number(forecast.probability_yes) * 100).toFixed(1)}%
                    </div>
                    <div className="text-[11px] font-mono text-zinc-400">
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
                    <span className="text-emerald-400">
                      YES: {(Number(forecast.probability_yes) * 100).toFixed(1)}%
                    </span>
                    <span className="text-indigo-400">
                      NO: {(Number(forecast.probability_no) * 100).toFixed(1)}%
                    </span>
                  </div>
                  <div className="h-3 w-full rounded-full bg-zinc-800 overflow-hidden flex">
                    <div
                      style={{ width: `${Number(forecast.probability_yes) * 100}%` }}
                      className="bg-emerald-500 transition-all duration-700"
                    />
                    <div
                      style={{ width: `${Number(forecast.probability_no) * 100}%` }}
                      className="bg-indigo-500 transition-all duration-700"
                    />
                  </div>
                </div>

                {/* Verified Market Specification */}
                <div className="rounded-xl bg-zinc-950/70 border border-zinc-800/80 p-4 space-y-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-mono text-sky-400 uppercase tracking-wider font-semibold">
                      Venue: {forecast.market?.platform}
                    </span>
                    {forecast.market?.url && (
                      <a
                        href={forecast.market.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-zinc-500 hover:text-zinc-300 flex items-center gap-1 text-[11px]"
                      >
                        View Official Contract <ExternalLink className="size-3" />
                      </a>
                    )}
                  </div>
                  <h4 className="text-sm font-semibold text-zinc-100">
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
                  <div className="rounded-xl bg-zinc-950/70 border border-zinc-800/80 p-4 space-y-3">
                    <div className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-2 font-mono text-amber-400 uppercase tracking-wider font-semibold">
                        <span>Domain Interval Study & Microstructure</span>
                        <span className="text-[10px] px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20 font-normal">
                          {forecast.specialist_context.category?.toUpperCase() || "CRYPTO"} SPECIALIST
                        </span>
                      </div>
                      {forecast.specialist_context.crypto_research?.live_spot_reference?.price && (
                        <span className="text-xs font-mono text-zinc-300">
                          Spot: ${Number(forecast.specialist_context.crypto_research.live_spot_reference.price).toLocaleString()}
                        </span>
                      )}
                    </div>

                    {forecast.specialist_context.crypto_research?.interval_study && (
                      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-xs">
                        <div className="p-2.5 rounded bg-zinc-900/80 border border-zinc-800">
                          <div className="text-[10px] font-mono text-zinc-500 uppercase">15m Trend Pattern</div>
                          <div className="font-semibold text-zinc-200 mt-0.5 capitalize">
                            {forecast.specialist_context.crypto_research.interval_study.recent_interval_trend?.replace(/_/g, " ")}
                          </div>
                        </div>
                        <div className="p-2.5 rounded bg-zinc-900/80 border border-zinc-800">
                          <div className="text-[10px] font-mono text-zinc-500 uppercase">15m RSI Oscillator</div>
                          <div className="font-semibold text-emerald-400 mt-0.5">
                            {forecast.specialist_context.crypto_research.interval_study.momentum_oscillator_rsi_15m}
                          </div>
                        </div>
                        <div className="p-2.5 rounded bg-zinc-900/80 border border-zinc-800 col-span-2 sm:col-span-1">
                          <div className="text-[10px] font-mono text-zinc-500 uppercase">Order Flow Imbalance</div>
                          <div className="font-semibold text-zinc-200 mt-0.5 truncate">
                            {forecast.specialist_context.crypto_research.interval_study.order_book_depth_ratio_bids_vs_asks}
                          </div>
                        </div>
                      </div>
                    )}

                    {forecast.specialist_context.crypto_research?.sentiment_and_social_signals && (
                      <div className="text-xs text-zinc-400 bg-zinc-900/50 p-2.5 rounded border border-zinc-800 space-y-1">
                        <div className="text-[10px] font-mono uppercase text-zinc-500">Social Media & Sentiment Consensus</div>
                        <div>{forecast.specialist_context.crypto_research.sentiment_and_social_signals.social_media_prediction_consensus}</div>
                      </div>
                    )}
                  </div>
                )}

                {/* EV Decision Metrics Grid */}
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                  <div className="p-3 rounded-lg border border-zinc-800/80 bg-zinc-950">
                    <div className="text-zinc-500 font-mono text-[10px] uppercase">Ask YES</div>
                    <div className="text-sm font-mono font-semibold text-zinc-200 mt-1">
                      ${Number(metrics?.executable_ask_yes ?? 0).toFixed(3)}
                    </div>
                  </div>
                  <div className="p-3 rounded-lg border border-zinc-800/80 bg-zinc-950">
                    <div className="text-zinc-500 font-mono text-[10px] uppercase">Effective Cost</div>
                    <div className="text-sm font-mono font-semibold text-zinc-200 mt-1">
                      ${Number(metrics?.effective_cost_yes ?? 0).toFixed(3)}
                    </div>
                  </div>
                  <div className="p-3 rounded-lg border border-zinc-800/80 bg-zinc-950">
                    <div className="text-zinc-500 font-mono text-[10px] uppercase">Net Edge (YES)</div>
                    <div
                      className={`text-sm font-mono font-semibold mt-1 ${
                        Number(metrics?.estimated_net_edge_yes ?? 0) > 0
                          ? "text-emerald-400"
                          : "text-zinc-400"
                      }`}
                    >
                      {Number(metrics?.estimated_net_edge_yes ?? 0) > 0 ? "+" : ""}
                      {(Number(metrics?.estimated_net_edge_yes ?? 0) * 100).toFixed(2)}%
                    </div>
                  </div>
                  <div className="p-3 rounded-lg border border-zinc-800/80 bg-zinc-950">
                    <div className="text-zinc-500 font-mono text-[10px] uppercase">Quarter-Kelly</div>
                    <div className="text-sm font-mono font-semibold text-zinc-200 mt-1">
                      {(Number(metrics?.quarter_kelly_fraction ?? 0) * 100).toFixed(1)}%
                    </div>
                  </div>
                </div>

                {/* Gemini Analytical Synthesis & Reasoning */}
                {forecast.explanation?.reasoning && (
                  <div className="space-y-2">
                    <h5 className="text-xs font-semibold text-zinc-300 uppercase tracking-wider font-mono">
                      Gemini Multimodal Reasoning Synthesis
                    </h5>
                    <p className="text-xs text-zinc-400 leading-relaxed bg-zinc-950/60 p-3.5 rounded-lg border border-zinc-800/80">
                      {forecast.explanation.reasoning}
                    </p>
                  </div>
                )}

                {/* Itemized Evidence Citations */}
                {forecast.evidence && forecast.evidence.length > 0 && (
                  <div className="space-y-2">
                    <h5 className="text-xs font-semibold text-zinc-300 uppercase tracking-wider font-mono">
                      Documentary Evidence Citations
                    </h5>
                    <div className="space-y-2">
                      {forecast.evidence.map((ev, i) => (
                        <div
                          key={i}
                          className="p-3 rounded-lg border border-zinc-800/60 bg-zinc-950/40 text-xs space-y-1"
                        >
                          <div className="flex items-center justify-between text-[11px]">
                            <span className="font-semibold text-zinc-300">{ev.source_citation}</span>
                            <span className="font-mono text-zinc-500 text-[10px]">
                              Relevance: {(Number(ev.relevance_score) * 100).toFixed(0)}%
                            </span>
                          </div>
                          <p className="text-zinc-400 text-[11px] italic">&ldquo;{ev.snippet}&rdquo;</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Continuous Improvement & Post-Settlement Feedback Cockpit */}
                <div className="rounded-xl bg-zinc-950/80 border border-zinc-800/90 p-5 space-y-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <RefreshCw className="size-4 text-sky-400" />
                      <h5 className="text-xs font-semibold text-zinc-200 uppercase tracking-wider font-mono">
                        Trade Completed? Train AI with Actual Settlement
                      </h5>
                    </div>
                    <span className="text-[10px] font-mono text-zinc-500">
                      Self-improving feedback loop
                    </span>
                  </div>

                  <p className="text-xs text-zinc-400">
                    Once the market candle closes, report whether it resolved YES or NO. The AI records postmortem learnings into SQLite memory to avoid repeating mistakes and continuously improve future accuracy.
                  </p>

                  {feedbackResult ? (
                    <div className="rounded-lg border border-sky-500/30 bg-sky-500/5 p-4 space-y-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          {feedbackResult.was_correct ? (
                            <span className="px-2.5 py-0.5 rounded text-xs font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                              Prediction Correct (Aligned)
                            </span>
                          ) : (
                            <span className="px-2.5 py-0.5 rounded text-xs font-semibold bg-amber-500/20 text-amber-400 border border-amber-500/30">
                              Discrepancy Analyzed
                            </span>
                          )}
                          <span className="text-xs text-zinc-300 font-mono">
                            Settled as: {feedbackResult.actual_outcome}
                          </span>
                        </div>
                      </div>

                      <div className="space-y-1">
                        <div className="text-[10px] font-mono uppercase text-zinc-500">AI Postmortem Reflection</div>
                        <p className="text-xs text-zinc-300 leading-relaxed bg-zinc-900/80 p-3 rounded border border-zinc-800">
                          {feedbackResult.postmortem_analysis}
                        </p>
                      </div>

                      {feedbackResult.key_learnings && feedbackResult.key_learnings.length > 0 && (
                        <div className="space-y-1.5">
                          <div className="text-[10px] font-mono uppercase text-zinc-500">Learnings Injected into SQLite Long-Term Memory</div>
                          <ul className="space-y-1 text-xs text-zinc-400 list-disc list-inside">
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
                        <label className="text-xs font-medium text-zinc-300">Actual Settlement:</label>
                        <div className="flex gap-2">
                          <button
                            type="button"
                            onClick={() => setFeedbackOutcome("YES")}
                            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                              feedbackOutcome === "YES"
                                ? "bg-emerald-500 text-zinc-950 shadow-md shadow-emerald-500/20"
                                : "bg-zinc-900 text-zinc-400 hover:text-zinc-200 border border-zinc-800"
                            }`}
                          >
                            Resolved YES
                          </button>
                          <button
                            type="button"
                            onClick={() => setFeedbackOutcome("NO")}
                            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                              feedbackOutcome === "NO"
                                ? "bg-indigo-500 text-white shadow-md shadow-indigo-500/20"
                                : "bg-zinc-900 text-zinc-400 hover:text-zinc-200 border border-zinc-800"
                            }`}
                          >
                            Resolved NO
                          </button>
                        </div>
                      </div>

                      <div>
                        <input
                          type="text"
                          placeholder="Optional notes: e.g. target reached in minute 8, unexpected news spike, etc."
                          value={feedbackNotes}
                          onChange={(e) => setFeedbackNotes(e.target.value)}
                          className="w-full h-9 px-3 rounded-lg border border-zinc-800 bg-zinc-900 text-xs text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-sky-500"
                        />
                      </div>

                      {feedbackError && (
                        <p className="text-xs text-rose-400">{feedbackError}</p>
                      )}

                      <button
                        type="button"
                        onClick={submitFeedback}
                        disabled={submittingFeedback}
                        className="px-4 py-2 rounded-lg bg-sky-500 hover:bg-sky-400 disabled:bg-zinc-800 text-zinc-950 font-semibold text-xs transition-colors flex items-center gap-2 cursor-pointer"
                      >
                        {submittingFeedback ? (
                          <>
                            <Loader2 className="size-3 animate-spin" />
                            Analyzing Settlement & Updating SQLite Memory...
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
