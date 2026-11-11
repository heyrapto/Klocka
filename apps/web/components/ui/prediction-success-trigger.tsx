"use client";

/**
 * PredictionSuccessTrigger
 *
 * A lightweight demo/dev button that fires the celebratory modal using
 * a resolved fixture from demo-data. This component should be placed on
 * any page where a forecast is displayed so QA and stakeholders can verify
 * the modal without needing a live resolved market.
 *
 * In production this component is rendered only when a real forecast reaches
 * a resolved (YES/NO) state; pass the live `forecast` and optional `market`
 * props to `celebrate()` from the resolution polling hook instead.
 */

import { Trophy } from "lucide-react";

import { usePredictionSuccess } from "@/components/ui/prediction-success-context";
import { Button } from "@/components/ui/button";
import type { Forecast, Market } from "@/lib/types";

export interface PredictionSuccessTriggerProps {
  forecast: Forecast;
  market?: Market;
  /** Override button label (default: "🎉 Celebrate prediction") */
  label?: string;
}

export function PredictionSuccessTrigger({
  forecast,
  market,
  label = "Celebrate prediction",
}: PredictionSuccessTriggerProps) {
  const { celebrate } = usePredictionSuccess();

  function handleClick() {
    celebrate({ forecast, market });
  }

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={handleClick}
      className="gap-1.5 border-emerald-500/30 text-emerald-400 hover:border-emerald-500/60 hover:bg-emerald-500/10 hover:text-emerald-300"
    >
      <Trophy className="size-3.5" aria-hidden="true" />
      {label}
    </Button>
  );
}
