import { cn } from "@/lib/cn";

export function PriceChart({
  values,
  labels,
  height = 220,
  className,
  id,
}: {
  values: number[];
  labels?: [string, string];
  height?: number;
  className?: string;
  id: string;
}) {
  const width = 720;
  const padding = { top: 18, right: 18, bottom: 22, left: 42 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const min = Math.max(0, Math.min(...values) - 0.04);
  const max = Math.min(1, Math.max(...values) + 0.04);
  const range = max - min || 0.1;

  const points = values.map((value, index) => {
    const x = padding.left + (index / Math.max(1, values.length - 1)) * plotWidth;
    const y = padding.top + plotHeight - ((value - min) / range) * plotHeight;
    return [x, y] as const;
  });
  const line = points
    .map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");
  const area = `${line} L${padding.left + plotWidth},${padding.top + plotHeight} L${padding.left},${padding.top + plotHeight} Z`;
  const ticks = Array.from({ length: 4 }, (_, index) => min + (range * index) / 3);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className={cn("h-auto w-full", className)}
      role="img"
      aria-label="Market-implied YES probability over the last twelve observations"
    >
      <defs>
        <linearGradient id={`price-${id}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#38bdf8" stopOpacity="0" />
        </linearGradient>
      </defs>
      {ticks.map((tick) => {
        const y = padding.top + plotHeight - ((tick - min) / range) * plotHeight;
        return (
          <g key={tick}>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={y}
              y2={y}
              stroke="#1f1f23"
              strokeDasharray="3 4"
            />
            <text
              x={padding.left - 8}
              y={y + 3}
              textAnchor="end"
              fill="#71717a"
              fontSize="9"
              fontFamily="var(--font-geist-mono)"
            >
              {(tick * 100).toFixed(0)}¢
            </text>
          </g>
        );
      })}
      <path d={area} fill={`url(#price-${id})`} />
      <path
        d={line}
        fill="none"
        stroke="#38bdf8"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {points.map(([x, y], index) =>
        index === points.length - 1 ? (
          <g key={index}>
            <circle cx={x} cy={y} r="4" fill="#09090b" stroke="#38bdf8" strokeWidth="2" />
          </g>
        ) : null,
      )}
      {labels ? (
        <>
          <text
            x={padding.left}
            y={height - 6}
            fill="#52525b"
            fontSize="9"
            fontFamily="var(--font-geist-mono)"
          >
            {labels[0]}
          </text>
          <text
            x={width - padding.right}
            y={height - 6}
            textAnchor="end"
            fill="#52525b"
            fontSize="9"
            fontFamily="var(--font-geist-mono)"
          >
            {labels[1]}
          </text>
        </>
      ) : null}
    </svg>
  );
}
