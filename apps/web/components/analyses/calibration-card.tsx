"use client";

import { Activity, BrainCircuit, CheckCircle2, Cpu, Gauge, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";

export interface AssetCalibration {
  symbol: string;
  category: string;
  sample_count: number;
  correct_count: number;
  win_rate: number | null;
  brier_score: number;
  vol_multiplier: number;
  momentum_weight: number;
  order_flow_weight: number;
  uncertainty_margin: number;
  last_discrepancy_reason: string | null;
  updated_at: string;
}

interface CalibrationCardProps {
  symbol?: string;
  apiBase?: string;
  refreshTrigger?: number;
}

export function CalibrationCard({
  symbol = "BTC",
  apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  refreshTrigger = 0,
}: CalibrationCardProps) {
  const [data, setData] = useState<AssetCalibration | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function fetchCalibration() {
      setLoading(true);
      try {
        const resp = await fetch(`${apiBase}/api/v1/analyses/calibration/${encodeURIComponent(symbol)}`);
        if (resp.ok) {
          const json = await resp.json();
          if (!cancelled) {
            setData(json);
          }
        }
      } catch (err) {
        console.error("Failed to load asset calibration matrix", err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    fetchCalibration();
    return () => {
      cancelled = true;
    };
  }, [symbol, apiBase, refreshTrigger]);

  if (!data && !loading) return null;

  const winRatePct = data?.win_rate != null ? (data.win_rate * 100).toFixed(1) : "--";
  const brierColor =
    (data?.brier_score ?? 0.25) <= 0.20
      ? "text-emerald-700 bg-emerald-50 border-emerald-200"
      : (data?.brier_score ?? 0.25) <= 0.25
        ? "text-amber-700 bg-amber-50 border-amber-200"
        : "text-rose-700 bg-rose-50 border-rose-200";

  return (
    <div className="rounded-xl border border-line bg-surface/90 p-5 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between border-b border-line pb-4">
        <div className="flex items-center gap-2.5">
          <div className="flex size-8 items-center justify-center rounded-lg border border-accent/20 bg-accent/10 text-accent">
            <BrainCircuit className="size-4" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h4 className="font-mono text-xs font-bold uppercase tracking-wider text-zinc-900">
                {data?.symbol || symbol} Adaptive Calibration Matrix
              </h4>
              <span className="inline-flex items-center gap-1 rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[9px] font-semibold text-sky-700 uppercase">
                <Sparkles className="size-2.5" />
                Self-Tuning Memory
              </span>
            </div>
            <p className="text-[11px] text-zinc-500">
              Dynamically recalibrating option volatility and indicator weights based on verified candle settlements.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs">
          <span className="font-mono text-zinc-500">Samples:</span>
          <span className="font-mono font-bold text-zinc-800">{data?.sample_count ?? 0}</span>
          <span className="text-zinc-300">•</span>
          <span className="font-mono text-zinc-500">Win Rate:</span>
          <span className="font-mono font-bold text-emerald-700">{winRatePct}%</span>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div className="rounded-lg border border-line bg-raised/40 p-2.5">
          <div className="flex items-center justify-between text-[10px] font-mono uppercase text-zinc-500">
            <span>Rolling Brier</span>
            <Gauge className="size-3 text-zinc-500" />
          </div>
          <div className="mt-1 flex items-baseline gap-1.5">
            <span className="font-mono text-base font-bold text-zinc-900">
              {data ? data.brier_score.toFixed(3) : "0.250"}
            </span>
            <span className={`rounded border px-1.5 py-0.2 text-[9px] font-semibold ${brierColor}`}>
              {(data?.brier_score ?? 0.25) <= 0.20 ? "Calibrated" : "Baseline"}
            </span>
          </div>
          <p className="mt-0.5 text-[10px] text-zinc-500">0.0 = perfect, 0.25 = random</p>
        </div>

        <div className="rounded-lg border border-line bg-raised/40 p-2.5">
          <div className="flex items-center justify-between text-[10px] font-mono uppercase text-zinc-500">
            <span>Vol Multiplier</span>
            <Activity className="size-3 text-zinc-500" />
          </div>
          <div className="mt-1 flex items-baseline gap-1">
            <span className="font-mono text-base font-bold text-zinc-900">
              {data ? `${data.vol_multiplier.toFixed(2)}x` : "1.00x"}
            </span>
          </div>
          <p className="mt-0.5 text-[10px] text-zinc-500">Dynamic EWMA vol scaler</p>
        </div>

        <div className="rounded-lg border border-line bg-raised/40 p-2.5">
          <div className="flex items-center justify-between text-[10px] font-mono uppercase text-zinc-500">
            <span>Order Flow Weight</span>
            <Cpu className="size-3 text-zinc-500" />
          </div>
          <div className="mt-1 flex items-baseline gap-1">
            <span className="font-mono text-base font-bold text-zinc-900">
              {data ? `${(data.order_flow_weight * 100).toFixed(0)}%` : "30%"}
            </span>
            <span className="text-[10px] text-zinc-500">
              (Mom: {data ? `${(data.momentum_weight * 100).toFixed(0)}%` : "35%"})
            </span>
          </div>
          <p className="mt-0.5 text-[10px] text-zinc-500">Microstructure blend</p>
        </div>

        <div className="rounded-lg border border-line bg-raised/40 p-2.5">
          <div className="flex items-center justify-between text-[10px] font-mono uppercase text-zinc-500">
            <span>Uncertainty Buffer</span>
            <CheckCircle2 className="size-3 text-zinc-500" />
          </div>
          <div className="mt-1 flex items-baseline gap-1">
            <span className="font-mono text-base font-bold text-zinc-900">
              ±{data ? `${(data.uncertainty_margin * 100).toFixed(1)}%` : "2.0%"}
            </span>
          </div>
          <p className="mt-0.5 text-[10px] text-zinc-500">Tail-risk margin</p>
        </div>
      </div>

      {data?.last_discrepancy_reason && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-line/60 bg-raised/60 px-3 py-1.5 text-[11px] text-zinc-600">
          <span className="font-mono font-semibold text-zinc-700">Last Reflection:</span>
          <span className="rounded bg-surface px-1.5 py-0.5 font-mono text-[10px] font-medium text-zinc-800 border border-line">
            {data.last_discrepancy_reason.replace(/_/g, " ")}
          </span>
          <span className="text-zinc-500 text-[10px]">
            Learned from previous settlement discrepancy
          </span>
        </div>
      )}
    </div>
  );
}
