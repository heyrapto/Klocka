import { cn } from "@/lib/cn";

export function BrierChart({
  data,
  height = 240,
  className,
}: {
  data: ReadonlyArray<{ label: string; model: number; market: number }>;
  height?: number;
  className?: string;
}) {
  const width = 680;
  const padding = { top: 16, right: 16, bottom: 28, left: 40 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  const values = data.flatMap((point) => [point.model, point.market]);
  const min = Math.max(0, Math.floor((Math.min(...values) - 0.01) * 100) / 100);
  const max = Math.ceil((Math.max(...values) + 0.01) * 100) / 100;
  const range = max - min || 0.1;

  const x = (index: number) =>
    padding.left + (index / Math.max(1, data.length - 1)) * plotWidth;
  const y = (value: number) =>
    padding.top + plotHeight - ((value - min) / range) * plotHeight;

  const path = (key: "model" | "market") =>
    data
      .map((point, index) => `${index === 0 ? "M" : "L"}${x(index).toFixed(1)},${y(point[key]).toFixed(1)}`)
      .join(" ");

  const ticks = Array.from({ length: 5 }, (_, index) => min + (range * index) / 4);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={cn("h-auto w-full", className)}
      role="img"
      aria-label="Brier score over time: model versus market baseline"
    >
      <defs>
        <linearGradient id="brier-model-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.18" />
          <stop offset="100%" stopColor="#38bdf8" stopOpacity="0" />
        </linearGradient>
      </defs>

      {ticks.map((tick) => (
        <g key={tick}>
          <line
            x1={padding.left}
            x2={width - padding.right}
            y1={y(tick)}
            y2={y(tick)}
            stroke="#27272a"
            strokeDasharray="3 4"
            strokeWidth="1"
          />
          <text
            x={padding.left - 8}
            y={y(tick) + 3}
            textAnchor="end"
            fill="#71717a"
            fontSize="9"
            fontFamily="var(--font-geist-mono)"
          >
            {tick.toFixed(3)}
          </text>
        </g>
      ))}

      <path
        d={`${path("model")} L${x(data.length - 1)},${padding.top + plotHeight} L${padding.left},${padding.top + plotHeight} Z`}
        fill="url(#brier-model-area)"
      />
      <path
        d={path("market")}
        fill="none"
        stroke="#71717a"
        strokeWidth="1.5"
        strokeDasharray="5 4"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      <path
        d={path("model")}
        fill="none"
        stroke="#38bdf8"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />

      {data.map((point, index) => (
        <g key={point.label}>
          <circle cx={x(index)} cy={y(point.model)} r="2.5" fill="#38bdf8" />
          <text
            x={x(index)}
            y={height - 8}
            textAnchor="middle"
            fill="#71717a"
            fontSize="9"
            fontFamily="var(--font-geist-mono)"
          >
            {point.label}
          </text>
        </g>
      ))}
    </svg>
  );
}

export function BrierLegend() {
  return (
    <div className="flex items-center gap-4 text-[11px] text-zinc-500">
      <span className="inline-flex items-center gap-1.5">
        <span className="h-0.5 w-4 rounded-full bg-sky-400" aria-hidden="true" />
        Model
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span
          className="h-0.5 w-4 rounded-full border-t border-dashed border-zinc-500"
          aria-hidden="true"
        />
        Market baseline
      </span>
    </div>
  );
}
