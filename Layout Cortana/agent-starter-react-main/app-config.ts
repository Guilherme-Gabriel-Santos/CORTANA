export interface AppConfig {
  pageTitle: string;
  pageDescription: string;
  companyName: string;

  supportsChatInput: boolean;
  supportsVideoInput: boolean;
  supportsScreenShare: boolean;
  isPreConnectBufferEnabled: boolean;

  logo: string;
  startButtonText: string;
  accent?: string;
  logoDark?: string;
  accentDark?: string;

  // agent dispatch configuration
  agentName?: string;

  // LiveKit Cloud Sandbox configuration
  sandboxId?: string;

  // audio visualizer configuration
  audioVisualizerType?: 'aura' | 'wave' | 'grid' | 'radial' | 'bar';
  audioVisualizerColor?: string;
  audioVisualizerAuraColorShift?: number;
  audioVisualizerWaveLineWidth?: number;
  audioVisualizerGridRowCount?: number;
  audioVisualizerGridColumnCount?: number;
  audioVisualizerRadialBarCount?: number;
  audioVisualizerRadialRadius?: number;
  audioVisualizerBarCount?: number;
}

export const APP_CONFIG_DEFAULTS: AppConfig = {
  companyName: 'Cortana',
  pageTitle: 'Cortana - Assistente de Voz',
  pageDescription: 'Sua assistente de voz inteligente e pessoal, sempre pronta para ajudar.',

  supportsChatInput: true,
  supportsVideoInput: true,
  supportsScreenShare: true,
  isPreConnectBufferEnabled: true,

  logo: '/lk-logo.svg',
  accent: '#BC13FE',
  logoDark: '/lk-logo-dark.svg',
  accentDark: '#7B2FBE',
  startButtonText: 'Iniciar Chamada',
  
  audioVisualizerType: 'aura',
  audioVisualizerColor: '#BC13FE', // Neon Purple
  audioVisualizerAuraColorShift: 0.5,

  // agent dispatch configuration
  agentName: process.env.AGENT_NAME ?? undefined,

  // LiveKit Cloud Sandbox configuration
  sandboxId: undefined,
};
