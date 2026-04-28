'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { Cloud, Mail, MessageSquare, Music } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import { useSessionContext, useSessionMessages } from '@livekit/components-react';
import type { AppConfig } from '@/app-config';
import {
  AgentControlBar,
  type AgentControlBarControls,
} from '@/components/agents-ui/agent-control-bar';
import { Shimmer } from '../ai-elements/shimmer';
import { CortanaOrb } from './cortana-orb';
import {
  type CyberLine,
  CyberSentryPanel,
  FaceIdPanel,
  type FaceIdState,
  type MemoryItem,
  MemoryStreamPanel,
  NowPlayingPanel,
  type NowPlayingState,
  QuickActionsPanel,
  SystemVitalsPanel,
  type TranscriptLine,
  TranscriptStrip,
  type VitalsData,
  type WhatsAppItem,
  WhatsAppPanel,
} from './hud-panels';

const MotionDiv = motion.create('div');
const MotionMessage = motion.create(Shimmer);

const BOTTOM_VIEW_MOTION_PROPS = {
  variants: {
    visible: { opacity: 1, translateY: '0%' },
    hidden: { opacity: 0, translateY: '100%' },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
  transition: { duration: 0.3, delay: 0.4, ease: 'easeOut' as const },
};

const SHIMMER_MOTION_PROPS = {
  variants: {
    visible: { opacity: 1, transition: { ease: 'easeIn' as const, duration: 0.5, delay: 0.8 } },
    hidden: { opacity: 0, transition: { ease: 'easeIn' as const, duration: 0.5, delay: 0 } },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
};

interface SessionViewProps {
  appConfig: AppConfig;
  onManualDisconnect?: () => void;
}

/* ==========================================================================
   SessionView — HUD estilo Cortana (anéis ondulados + painéis laterais)
   ========================================================================== */
export const SessionView = ({
  appConfig,
  onManualDisconnect,
  ...props
}: React.ComponentProps<'section'> & SessionViewProps) => {
  const session = useSessionContext();
  const { messages } = useSessionMessages(session);
  const [chatOpen, setChatOpen] = useState(false);
  const [gameMode, setGameMode] = useState(false);

  // Vitals vindos do data channel (preenchido pelo backend via psutil)
  const [vitals, setVitals] = useState<VitalsData>({
    cpu: 0,
    ram: 0,
    vram: 0,
    cpuTemp: 0,
    gpuTemp: 0,
    disk: 0,
    fps: 0,
    latency: 0,
    uptime: '00:00',
  });

  // Streams de dados HUD — podem ser populados via data channel
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [whatsapp, setWhatsapp] = useState<WhatsAppItem[]>([]);
  const [faceId, setFaceId] = useState<FaceIdState>({
    status: 'SCANNING',
    confidence: 0,
  });
  const [nowPlaying, setNowPlaying] = useState<NowPlayingState>({
    playing: false,
  });
  const [cyberLog, setCyberLog] = useState<CyberLine[]>([]);

  /* ---- Data channel listener ------------------------------------------- */
  useEffect(() => {
    if (!session.room) return;

    const handleData = (payload: Uint8Array) => {
      try {
        const str = new TextDecoder().decode(payload);
        const json = JSON.parse(str);
        switch (json.type) {
          case 'metrics':
            setVitals((v) => ({ ...v, ...json.data }));
            break;
          case 'game_mode':
            setGameMode(!!json.active);
            break;
          case 'memory_stream':
            setMemories(json.data ?? []);
            break;
          case 'whatsapp':
            setWhatsapp(json.data ?? []);
            break;
          case 'face_id':
            setFaceId(json.data);
            break;
          case 'now_playing':
            setNowPlaying(json.data);
            break;
          case 'cyber_log':
            setCyberLog((prev) => [json.data, ...prev].slice(0, 40));
            break;
        }
      } catch {
        /* payload não-JSON, ignora */
      }
    };

    session.room.on('dataReceived', handleData);
    return () => {
      session.room?.off('dataReceived', handleData);
    };
  }, [session.room]);

  /* ---- Vitals fake quando não há backend emitindo dados ----------------- */
  useEffect(() => {
    const hasBackendFeed = vitals.cpu > 0 || vitals.ram > 0;
    if (hasBackendFeed) return;
    const id = setInterval(() => {
      setVitals((v) => ({
        ...v,
        cpu: 20 + Math.random() * 55,
        ram: 35 + Math.random() * 45,
        vram: 30 + Math.random() * 50,
        cpuTemp: 45 + Math.round(Math.random() * 30),
        gpuTemp: 50 + Math.round(Math.random() * 30),
        disk: 45 + Math.round(Math.random() * 20),
        fps: 58 + Math.round(Math.random() * 4),
        latency: 30 + Math.round(Math.random() * 40),
      }));
    }, 2200);
    return () => clearInterval(id);
  }, [vitals.cpu, vitals.ram]);

  /* ---- Transcript derivado das mensagens LiveKit ------------------------ */
  const transcript: TranscriptLine[] = useMemo(() => {
    return messages.map((m) => ({
      who: m.from?.isLocal ? 'user' : 'cortana',
      said: m.message ?? '',
    }));
  }, [messages]);

  /* ---- Controles da barra ---------------------------------------------- */
  const controls: AgentControlBarControls = {
    leave: true,
    microphone: true,
    chat: appConfig.supportsChatInput,
    camera: appConfig.supportsVideoInput,
    screenShare: appConfig.supportsScreenShare,
  };

  const handleDisconnect = () => {
    if (onManualDisconnect) onManualDisconnect();
    try {
      if (session.end) session.end();
    } catch (e) {
      console.warn('Erro ao desconectar sessão:', e);
    }
  };

  /* ---- Quick Actions (placeholder, pode vir do config) ------------------ */
  const quickActions = useMemo(
    () => [
      { id: 'dash', label: 'Dash', icon: Cloud },
      { id: 'mail', label: 'Mail', icon: Mail },
      { id: 'music', label: 'Música', icon: Music },
      { id: 'chat', label: 'Chat', icon: MessageSquare },
    ],
    []
  );

  return (
    <section
      className="hud-root font-hud-mono relative h-svh w-svw overflow-hidden text-[#e8f4ff]"
      {...props}
    >
      {/* Overlays decorativos */}
      <div className="hud-scanlines" />
      <div className="hud-vignette" />
      <div className="hud-sweep" />

      {/* Grid principal: 3 colunas + linha bottom strip */}
      <div
        className="relative z-10 grid h-full gap-3 p-3 md:gap-4 md:p-5"
        style={{
          gridTemplateColumns: 'minmax(260px, 320px) 1fr minmax(260px, 320px)',
          gridTemplateRows: 'minmax(0, 1fr) auto auto',
          gridTemplateAreas: `
            "left center right"
            "bottom bottom bottom"
            "transcript transcript transcript"
          `,
        }}
      >
        {/* LEFT COLUMN */}
        <div className="flex min-h-0 flex-col gap-3 md:gap-4" style={{ gridArea: 'left' }}>
          <SystemVitalsPanel vitals={vitals} />
          <MemoryStreamPanel items={memories} />
        </div>

        {/* CENTER — ORB + control bar */}
        <div
          className="relative flex min-h-0 flex-col items-center justify-center"
          style={{ gridArea: 'center' }}
        >
          <div className="flex w-full flex-1 items-center justify-center">
            <CortanaOrb size={440} disabled={gameMode} />
          </div>

          {/* Control bar */}
          <MotionDiv {...BOTTOM_VIEW_MOTION_PROPS} className="relative mb-2 w-full max-w-2xl">
            {appConfig.isPreConnectBufferEnabled && (
              <AnimatePresence>
                {messages.length === 0 && (
                  <MotionMessage
                    key="pre-connect-message"
                    duration={2}
                    aria-hidden={messages.length > 0}
                    {...SHIMMER_MOTION_PROPS}
                    className="neon-text pointer-events-none mx-auto block w-full max-w-2xl pb-3 text-center text-[10px] font-bold tracking-[0.3em] text-purple-400 uppercase"
                  >
                    Cortana online · aguardando comando
                  </MotionMessage>
                )}
              </AnimatePresence>
            )}

            <div className="hud-panel purple p-2 pb-2.5">
              <AgentControlBar
                variant="livekit"
                controls={controls}
                isChatOpen={chatOpen}
                isConnected={true}
                onDisconnect={handleDisconnect}
                onIsChatOpenChange={setChatOpen}
              />
            </div>
          </MotionDiv>
        </div>

        {/* RIGHT COLUMN */}
        <div className="flex min-h-0 flex-col gap-3 md:gap-4" style={{ gridArea: 'right' }}>
          <FaceIdPanel state={faceId} />
          <WhatsAppPanel items={whatsapp} />
        </div>

        {/* BOTTOM STRIP: Now Playing | CyberSentry | Quick Actions */}
        <div className="flex gap-3 md:gap-4" style={{ gridArea: 'bottom' }}>
          <NowPlayingPanel state={nowPlaying} />
          <CyberSentryPanel lines={cyberLog} />
          <QuickActionsPanel actions={quickActions} />
        </div>

        {/* TRANSCRIPT */}
        <div style={{ gridArea: 'transcript' }}>
          <TranscriptStrip lines={transcript} />
        </div>
      </div>

      {/* Game mode overlay */}
      {gameMode && (
        <div className="font-hud-mono absolute bottom-4 left-1/2 z-50 -translate-x-1/2 animate-pulse text-[10px] tracking-[0.3em] text-cyan-400">
          ◆ GAME MODE · LOW POWER ◆
        </div>
      )}
    </section>
  );
};

export default SessionView;
