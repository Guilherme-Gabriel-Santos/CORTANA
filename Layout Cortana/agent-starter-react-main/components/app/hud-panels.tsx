'use client';

import React from 'react';
import { Brain, MessageSquare, Music2, Scan, Shield } from 'lucide-react';
import { cn } from '@/lib/shadcn/utils';

/* ==========================================================================
   SYSTEM VITALS — CPU / RAM / VRAM + mini-grid de métricas
   ========================================================================== */
export interface VitalsData {
  cpu: number;
  ram: number;
  vram: number;
  cpuTemp?: number;
  gpuTemp?: number;
  disk?: number;
  fps?: number;
  latency?: number;
  uptime?: string;
}

export const SystemVitalsPanel: React.FC<{ vitals: VitalsData }> = ({ vitals }) => {
  const bars = [
    { label: 'CPU', value: vitals.cpu, variant: 'cyan' as const },
    { label: 'RAM', value: vitals.ram, variant: 'cyan' as const },
    { label: 'VRAM', value: vitals.vram, variant: 'purple' as const },
  ];

  const metrics = [
    {
      k: 'CPU_TEMP',
      v: vitals.cpuTemp ? `${vitals.cpuTemp}°C` : '--',
      ok: (vitals.cpuTemp ?? 0) < 75,
    },
    {
      k: 'GPU_TEMP',
      v: vitals.gpuTemp ? `${vitals.gpuTemp}°C` : '--',
      ok: (vitals.gpuTemp ?? 0) < 78,
    },
    { k: 'UPTIME', v: vitals.uptime ?? '00:00', ok: true },
    { k: 'DISK', v: vitals.disk != null ? `${vitals.disk}%` : '--', ok: (vitals.disk ?? 0) < 85 },
    { k: 'FPS_HUD', v: vitals.fps ? `${vitals.fps}` : '--', ok: true },
    {
      k: 'LATENCY',
      v: vitals.latency ? `${vitals.latency}ms` : '--',
      ok: (vitals.latency ?? 0) < 80,
    },
  ];

  return (
    <div className="hud-panel">
      <div className="hud-panel-title">
        <span>System Vitals</span>
        <span className="font-hud-mono text-[9px] opacity-70">PSUTIL</span>
      </div>
      <div className="hud-panel-body space-y-3">
        {bars.map((b) => (
          <div key={b.label} className="space-y-1">
            <div className="font-hud-mono flex justify-between text-[10px]">
              <span className="tracking-[2px] text-white/60">{b.label}</span>
              <span className="text-white/90">{b.value.toFixed(0)}%</span>
            </div>
            <div className="hud-bar">
              <div
                className={cn('hud-bar-fill', b.variant === 'purple' && 'purple')}
                style={{ width: `${b.value}%` }}
              />
            </div>
          </div>
        ))}

        <div className="grid grid-cols-2 gap-1.5 border-t border-white/5 pt-2">
          {metrics.map((m) => (
            <div
              key={m.k}
              className="font-hud-mono flex justify-between border border-white/5 bg-white/[0.02] px-2 py-1 text-[10px]"
            >
              <span className="text-white/50">{m.k}</span>
              <span className={m.ok ? 'text-cyan-300' : 'text-amber-400'}>{m.v}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

/* ==========================================================================
   MEMORY STREAM — Últimas memórias do Obsidian (mock ou via data channel)
   ========================================================================== */
export interface MemoryItem {
  id: string;
  timestamp: string; // HH:MM
  content: string;
}

export const MemoryStreamPanel: React.FC<{ items: MemoryItem[] }> = ({ items }) => {
  return (
    <div className="hud-panel purple flex min-h-0 flex-1 flex-col">
      <div className="hud-panel-title">
        <span>Memory Stream</span>
        <span className="font-hud-mono text-[9px] opacity-70">OBSIDIAN</span>
      </div>
      <div className="hud-panel-body hud-scroll flex-1 space-y-1.5 overflow-y-auto">
        {items.length === 0 ? (
          <div className="py-6 text-center text-[10px] tracking-widest text-white/30 uppercase">
            <Brain className="mx-auto mb-1 h-5 w-5 opacity-40" />
            Nenhuma memória ativa
          </div>
        ) : (
          items.map((m) => (
            <div
              key={m.id}
              className="font-hud-mono flex gap-2 border-b border-white/5 py-1 text-[11px] last:border-0"
            >
              <span className="shrink-0 text-purple-400/80">{m.timestamp}</span>
              <span className="truncate text-white/80">{m.content}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

/* ==========================================================================
   FACE ID — Estado do reconhecimento facial
   ========================================================================== */
export interface FaceIdState {
  status: 'ENGAGED' | 'SCANNING' | 'LOST' | 'OFFLINE';
  confidence: number; // 0..100
  user?: string;
  distance?: number; // cm
}

export const FaceIdPanel: React.FC<{ state: FaceIdState }> = ({ state }) => {
  const statusColor =
    state.status === 'ENGAGED'
      ? 'text-green-400'
      : state.status === 'SCANNING'
        ? 'text-cyan-400'
        : state.status === 'LOST'
          ? 'text-amber-400'
          : 'text-white/40';

  return (
    <div className="hud-panel">
      <div className="hud-panel-title">
        <span>Face ID</span>
        <span className="font-hud-mono text-[9px] opacity-70">FACIAL REC</span>
      </div>
      <div className="hud-panel-body space-y-2">
        <div className="relative flex aspect-video items-center justify-center overflow-hidden border border-white/10 bg-black/40">
          <div className="pointer-events-none absolute inset-0 grid grid-cols-4 grid-rows-3">
            {Array.from({ length: 12 }).map((_, i) => (
              <div key={i} className="border border-white/5" />
            ))}
          </div>
          <Scan className={cn('h-10 w-10', statusColor, 'animate-pulse')} strokeWidth={1.2} />
          {/* Corner brackets */}
          {(['tl', 'tr', 'bl', 'br'] as const).map((pos) => (
            <div
              key={pos}
              className={cn(
                'absolute h-3 w-3 border-purple-500',
                pos === 'tl' && 'top-1 left-1 border-t-2 border-l-2',
                pos === 'tr' && 'top-1 right-1 border-t-2 border-r-2',
                pos === 'bl' && 'bottom-1 left-1 border-b-2 border-l-2',
                pos === 'br' && 'right-1 bottom-1 border-r-2 border-b-2'
              )}
            />
          ))}
        </div>
        <div className="font-hud-mono space-y-1 text-[11px]">
          <Row k="STATUS" v={state.status} highlight={statusColor} />
          <Row k="CONFIDENCE" v={`${state.confidence.toFixed(1)}%`} />
          {state.user && <Row k="USER" v={state.user} />}
          {state.distance != null && <Row k="DISTANCE" v={`${state.distance}cm`} />}
        </div>
      </div>
    </div>
  );
};

const Row: React.FC<{ k: string; v: string; highlight?: string }> = ({ k, v, highlight }) => (
  <div className="flex justify-between border-b border-dashed border-white/5 py-0.5">
    <span className="text-white/50">{k}</span>
    <span className={cn('text-white/90', highlight)}>{v}</span>
  </div>
);

/* ==========================================================================
   WHATSAPP — Contatos pendentes
   ========================================================================== */
export interface WhatsAppItem {
  id: string;
  name: string;
  preview: string;
  count: number;
}

export const WhatsAppPanel: React.FC<{ items: WhatsAppItem[] }> = ({ items }) => (
  <div className="hud-panel purple flex min-h-0 flex-1 flex-col">
    <div className="hud-panel-title">
      <span>WhatsApp</span>
      <span className="font-hud-mono text-[9px] opacity-70">
        {items.reduce((sum, i) => sum + i.count, 0)} pendentes
      </span>
    </div>
    <div className="hud-panel-body hud-scroll flex-1 space-y-1.5 overflow-y-auto">
      {items.length === 0 ? (
        <div className="py-6 text-center text-[10px] tracking-widest text-white/30 uppercase">
          <MessageSquare className="mx-auto mb-1 h-5 w-5 opacity-40" />
          Sem pendências
        </div>
      ) : (
        items.map((it) => (
          <div
            key={it.id}
            className="flex cursor-pointer items-center gap-2 border border-purple-500/20 bg-purple-500/5 px-2 py-1.5 transition-colors hover:bg-purple-500/10"
          >
            <div
              className="font-orbitron flex h-7 w-7 items-center justify-center rounded-full text-[10px] font-bold text-[#140030]"
              style={{
                background: 'linear-gradient(135deg, #00d4ff, #bc13fe)',
                boxShadow: '0 0 6px rgba(188,19,254,0.4)',
              }}
            >
              {it.name.slice(0, 2).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[12px] font-semibold text-white/90">{it.name}</div>
              <div className="font-hud-mono truncate text-[10px] text-white/50">{it.preview}</div>
            </div>
            {it.count > 0 && (
              <span className="font-hud-mono rounded-full bg-red-500 px-1.5 py-0.5 text-[9px] font-bold text-white shadow-[0_0_6px_rgba(255,71,87,0.6)]">
                {it.count}
              </span>
            )}
          </div>
        ))
      )}
    </div>
  </div>
);

/* ==========================================================================
   NOW PLAYING
   ========================================================================== */
export interface NowPlayingState {
  track?: string;
  artist?: string;
  playing: boolean;
}

export const NowPlayingPanel: React.FC<{ state: NowPlayingState }> = ({ state }) => (
  <div className="hud-panel min-w-0 flex-1">
    <div className="hud-panel-title">
      <span>Now Playing</span>
      <span className="font-hud-mono text-[9px] opacity-70">SPOTIFY</span>
    </div>
    <div className="hud-panel-body flex items-center gap-3">
      <div
        className="h-12 w-12 shrink-0 rounded-full border-2 border-purple-500"
        style={{
          background: 'radial-gradient(circle, #1a0a2a 40%, #000 100%)',
          boxShadow: '0 0 14px rgba(188,19,254,0.5)',
          animation: state.playing ? 'spin 4s linear infinite' : 'none',
        }}
      >
        <div className="mx-auto mt-5 h-2 w-2 rounded-full bg-cyan-400" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12px] font-semibold text-white/90">{state.track || '—'}</div>
        <div className="font-hud-mono truncate text-[10px] text-white/50">
          {state.artist || 'Silêncio'}
        </div>
      </div>
      <Music2 className="h-4 w-4 text-purple-400" />
      <style jsx>{`
        @keyframes spin {
          to {
            transform: rotate(360deg);
          }
        }
      `}</style>
    </div>
  </div>
);

/* ==========================================================================
   CYBER SENTRY — Log de auditoria rolando
   ========================================================================== */
export interface CyberLine {
  id: string;
  timestamp: string; // HH:MM:SS
  severity: 'ok' | 'warn' | 'err' | 'info';
  message: string;
}

export const CyberSentryPanel: React.FC<{ lines: CyberLine[] }> = ({ lines }) => (
  <div className="hud-panel min-w-0 flex-1">
    <div className="hud-panel-title">
      <span>CyberSentry</span>
      <span className="font-hud-mono text-[9px] opacity-70">AUDIT</span>
    </div>
    <div className="hud-panel-body hud-scroll max-h-28 space-y-0.5 overflow-y-auto">
      {lines.length === 0 ? (
        <div className="font-hud-mono py-2 text-center text-[10px] text-white/30">
          <Shield className="mx-auto mb-1 h-4 w-4 opacity-40" />
          Aguardando varredura
        </div>
      ) : (
        lines.map((l) => (
          <div
            key={l.id}
            className={cn(
              'font-hud-mono text-[10px] leading-relaxed',
              l.severity === 'ok' && 'text-green-400',
              l.severity === 'warn' && 'text-amber-400',
              l.severity === 'err' && 'text-red-400',
              l.severity === 'info' && 'text-white/60'
            )}
          >
            [{l.timestamp}] {l.message}
          </div>
        ))
      )}
    </div>
  </div>
);

/* ==========================================================================
   QUICK ACTIONS
   ========================================================================== */
export interface QuickAction {
  id: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  onClick?: () => void;
}

export const QuickActionsPanel: React.FC<{ actions: QuickAction[] }> = ({ actions }) => (
  <div className="hud-panel purple min-w-0 flex-1">
    <div className="hud-panel-title">
      <span>Quick Actions</span>
      <span className="font-hud-mono text-[9px] opacity-70">SHORTCUTS</span>
    </div>
    <div className="hud-panel-body grid grid-cols-4 gap-2">
      {actions.map((a) => {
        const Icon = a.icon;
        return (
          <button
            key={a.id}
            onClick={a.onClick}
            className="group flex flex-col items-center gap-1 border border-purple-500/20 bg-purple-500/5 p-2 transition-all hover:border-purple-400/60 hover:bg-purple-500/15"
          >
            <Icon className="h-4 w-4 text-purple-300 transition-colors group-hover:text-cyan-300" />
            <span className="font-hud-mono text-[9px] tracking-wider text-white/60 uppercase group-hover:text-white/90">
              {a.label}
            </span>
          </button>
        );
      })}
    </div>
  </div>
);

/* ==========================================================================
   TRANSCRIPT STRIP — duas últimas linhas do chat
   ========================================================================== */
export interface TranscriptLine {
  who: 'user' | 'cortana';
  said: string;
}

export const TranscriptStrip: React.FC<{ lines: TranscriptLine[] }> = ({ lines }) => (
  <div className="hud-panel">
    <div className="hud-panel-title">
      <span>Transcript</span>
      <span className="font-hud-mono text-[9px] opacity-70">LIVE</span>
    </div>
    <div className="hud-panel-body hud-scroll max-h-24 space-y-1 overflow-y-auto">
      {lines.length === 0 ? (
        <div className="font-hud-mono py-1 text-center text-[10px] text-white/30">
          Aguardando interação…
        </div>
      ) : (
        lines.slice(-3).map((l, i, arr) => (
          <div
            key={i}
            className={cn(
              'font-hud-mono flex gap-2 text-[11px]',
              i < arr.length - 1 && 'opacity-50'
            )}
          >
            <span
              className={cn(
                'shrink-0 font-bold tracking-[1px]',
                l.who === 'user' ? 'text-cyan-400' : 'text-purple-400'
              )}
            >
              {l.who === 'user' ? '[USR]' : '[CORTANA]'}
            </span>
            <span className="text-white/80">{l.said}</span>
          </div>
        ))
      )}
    </div>
  </div>
);
