'use client';

import React, { useEffect, useRef } from 'react';
import { useTrackVolume, useVoiceAssistant } from '@livekit/components-react';

interface CortanaOrbProps {
  /** Tamanho do canvas em pixels CSS. */
  size?: number;
  /** Raio do buraco central em pixels. */
  innerRadius?: number;
  /** Raio externo em pixels. */
  outerRadius?: number;
  /** Quantidade de anéis concêntricos. */
  ringCount?: number;
  /** Resolução angular (segmentos por anel). */
  segments?: number;
  /** Se desabilitado, o canvas não renderiza (economia em modo low-power). */
  disabled?: boolean;
}

/**
 * Orb Cortana — anéis concêntricos ondulados em canvas, reativos ao
 * volume da voz do agente (`useTrackVolume`). Sem dependências externas
 * (substitui Vanta TRUNK + p5).
 */
export const CortanaOrb: React.FC<CortanaOrbProps> = ({
  size = 440,
  innerRadius = 70,
  outerRadius = 195,
  ringCount = 70,
  segments = 220,
  disabled = false,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number | null>(null);
  const volumeRef = useRef<number>(0);

  // Audio reactivity via LiveKit
  const { audioTrack } = useVoiceAssistant();
  const volume = useTrackVolume(audioTrack);

  // Espelha volume em ref pra ser lido dentro do rAF sem disparar re-render
  useEffect(() => {
    volumeRef.current = volume;
  }, [volume]);

  useEffect(() => {
    if (disabled) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    canvas.style.width = size + 'px';
    canvas.style.height = size + 'px';
    ctx.scale(dpr, dpr);

    const cx = size / 2;
    const cy = size / 2;

    // Seeds por anel pra dar variedade
    const seeds: number[] = [];
    for (let i = 0; i < ringCount; i++) seeds.push(Math.random() * 1000);

    let t = 0;

    const draw = () => {
      ctx.clearRect(0, 0, size, size);
      ctx.lineWidth = 0.8;

      // Volume amplifica amplitude (0..1 LiveKit → multiplicador 1..3.5)
      const volBoost = 1 + Math.min(volumeRef.current, 1) * 2.5;

      for (let r = 0; r < ringCount; r++) {
        const p = r / (ringCount - 1);
        const baseR = innerRadius + p * (outerRadius - innerRadius);
        const seed = seeds[r];

        const bell = Math.sin(p * Math.PI); // 0..1..0
        const alpha = 0.18 + bell * 0.55;
        const hueMix = 275 + bell * 15; // 275..290
        const light = 35 + bell * 22; // 35..57%
        ctx.strokeStyle = `hsla(${hueMix}, 95%, ${light}%, ${alpha})`;

        ctx.beginPath();
        for (let i = 0; i <= segments; i++) {
          const a = (i / segments) * Math.PI * 2;

          const wave =
            Math.sin(a * 6 + t * 0.9 + seed) * 3.2 +
            Math.sin(a * 11 - t * 0.6 + seed * 0.5) * 2.1 +
            Math.sin(a * 3 + t * 0.5 + seed * 0.7) * 4.5 +
            Math.sin(a * 17 + t * 1.2 + seed * 0.3) * 1.3;

          const amp = (0.5 + bell * 1.1) * volBoost;
          const radius = baseR + wave * amp;

          const x = cx + Math.cos(a) * radius;
          const y = cy + Math.sin(a) * radius;
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.closePath();
        ctx.stroke();
      }

      // Avança tempo um pouco mais rápido quando está ouvindo voz
      t += 0.018 + Math.min(volumeRef.current, 1) * 0.04;
      rafRef.current = requestAnimationFrame(draw);
    };

    draw();

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [size, innerRadius, outerRadius, ringCount, segments, disabled]);

  if (disabled) return null;

  return (
    <div
      className="relative flex items-center justify-center"
      style={{ width: size, height: size }}
    >
      {/* Halo difuso roxo atrás do canvas */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-5 rounded-full"
        style={{
          background:
            'radial-gradient(circle at 50% 50%, rgba(188,19,254,0.28) 0%, rgba(188,19,254,0.12) 35%, rgba(90,26,158,0.08) 60%, transparent 80%)',
          filter: 'blur(14px)',
          animation: 'hud-orb-glow 3.6s ease-in-out infinite',
        }}
      />
      <canvas
        ref={canvasRef}
        className="relative z-10"
        style={{
          filter:
            'drop-shadow(0 0 12px rgba(188,19,254,0.5)) drop-shadow(0 0 24px rgba(188,19,254,0.25))',
        }}
      />
      <style jsx>{`
        @keyframes hud-orb-glow {
          0%,
          100% {
            transform: scale(1);
            opacity: 0.9;
          }
          50% {
            transform: scale(1.06);
            opacity: 1;
          }
        }
      `}</style>
    </div>
  );
};

export default CortanaOrb;
