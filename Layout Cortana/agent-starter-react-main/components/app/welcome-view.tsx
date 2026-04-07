import { Button } from '@/components/ui/button';

function WelcomeImage() {
  return (
    <div className="relative flex items-center justify-center mb-10 mt-6 size-32">
      {/* Outer spinning ring */}
      <div 
        className="absolute inset-0 rounded-full border border-purple-500/30 border-t-purple-500 animate-spin glow-icon" 
        style={{ animationDuration: '3s' }} 
      />
      {/* Middle reverse spinning ring */}
      <div 
        className="absolute inset-2 rounded-full border border-purple-400/20 border-b-purple-400 animate-spin glow-icon" 
        style={{ animationDuration: '2s', animationDirection: 'reverse' }} 
      />
      {/* Inner pulsing core */}
      <div className="absolute inset-6 rounded-full bg-purple-600/30 blur-md animate-pulse" />
      <div className="absolute inset-8 rounded-full bg-purple-500/60 shadow-[0_0_25px_15px_rgba(188,19,254,0.3)]" />
    </div>
  );
}

interface WelcomeViewProps {
  startButtonText: string;
  onStartCall: () => void;
}

export const WelcomeView = ({
  startButtonText,
  onStartCall,
  ref,
}: React.ComponentProps<'div'> & WelcomeViewProps) => {
  return (
    <div ref={ref} className="flex min-h-svh w-full items-center justify-center bg-background">
      <section className="cyber-panel flex flex-col items-center justify-center text-center p-12 px-16 relative overflow-hidden">
        {/* Decorative corner brackets */}
        <div className="absolute top-0 left-0 w-8 h-8 border-t-2 border-l-2 border-purple-500/50" />
        <div className="absolute top-0 right-0 w-8 h-8 border-t-2 border-r-2 border-purple-500/50" />
        <div className="absolute bottom-0 left-0 w-8 h-8 border-b-2 border-l-2 border-purple-500/50" />
        <div className="absolute bottom-0 right-0 w-8 h-8 border-b-2 border-r-2 border-purple-500/50" />

        <div className="text-xs text-purple-400/70 font-mono tracking-widest mb-2 uppercase">
          [ SYS_STATUS: IDLE ]
        </div>
        
        <WelcomeImage />

        <h1 className="neon-text text-3xl font-bold text-white font-mono tracking-widest mb-2 uppercase">
          CORTANA
        </h1>
        <p className="text-purple-300/80 max-w-prose pt-1 pb-6 leading-6 font-mono text-sm tracking-widest uppercase">
          Interface Inteligente Pronta
        </p>

        <Button
          size="lg"
          onClick={onStartCall}
          className="mt-4 w-72 h-12 rounded-sm bg-purple-700/80 hover:bg-purple-500 border border-purple-400/50 shadow-[0_0_15px_rgba(188,19,254,0.4)] text-white font-mono text-xs font-bold tracking-[0.2em] uppercase transition-all duration-300"
        >
          {startButtonText}
        </Button>
      </section>

      <div className="fixed bottom-6 left-0 flex w-full items-center justify-center pointer-events-none">
        <p className="text-purple-500/40 font-mono text-xs tracking-widest uppercase">
          // Awaiting connection protocol
        </p>
      </div>
    </div>
  );
};
