"use client";

/**
 * PredictionSuccessContext
 *
 * React context + hook pair that lets any Client Component trigger the
 * celebratory PredictionSuccessModal without prop-drilling.
 *
 * Usage — wrap your page or shell in <PredictionSuccessProvider>:
 *
 *   <PredictionSuccessProvider>
 *     {children}
 *   </PredictionSuccessProvider>
 *
 * Then call the hook wherever a resolution event is detected:
 *
 *   const { celebrate } = usePredictionSuccess();
 *   celebrate({ forecast, market });  // market is optional
 *
 * The provider renders the modal and confetti as a portal at the bottom of
 * its own subtree so it stays above all other content without requiring a
 * separate root-level portal target.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

import { PredictionSuccessModal } from "@/components/ui/prediction-success-modal";
import type { Forecast, Market } from "@/lib/types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface CelebratePayload {
  forecast: Forecast;
  /** Optional — modal degrades gracefully without it */
  market?: Market;
}

export interface PredictionSuccessContextValue {
  /** Call this to open the celebratory modal */
  celebrate: (payload: CelebratePayload) => void;
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const PredictionSuccessContext = createContext<PredictionSuccessContextValue | null>(
  null,
);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function PredictionSuccessProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [payload, setPayload] = useState<CelebratePayload | null>(null);
  const [open, setOpen] = useState(false);

  const celebrate = useCallback((incoming: CelebratePayload) => {
    setPayload(incoming);
    setOpen(true);
  }, []);

  const handleClose = useCallback(() => {
    setOpen(false);
    // Keep payload mounted during close animation; clear after.
    setTimeout(() => setPayload(null), 400);
  }, []);

  const value = useMemo<PredictionSuccessContextValue>(
    () => ({ celebrate }),
    [celebrate],
  );

  return (
    <PredictionSuccessContext.Provider value={value}>
      {children}

      {payload !== null ? (
        <PredictionSuccessModal
          open={open}
          onClose={handleClose}
          forecast={payload.forecast}
          market={payload.market}
        />
      ) : null}
    </PredictionSuccessContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Returns `{ celebrate }` from the nearest `<PredictionSuccessProvider>`.
 *
 * Throws if called outside the provider so callers fail fast during
 * development rather than silently swallowing events.
 */
export function usePredictionSuccess(): PredictionSuccessContextValue {
  const ctx = useContext(PredictionSuccessContext);
  if (ctx === null) {
    if (process.env.NODE_ENV !== "production") {
      console.warn(
        "usePredictionSuccess was invoked outside of a <PredictionSuccessProvider>.",
      );
    }
    return {
      celebrate: () => {},
    };
  }
  return ctx;
}
