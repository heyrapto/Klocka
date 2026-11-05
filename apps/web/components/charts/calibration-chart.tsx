import { cn } from "@/lib/cn";
import type { CalibrationPoint } from "@/lib/types";

export function CalibrationChart({
  points,
  className,
}: {
  points: CalibrationPoint[];
  className?: string;
}) {
  const size = 300;
  const padding = 30;
  const plot = size - padding * 2;
  const x = (value: number) => padding + value * plot;
  const y = (value: number) => padding + plot - value * plot;
  const maxCount = Math.max(...points.map((point) => point.sampleCount), 1);

  const line = points
    .slice()
    .sort((a, b) => a.predicted - b.predicted)
    .map((point, index) => `${index === 0 ? "M" : "L"}${x(point.predicted).toFixed(1)},${y(point.observed).toFixed(1)}`)
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${size} ${size}`}
      className={cn("h-auto w-full max-w-[300px]", className)}
      role="img"
      aria-label="Reliability diagram comparing predicted probability with observed frequency"
    >
      <rect
        x={padding}
        y={padding}
        width={plot}
        height={plot}
        fill="#0c0c0e"
        stroke="#27272a"
      />
      {[0.25, 0.5, 0.75].map((tick) => (
        <g key={tick}>
          <line
            x1={x(tick)}
            x2={x(tick)}
            y1={padding}
            y2={padding + plot}
            stroke="#1f1f23"
          />
          <line
            x1={padding}
            x2={padding + plot}
            y1={y(tick)}
            y2={y(tick)}
            stroke="#1f1f23"
          />
        </g>
      ))}
      <line
        x1={x(0)}
        y1={y(0)}
        x2={x(1)}
        y2={y(1)}
        stroke="#52525b"
        strokeDasharray="4 4"
      />
      <path
        d={line}
        fill="none"
        stroke="#38bdf8"
        strokeWidth="1.75"
        strokeLinejoin="round"
      />
      {points.map((point) => (
        <circle
          key={point.bucket}
          cx={x(point.predicted)}
          cy={y(point.observed)}
          r={2.5 + (point.sampleCount / maxCount) * 3.5}
          fill="#0c0c0e"
          stroke="#38bdf8"
          strokeWidth="1.5"
        />
      ))}
      {[0, 0.5, 1].map((tick) => (
        <g key={tick}>
          <text
            x={x(tick)}
            y={size - 8}
            textAnchor="middle"
            fill="#71717a"
            fontSize="9"
            fontFamily="var(--font-geist-mono)"
          >
            {tick.toFixed(1)}
          </text>
          <text
            x={padding - 8}
            y={y(tick) + 3}
            textAnchor="end"
            fill="#71717a"
            fontSize="9"
            fontFamily="var(--font-geist-mono)"
          >
            {tick.toFixed(1)}
          </text>
        </g>
      ))}
      <text
        x={padding + plot / 2}
        y={size - 22}
        textAnchor="middle"
        fill="#52525b"
        fontSize="8"
        letterSpacing="1.2"
      >
        PREDICTED
      </text>
      <text
        x={10}
        y={padding + plot / 2}
        textAnchor="middle"
        fill="#52525b"
        fontSize="8"
        letterSpacing="1.2"
        transform={`rotate(-90 10 ${padding + plot / 2})`}
      >
        OBSERVED
      </text>
    </svg>
  );
}
