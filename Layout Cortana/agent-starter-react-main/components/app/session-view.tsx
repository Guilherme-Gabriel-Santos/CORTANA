'use client';

import React, { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {
  useSessionContext,
  useSessionMessages,
  useRemoteParticipants,
  useVoiceAssistant,
  useTrackVolume
} from '@livekit/components-react';
import type { AppConfig } from '@/app-config';
import {
  AgentControlBar,
  type AgentControlBarControls,
} from '@/components/agents-ui/agent-control-bar';
import { TileLayout } from '@/components/app/tile-layout';
import { cn } from '@/lib/shadcn/utils';
import { Shimmer } from '../ai-elements/shimmer';
import { 
  Cloud, 
  FileText, 
  Search, 
  BarChart, 
  Settings, 
  Twitter, 
  Mail, 
  Music, 
  MessageSquare,
  User,
  Activity
} from 'lucide-react';

const MotionBottom = motion.create('div');
const MotionMessage = motion.create(Shimmer);

const BOTTOM_VIEW_MOTION_PROPS = {
  variants: {
    visible: { opacity: 1, translateY: '0%' },
    hidden: { opacity: 0, translateY: '100%' },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
  transition: { duration: 0.3, delay: 0.5, ease: 'easeOut' as const },
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

// --- Sub-componente para controle de performance da Orb ---
const VantaController = ({ vantaRef, gameMode }: { vantaRef: React.MutableRefObject<any>, gameMode: boolean }) => {
  const { audioTrack } = useVoiceAssistant();
  const volume = useTrackVolume(audioTrack);

  useEffect(() => {
    const effect = vantaRef.current;
    if (!effect) return;

    // Atualizar Chaos conforme Volume (Reatividade à voz)
    const baseChaos = 3.0;
    const voiceChaos = volume * 7.0;
    const finalChaos = baseChaos + voiceChaos;

    if (Math.abs(effect.options.chaos - finalChaos) > 0.05) {
      effect.setOptions({ chaos: finalChaos });
    }
  }, [volume, vantaRef, gameMode]);

  return null;
};

// --- Componente Modular da Orb com seu próprio ciclo de vida ---
const VantaOrb = ({ agentPersona, currentColor, gameMode, vantaRef }: { agentPersona: string; currentColor: number; gameMode: boolean; vantaRef: React.MutableRefObject<any> }) => {
  const localRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let vantaEffect: any = null;
    let attempts = 0;
    let initTimer: NodeJS.Timeout;

    if (gameMode) {
      if (vantaRef.current) {
        vantaRef.current.destroy();
        vantaRef.current = null;
      }
      return;
    }

    const tryInitVanta = () => {
      const el = localRef.current;
      const win = window as any;
      const hasVanta = !!win.VANTA?.TRUNK;
      const hasP5 = !!win.p5;

      if (el && hasVanta && hasP5) {
        try {
          // A cor agora vem via prop
          vantaEffect = win.VANTA.TRUNK({
            el: el,
            p5: win.p5,
            mouseControls: false,
            touchControls: false,
            gyroControls: false,
            minHeight: 200.0,
            minWidth: 200.0,
            scale: 1.0,
            scaleMobile: 1.0,
            color: currentColor,
            backgroundColor: 0x000000,
            backgroundAlpha: 0,
            spacing: 0.0,
            chaos: 3.0,
          });
          vantaRef.current = vantaEffect;

          // Apply transparency directly to the canvas element that Vanta/p5 creates
          setTimeout(() => {
            const canvas = el?.querySelector('canvas');
            if (canvas) {
              canvas.style.mixBlendMode = 'screen';
              canvas.style.background = 'transparent';
            }
          }, 200);
        } catch (e) {
          console.error('Vanta Orb Init Error:', e);
          attempts++;
          if (attempts < 10) initTimer = setTimeout(tryInitVanta, 500);
        }
      } else {
        attempts++;
        if (attempts < 50) initTimer = setTimeout(tryInitVanta, 100);
      }
    };

    tryInitVanta();

    return () => {
      clearTimeout(initTimer);
      if (vantaEffect) {
        try {
          if (vantaRef.current === vantaEffect) {
            vantaRef.current = null;
          }
          vantaEffect.destroy();
        } catch (e) { }
      }
    };
  }, [gameMode, agentPersona, currentColor]);

  if (gameMode) return null;

  return (
    <div
      ref={localRef}
      className="w-[1000px] h-[1000px] transition-opacity duration-1000"
      style={{
        transform: 'scale(0.75) translateY(-15%)',
        transformOrigin: 'center center',
        mixBlendMode: 'screen',
        opacity: gameMode ? 0 : 1
      }}
    />
  );
};

interface SessionViewProps {
  appConfig: AppConfig;
  onManualDisconnect?: () => void;
}

const SystemMonitor = ({ metrics }: { metrics: any }) => {
  const items = [
    { label: 'CPU_LOAD', val: metrics.cpu || 0, color: 'text-cyan-400', barColor: 'bg-cyan-500' },
    { label: 'MTX_RAM', val: metrics.ram || 0, color: 'text-purple-400', barColor: 'bg-purple-500' },
    { label: 'SSD_IO', val: metrics.disk || 0, color: 'text-fuchsia-400', barColor: 'bg-fuchsia-500' },
    { label: 'GPU_PROC', val: metrics.gpu || 0, color: 'text-blue-400', barColor: 'bg-blue-500' },
  ];

  return (
    <div className="space-y-4 mt-2">
      {items.map((item, i) => (
        <div key={i} className="flex flex-col gap-1.5 p-2 rounded bg-white/5 border border-white/10 relative overflow-hidden group">
          <div className="flex justify-between items-center z-10">
            <span className={cn("text-[9px] font-bold tracking-[0.2em]", item.color)}>{item.label}</span>
            <span className="text-[10px] font-mono text-white/70">{item.val}%</span>
          </div>
          <div className="h-1.5 w-full bg-white/5 rounded-full overflow-hidden border border-white/5 z-10">
            <motion.div 
              initial={{ width: 0 }}
              animate={{ width: `${item.val}%` }}
              className={cn("h-full shadow-[0_0_8px_currentColor]", item.barColor)}
              style={{ color: 'inherit' }}
            />
          </div>
          {/* Scanline effect */}
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/5 to-transparent -translate-x-full group-hover:animate-[scan_2s_infinite]" />
        </div>
      ))}
      <style jsx>{`
        @keyframes scan {
          100% { transform: translateX(100%); }
        }
      `}</style>
    </div>
  );
};

export const SessionView = ({
  appConfig,
  onManualDisconnect,
  ...props
}: React.ComponentProps<'section'> & SessionViewProps) => {
  const session = useSessionContext();
  const { messages } = useSessionMessages(session);
  const [chatOpen, setChatOpen] = useState(false);
  const [theaterMode, setTheaterMode] = useState(false);
  const [gameMode, setGameMode] = useState(false);
  const [metrics, setMetrics] = useState({ cpu: 0, ram: 0, disk: 0, gpu: 0 });
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const vantaEffectRef = useRef<any>(null);

  // Escuta métricas e sinais via Data Channel
  useEffect(() => {
    if (!session.room) return;

    const handleData = (payload: Uint8Array, participant: any) => {
      try {
        const str = new TextDecoder().decode(payload);
        const json = JSON.parse(str);
        if (json.type === 'metrics') {
          setMetrics(json.data);
        } else if (json.type === 'game_mode') {
          setGameMode(json.active);
        }
      } catch (e) {
        // Ignora erros de parsing
      }
    };

    session.room.on('dataReceived', handleData);
    return () => {
      session.room?.off('dataReceived', handleData);
    };
  }, [session.room]);

  // Monitora participantes para detectar Persona
  const participants = useRemoteParticipants();
  const agentParticipant = participants.find((p: any) => !p.isLocal);
  const agentPersona = agentParticipant?.attributes?.['agent_persona'] || 'cortana';

  // Definição de Cores
  const PERSONA_COLORS = {
    alice: 0xff69b4,
    jarvis: 0x1da3b9,
    cortana: 0x7b2fbe,
  };
  const currentColor =
    PERSONA_COLORS[agentPersona as keyof typeof PERSONA_COLORS] || PERSONA_COLORS.cortana;

  useEffect(() => {
    const loadScript = (src: string): Promise<boolean> => {
      return new Promise((resolve) => {
        if (typeof document === 'undefined') return resolve(false);
        if (document.querySelector(`script[src="${src}"]`)) return resolve(true);
        const script = document.createElement('script');
        script.src = src;
        script.async = true;
        script.onload = () => resolve(true);
        script.onerror = () => resolve(false);
        document.body.appendChild(script);
      });
    };

    const setup = async () => {
      await loadScript('https://cdnjs.cloudflare.com/ajax/libs/p5.js/1.4.0/p5.min.js');
      await loadScript('https://cdn.jsdelivr.net/npm/vanta@0.5.24/dist/vanta.trunk.min.js');
    };
    setup();
  }, []);

  const controls: AgentControlBarControls = {
    leave: true,
    microphone: true,
    chat: appConfig.supportsChatInput,
    camera: appConfig.supportsVideoInput,
    screenShare: appConfig.supportsScreenShare,
  };

  useEffect(() => {
    const lastMessage = messages.at(-1);
    const lastMessageIsLocal = lastMessage?.from?.isLocal === true;
    if (scrollAreaRef.current && lastMessageIsLocal) {
      scrollAreaRef.current.scrollTop = scrollAreaRef.current.scrollHeight;
    }
  }, [messages]);

  const handleDisconnect = () => {
    if (onManualDisconnect) onManualDisconnect();
    try {
      if (session.end) session.end();
    } catch (e) {
      console.warn("Erro ao desconectar sessão:", e);
    }
  };

  return (
    <section
      className="relative flex h-svh w-svw flex-col bg-[#05050A] p-2 md:p-4 font-mono overflow-hidden text-gray-300"
      {...props}
    >
      <VantaController vantaRef={vantaEffectRef} gameMode={gameMode} />
      <div className="cyber-frame w-full h-full flex flex-col md:flex-row relative z-10 rounded-xl">
        
        {/* TOP BAR / HEADER STYLING */}
        <div className="absolute top-0 left-0 right-0 h-16 flex items-center justify-between px-6 pointer-events-none z-50">
           <div className={cn("flex items-center gap-4 cyber-panel px-4 py-2 mt-2 pointer-events-auto shadow-lg transition-opacity duration-500", theaterMode && "opacity-20 hover:opacity-100")}>
             <div className="w-8 h-8 rounded-full bg-purple-600/20 border border-purple-500/50 flex items-center justify-center glow-icon">
               <Activity className="w-5 h-5 text-purple-400" />
             </div>
             <div className="flex flex-col">
                <span className="neon-text font-bold text-lg text-purple-100 tracking-[0.2em] uppercase leading-none">SISTEMA</span>
                {gameMode && <span className="text-[7px] text-cyan-400 font-bold tracking-[0.3em] mt-1 animate-pulse">MODE: LOW_POWER</span>}
             </div>
           </div>
           
           <div className="hidden md:flex items-center gap-6 cyber-panel px-6 py-2 mt-2 pointer-events-auto shadow-lg">
             <div className="flex items-center gap-2">
               <div className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse shadow-[0_0_8px_#0ff]" />
               <span className="text-[10px] text-cyan-200 uppercase tracking-[0.3em] font-bold">Conectado</span>
             </div>
             <div className="h-4 w-[1px] bg-purple-500/30" />
             
             {/* BOTÃO MODO TEATRO */}
             <button 
                onClick={() => setTheaterMode(!theaterMode)}
                className="flex items-center gap-2 text-[10px] text-purple-300 uppercase tracking-widest font-bold hover:text-cyan-400 transition-colors group"
             >
               <span className="hidden lg:inline">{theaterMode ? 'Sair do Modo Foco' : 'Modo Teatro'}</span>
               <div className="w-8 h-8 rounded border border-purple-500/30 flex items-center justify-center group-hover:border-cyan-500/50 shadow-inner">
                  {theaterMode ? <Activity className="w-4 h-4" /> : <BarChart className="w-4 h-4" />}
               </div>
             </button>
           </div>
        </div>

        <div className={cn(
          "hidden md:flex flex-col pt-20 pb-6 px-4 border-r border-purple-500/30 z-20 bg-black/40 backdrop-blur-md transition-all duration-700 ease-in-out overflow-hidden shadow-[10px_0_30px_rgba(0,0,0,0.5)]",
          theaterMode ? "w-0 px-0 opacity-0 border-transparent translate-x-[-100%]" : "w-72 opacity-100"
        )}>
          <div className="cyber-panel p-4 mb-6 relative overflow-hidden group">
            <div className="absolute top-0 left-0 w-1 h-full bg-purple-500 shadow-[0_0_15px_#bc13fe]" />
            <h3 className="text-[10px] font-bold text-purple-400 uppercase tracking-[0.3em] mb-2 opacity-90">Memória Ativa</h3>
            <p className="text-[11px] font-medium text-purple-100/80 leading-relaxed uppercase tracking-wider">Monitorando preferências e histórico de Guilherme...</p>
          </div>

          <div className="flex-1 space-y-3 mt-2 overflow-y-auto pr-1 custom-scrollbar">
            <h3 className="text-[9px] font-bold text-cyan-400/60 uppercase tracking-[0.4em] mb-2 px-1">Performance_HUD</h3>
            <SystemMonitor metrics={metrics} />
            
            <div className="h-6" />
            
            <h3 className="text-[9px] font-bold text-purple-400/60 uppercase tracking-[0.4em] mb-2 px-1">Módulos_Operativos</h3>
            {[
              { icon: Cloud, label: 'Dashboard', val: '10 >' },
              { icon: FileText, label: 'Documentos', val: '>' },
              { icon: Search, label: 'Pesquisar IA', val: '>' },
            ].map((item, i) => (
              <div key={i} className="flex items-center justify-between p-2 rounded bg-purple-900/5 border border-purple-500/10 hover:bg-purple-900/30 hover:border-cyan-500/30 cursor-pointer transition-all duration-300 group">
                <div className="flex items-center gap-2">
                  <item.icon className="w-3.5 h-3.5 text-cyan-700 group-hover:text-cyan-400 transition-colors" />
                  <span className="text-[11px] font-medium text-purple-200/70 group-hover:text-purple-100 tracking-wide">{item.label}</span>
                </div>
                <span className="text-[9px] text-cyan-500/50 font-mono">{item.val}</span>
              </div>
            ))}
          </div>
        </div>

        {/* CENTER MAIN ORB & CAMERA FEED */}
        <div className="flex-1 relative flex flex-col items-center justify-center p-4 pt-20 z-10 overflow-hidden">
          
          {/* THE GLOWING SPHERE (VANTA TRUNK) */}
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none mix-blend-screen">
            <AnimatePresence mode="wait">
              <motion.div
                key={session.isConnected ? `vanta-${agentPersona}` : 'vanta-disconnected'}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                style={{ mixBlendMode: 'screen' }}
                transition={{ duration: 1.5, ease: "easeInOut" }}
                className="absolute inset-0 flex items-center justify-center p5-canvas-container"
              >
                <VantaOrb agentPersona={agentPersona} currentColor={currentColor} gameMode={gameMode} vantaRef={vantaEffectRef} />
              </motion.div>
            </AnimatePresence>
            
            {/* Subtle radial gradient to frame the orb */}
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,transparent_30%,#000000_100%)] opacity-80" />
          </div>

          {/* CAMERA FEED TILE LAYOUT */}
          <div className="relative z-20 w-full flex-1">
            <TileLayout chatOpen={chatOpen} />
          </div>

          {/* BOTTOM CONTROL BAR */}
          <MotionBottom
            {...BOTTOM_VIEW_MOTION_PROPS}
            className="relative z-30 w-full max-w-2xl mt-auto mb-2"
          >
            {appConfig.isPreConnectBufferEnabled && (
              <AnimatePresence>
                {messages.length === 0 && (
                  <MotionMessage
                    key="pre-connect-message"
                    duration={2}
                    aria-hidden={messages.length > 0}
                    {...SHIMMER_MOTION_PROPS}
                    className="pointer-events-none mx-auto block w-full max-w-2xl pb-4 text-center text-[10px] tracking-[0.3em] text-purple-400 font-bold neon-text uppercase"
                  >
                    SISTEMA OPERACIONAL CORTANA: AGUARDANDO COMANDO...
                  </MotionMessage>
                )}
              </AnimatePresence>
            )}

            <div className="absolute bottom-28 left-1/2 -translate-x-1/2 z-40 flex flex-col items-center pointer-events-none">
               <span className="text-[10px] text-purple-400 uppercase tracking-[0.4em] font-bold mb-2 neon-text">Microfone Ativo</span>
               <div className="w-48 h-[1px] bg-gradient-to-r from-transparent via-purple-500 to-transparent opacity-50" />
            </div>

            <div className="cyber-panel p-2 pb-3 w-full border-b-2 border-b-purple-500/50 shadow-[0_10px_40px_rgba(188,19,254,0.15)]">
              <AgentControlBar
                variant="livekit"
                controls={controls}
                isChatOpen={chatOpen}
                isConnected={true}
                onDisconnect={handleDisconnect}
                onIsChatOpenChange={setChatOpen}
              />
            </div>
          </MotionBottom>
        </div>

        <div className={cn(
          "hidden lg:flex flex-col pt-20 pb-6 px-4 border-l border-purple-500/20 z-20 bg-black/40 backdrop-blur-md transition-all duration-700 ease-in-out overflow-hidden shadow-[-10px_0_30px_rgba(0,0,0,0.5)]",
          theaterMode ? "w-0 px-0 opacity-0 border-transparent translate-x-[100%]" : "w-80 opacity-100"
        )}>
           
           <div className="cyber-panel p-4 mb-6 bg-gradient-to-br from-purple-900/20 to-transparent">
             <h3 className="text-[10px] font-bold text-purple-400 uppercase tracking-[0.3em] mb-4 flex items-center justify-between">
               <span>Comandos Rápidos</span>
               <span className="text-cyan-400/70 border border-cyan-500/30 px-1.5 rounded text-[8px]">ACTIVE</span>
             </h3>
             <div className="grid grid-cols-4 gap-3">
                {[
                  { icon: Twitter, label: 'Social' },
                  { icon: Mail, label: 'Mail' },
                  { icon: Music, label: 'Música' },
                  { icon: MessageSquare, label: 'Chat' }
                ].map((item, i) => (
                  <div key={i} className="flex flex-col items-center gap-2 group cursor-pointer">
                    <div className="w-10 h-10 rounded-md bg-purple-950/40 border border-purple-500/30 flex items-center justify-center group-hover:bg-cyan-900/40 group-hover:border-cyan-500/60 group-hover:shadow-[0_0_10px_rgba(0,255,255,0.2)] transition-all duration-300">
                      <item.icon className="w-4 h-4 text-purple-300 group-hover:text-cyan-300 glow-icon" />
                    </div>
                    <span className="text-[9px] text-purple-200/50 group-hover:text-cyan-300 uppercase tracking-widest">{item.label}</span>
                  </div>
                ))}
             </div>
           </div>

           <div className="cyber-panel p-4 flex-1 bg-gradient-to-bl from-purple-900/10 to-transparent flex flex-col overflow-hidden">
             <h3 className="text-[10px] font-bold text-purple-400 uppercase tracking-[0.3em] mb-4 flex items-center justify-between">
               <span>Histórico de Conversa</span>
               <span className="text-purple-500/70 border border-purple-500/30 px-1.5 rounded text-[8px]">SYNC</span>
             </h3>
             <div 
               ref={scrollAreaRef}
               className="space-y-3 flex-1 overflow-y-auto pr-2 custom-scrollbar p-1"
             >
               {messages.length === 0 ? (
                 <div className="flex flex-col items-center justify-center h-full opacity-30 text-center p-4">
                   <MessageSquare className="w-8 h-8 mb-2 text-purple-500" />
                   <span className="text-[10px] uppercase tracking-widest">Nenhuma atividade registrada</span>
                 </div>
               ) : (
                 messages.map((msg, i) => (
                   <div 
                     key={msg.id || i} 
                     className={cn(
                       "flex flex-col gap-1 p-3 rounded-md bg-black/40 border transition-all group",
                       msg.from?.isLocal 
                         ? "border-cyan-500/20 hover:border-cyan-500/40" 
                         : "border-purple-500/20 hover:border-purple-500/40"
                     )}
                   >
                     <div className="flex items-center gap-2 mb-1">
                        <div className={cn(
                          "w-5 h-5 rounded flex items-center justify-center border",
                          msg.from?.isLocal ? "bg-cyan-950/30 border-cyan-500/30" : "bg-purple-950/30 border-purple-500/30"
                        )}>
                          {msg.from?.isLocal ? (
                            <User className="w-3 h-3 text-cyan-400" />
                          ) : (
                            <Activity className="w-3 h-3 text-purple-400" />
                          )}
                        </div>
                        <span className={cn(
                          "text-[9px] font-bold uppercase tracking-[0.2em]",
                          msg.from?.isLocal ? "text-cyan-400" : "text-purple-400"
                        )}>
                          {msg.from?.isLocal ? "Você" : "Cortana"}
                        </span>
                        <span className="text-[8px] text-gray-600 ml-auto">
                          {new Date(msg.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                        </span>
                     </div>
                     <p className="text-[11px] text-purple-100/90 leading-relaxed font-medium">
                       {msg.message}
                     </p>
                   </div>
                 ))
               )}
             </div>
           </div>

        </div>

      </div>
      
      <style dangerouslySetInnerHTML={{__html: `
        .custom-scrollbar::-webkit-scrollbar {
          width: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
          background: rgba(0,0,0,0.2);
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
          background: rgba(123, 47, 190, 0.4);
          border-radius: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover {
          background: rgba(0, 255, 255, 0.4);
        }
      `}} />
    </section>
  );
};
