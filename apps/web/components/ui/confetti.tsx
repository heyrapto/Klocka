"use client";

/**
 * Confetti — zero-dependency canvas confetti effect.
 *
 * Renders a full-viewport <canvas> overlaid on top of the page.
 * Particles are animated with requestAnimationFrame and cleaned up
 * automatically once they all exit the viewport. The canvas is
 * pointer-events:none so it never blocks interaction below.
 */

import { useEffect, useRef } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  /** Rotation in radians */
  rotation: number;
  /** Rotation velocity (rad/frame) */
  rv: number;
  /** Width of the rectangular flake */
  w: number;
  /** Height of the rectangular flake */
  h: number;
  color: string;
  /** Opacity [0, 1] */
  opacity: number;
  /** Scale factor for wobble */
  wobble: number;
  /** Wobble speed */
  ws: number;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const COLORS = [
  "#34d399", // emerald-400
  "#38bdf8", // sky-400
  "#a78bfa", // violet-400
  "#fb923c", // orange-400
  "#f472b6", // pink-400
  "#facc15", // yellow-400
  "#e2e8f0", // slate-200
];

const GRAVITY = 0.18;
const DRAG = 0.995;
const PARTICLE_COUNT = 180;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function rand(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function createParticle(canvasW: number): Particle {
  return {
    x: rand(canvasW * 0.2, canvasW * 0.8),
    y: rand(-80, -10),
    vx: rand(-3.5, 3.5),
    vy: rand(-8, -2),
    rotation: rand(0, Math.PI * 2),
    rv: rand(-0.12, 0.12),
    w: rand(6, 14),
    h: rand(3, 7),
    color: COLORS[Math.floor(Math.random() * COLORS.length)]!,
    opacity: 1,
    wobble: rand(0, Math.PI * 2),
    ws: rand(0.04, 0.12),
  };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface ConfettiProps {
  /** Whether the confetti burst is active */
  active: boolean;
}

export function Confetti({ active }: ConfettiProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);
  const particlesRef = useRef<Particle[]>([]);

  useEffect(() => {
    if (!active) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Size canvas to viewport
    function resize() {
      if (!canvas) return;
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener("resize", resize);

    // Seed particles
    particlesRef.current = Array.from({ length: PARTICLE_COUNT }, () =>
      createParticle(canvas.width),
    );

    function draw() {
      if (!canvas || !ctx) return;

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      let alive = 0;

      for (const p of particlesRef.current) {
        // Physics
        p.vy += GRAVITY;
        p.vx *= DRAG;
        p.vy *= DRAG;
        p.x += p.vx;
        p.y += p.vy;
        p.rotation += p.rv;
        p.wobble += p.ws;

        // Fade out near bottom
        if (p.y > canvas.height * 0.75) {
          p.opacity = Math.max(0, p.opacity - 0.02);
        }

        if (p.y < canvas.height + 20 && p.opacity > 0) {
          alive++;
          ctx.save();
          ctx.globalAlpha = p.opacity;
          ctx.translate(p.x, p.y);
          ctx.rotate(p.rotation);
          // Wobble: scale x so the flake "flips"
          ctx.scale(Math.cos(p.wobble), 1);
          ctx.fillStyle = p.color;
          ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
          ctx.restore();
        }
      }

      if (alive > 0) {
        rafRef.current = requestAnimationFrame(draw);
      } else {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    }

    rafRef.current = requestAnimationFrame(draw);

    return () => {
      window.removeEventListener("resize", resize);
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [active]);

  if (!active) return null;

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      style={{
        position: "fixed",
        inset: 0,
        width: "100%",
        height: "100%",
        pointerEvents: "none",
        zIndex: 9999,
      }}
    />
  );
}
