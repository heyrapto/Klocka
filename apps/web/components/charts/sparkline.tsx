import { cn } from "@/lib/cn";

const TONE_STROKE = {
  info: "#38bdf8",
  positive: "#34d399",
  negative: "#fb7185",
  caution: "#fbbf24",
  neutral: "#a1a1aa",
} as const;

export function Sparkline({
  values,
  id,
  width = 120,
  height = 36,
  tone = "info",
  showArea = true,
  className,
  label,
}: {
  values: number[];
  id: string;
  width?: number;
  height?: number;
  tone?: keyof typeof TONE_STROKE;
  showArea?: boolean;
  className?: string;
  label?: string;
}) {
  if (values.length < 2) return null;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pad = 3;
  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - pad - ((value - min) / range) * (height - pad * 2);
    return [x, y] as const;
  });
  const line = points.map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const area = `${line} L${width},${height} L0,${height} Z`;
  const stroke = TONE_STROKE[tone];
  const gradientId = `spark-${id.replace(/[^a-zA-Z0-9_-]/g, "")}`;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={cn("overflow-visible", className)}
      role="img"
      aria-label={label ?? "Trend sparkline"}
      preserveAspectRatio="none"
    >
      {showArea ? (
        <>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
              <stop offset="100%" stopColor={stroke} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#${gradientId})`} />
        </>
      ) : null}
      <path
        d={line}
        fill="none"
        stroke={stroke}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      <circle
        cx={points[points.length - 1]?.[0]}
        cy={points[points.length - 1]?.[1]}
        r="2"
        fill={stroke}
      />
    </svg>
  );
}
