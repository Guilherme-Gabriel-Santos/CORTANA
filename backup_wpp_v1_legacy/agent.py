from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions, ChatContext, llm
from livekit.plugins import noise_cancellation, google
from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION
from mem0 import AsyncMemoryClient
import logging
import os
import asyncio
import webbrowser
import subprocess
from urllib.parse import quote_plus
import urllib.request as _urllib

# ── WhatsApp Integration ──────────────────────────────────────────────────────
from whatsapp_runtime import send_whatsapp_message, get_whatsapp_status
import whatsapp_bridge as _wpp_bridge
# ─────────────────────────────────────────────────────────────────────────────

try:
    import yt_dlp
    YT_DLP_DISPONIVEL = True
except ImportError:
    YT_DLP_DISPONIVEL = False

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_DISPONIVEL = True
except ImportError:
    PLAYWRIGHT_DISPONIVEL = False

from automacao_cortana import CortanaControl

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# CHROME + CDP
# ─────────────────────────────────────────

def _get_chrome_path():
    caminhos = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in caminhos:
        if os.path.exists(c):
            return c
    return None

CHROME_PATH = _get_chrome_path()
CDP_URL = "http://localhost:9222"

def _cdp_disponivel() -> bool:
    """Verifica se o Chrome já está rodando com depuração remota."""
    try:
        with _urllib.urlopen(f"{CDP_URL}/json/version", timeout=1) as r:
            return r.status == 200
    except:
        return False

async def _abrir_chrome_com_cdp(url: str = "about:blank"):
    """Abre o Chrome com porta de depuração (CDP) e navega para a URL."""
    if not CHROME_PATH:
        webbrowser.open(url)
        return False
    # Se o Chrome já está aberto COM cdp, só abre nova aba
    if _cdp_disponivel():
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(CDP_URL)
                page = await browser.contexts[0].new_page()
                await page.goto(url)
                await browser.disconnect()
            return True
        except:
            pass
    # Fecha o Chrome e reabre com depuração
   # subprocess.run(["taskkill", "/f", "/im", "chrome.exe"], capture_output=True)
    await asyncio.sleep(1)
    subprocess.Popen([CHROME_PATH, f"--remote-debugging-port=9222", url])
    await asyncio.sleep(2.5)
    return _cdp_disponivel()


# ── WhatsApp Proactive Speech Adapter ──────────────────────────────────────────
class _SpeechAdapter:
    """Adapter para expor say() no agente LiveKit via sessão."""
    def __init__(self, session):
        self._session = session

    async def say(self, text: str, add_to_chat: bool = True):
        try:
            logger.info(f"[SPEECH ADAPTER] Falando: {text[:50]}")
            # No LiveKit Agents 1.5, session.say() é o padrão para fala proativa
            await self._session.say(text, add_to_chat=add_to_chat)
        except Exception as e:
            logger.error(f"[SPEECH ADAPTER] Erro ao falar: {e}")

    async def speak_proactive_message(self, text: str, label: str = "", add_to_chat_ctx: bool = True):
        await self.say(text, add_to_chat=add_to_chat_ctx)
# ──────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────
# AGENTE
# ─────────────────────────────────────────

class Assistant(Agent, llm.ToolContext):
    def __init__(self, chat_ctx: ChatContext = None, session: AgentSession = None):
        super().__init__(
            instructions=AGENT_INSTRUCTION,
            llm=google.beta.realtime.RealtimeModel(
                voice="Aoede",
                temperature=0.7,
            ),
            chat_ctx=chat_ctx,
        )
        self.cortana_control = CortanaControl()
        self._session = session
        
        # Atributos de suporte para integração WhatsApp
        self._last_whatsapp_contact: str | None = None
        self._last_whatsapp_message: str | None = None
        self._whatsapp_history: list = []

    def remember_whatsapp_message(self, contact: str, text: str) -> None:
        """Registra mensagem recebida no histórico interno."""
        import time as _time
        entry = {
            "contact": contact,
            "text": text,
            "at": _time.strftime("%H:%M:%S"),
        }
        self._whatsapp_history.append(entry)
        self._last_whatsapp_contact = contact
        self._last_whatsapp_message = text
        # Mantém apenas as últimas 50 mensagens
        if len(self._whatsapp_history) > 50:
            self._whatsapp_history = self._whatsapp_history[-50:]

    # ────────────────────────────────
    # MÍDIA E WEB
    # ────────────────────────────────

    @agents.function_tool
    async def pesquisar_na_web(self, consulta: str, tipo: str = "google"):
        """
        Faz uma busca ou abre o YouTube.
        tipo = 'google' → busca no Google
        tipo = 'youtube' → abre a busca no YouTube (não inicia um vídeo automaticamente)
        tipo = 'url' → abre a URL diretamente
        """
        try:
            if tipo.lower() == "youtube":
                # Abre a BUSCA no YouTube, não um vídeo aleatório
                url = f"https://www.youtube.com/results?search_query={quote_plus(consulta)}"
                await _abrir_chrome_com_cdp(url)
                return f"Abrindo busca do YouTube por '{consulta}'."

            elif tipo.lower() == "url":
                await _abrir_chrome_com_cdp(consulta)
                return f"Abrindo: {consulta}"

            else: # google (padrão)
                url = f"https://www.google.com/search?q={quote_plus(consulta)}"
                await _abrir_chrome_com_cdp(url)
                return f"Pesquisando '{consulta}' no Google."
        except Exception as e:
            return f"Erro na pesquisa: {e}"

    @agents.function_tool
    async def pausar_retomar_youtube(self):
        """Pausa ou retoma o vídeo do YouTube que estiver tocando no Chrome."""
        try:
            # Estratégia 1: Keyboard shortcut via pygetwindow (mais confiável)
            try:
                import pygetwindow as gw
                import pyautogui
                import time

                # Procura janelas do Chrome que contenham "YouTube"
                janelas_yt = [w for w in gw.getAllWindows()
                              if "youtube" in w.title.lower() and w.visible]

                if janelas_yt:
                    janela = janelas_yt[0]
                    janela.activate()   # traz o Chrome para frente
                    time.sleep(0.4)     # aguarda o foco
                    pyautogui.press("k")  # 'K' = play/pause no YouTube
                    return "Play/Pause alternado no YouTube ✓"
            except ImportError:
                pass  # pygetwindow/pyautogui não instalados, tenta CDP

            # Estratégia 2: CDP (só funciona se Chrome foi aberto com --remote-debugging-port)
            if PLAYWRIGHT_DISPONIVEL and _cdp_disponivel():
                async with async_playwright() as p:
                    browser = await p.chromium.connect_over_cdp(CDP_URL)
                    for ctx in browser.contexts:
                        for page in ctx.pages:
                            if "youtube.com/watch" in page.url:
                                await page.evaluate(
                                    "const v = document.querySelector('video'); if(v) { v.paused ? v.play() : v.pause(); }"
                                )
                                await browser.disconnect()
                                return "Play/Pause alternado via CDP ✓"
                    await browser.disconnect()
                return "Nenhum vídeo do YouTube encontrado no Chrome."

            return ("Não foi possível controlar o YouTube. "
                    "Instale pygetwindow e pyautogui: pip install pygetwindow pyautogui")
        except Exception as e:
            return f"Erro no controle de mídia: {e}"

    @agents.function_tool
    async def fechar_programa(self, programa: str):
        """Fecha um programa pelo nome (ex: 'chrome', 'notepad', 'spotify')."""
        exe = programa if programa.lower().endswith(".exe") else f"{programa}.exe"
        res = subprocess.run(["taskkill", "/f", "/im", exe], capture_output=True)
        if res.returncode == 0:
            return f"Programa '{programa}' fechado com sucesso."
        return f"Não foi possível fechar '{programa}'. Verifique o nome do processo."

    @agents.function_tool
    async def abrir_programa(self, comando: str):
        """Abre um programa ou executável pelo nome ou caminho (ex: 'notepad', 'calc')."""
        try:
            subprocess.Popen(comando, shell=True)
            return f"'{comando}' aberto."
        except Exception as e:
            return f"Erro ao abrir '{comando}': {e}"

    # ────────────────────────────────
    # ARQUIVOS E PASTAS
    # ────────────────────────────────

    @agents.function_tool
    async def tocar_musica(self, musica: str):
        """
        Toca uma música, artista ou álbum no Spotify.
        Exemplo: 'Back in Black', 'Alok', 'Imagine Dragons'.
        """
        return self.cortana_control.tocar_musica_spotify(musica)

    @agents.function_tool
    async def abrir_aplicativo(self, nome_app: str):
        """Abre aplicativos conhecidos ou busca no sistema (ex: 'spotify', 'vscode', 'calculadora')."""
        return self.cortana_control.abrir_aplicativo(nome_app)

    @agents.function_tool
    async def criar_pasta(self, caminho: str):
        """Cria uma pasta."""
        return self.cortana_control.cria_pasta(caminho)

    @agents.function_tool
    async def deletar_item(self, caminho: str):
        """Deleta um arquivo ou pasta."""
        return self.cortana_control.deletar_arquivo(caminho)

    @agents.function_tool
    async def limpar_diretorio(self, caminho: str):
        """Limpa uma pasta."""
        return self.cortana_control.limpar_diretorio(caminho)

    @agents.function_tool
    async def mover_item(self, origem: str, destino: str):
        """Move um item."""
        return self.cortana_control.mover_item(origem, destino)

    @agents.function_tool
    async def copiar_item(self, origem: str, destino: str):
        """Copia um item."""
        return self.cortana_control.copiar_item(origem, destino)

    @agents.function_tool
    async def renomear_item(self, caminho: str, novo_nome: str):
        """Renomeia um item."""
        return self.cortana_control.renomear_item(caminho, novo_nome)

    @agents.function_tool
    async def organizar_pasta(self, caminho: str):
        """Organiza arquivos por tipo."""
        return self.cortana_control.organizar_pasta(caminho)

    @agents.function_tool
    async def compactar_pasta(self, caminho: str):
        """Compacta uma pasta."""
        return self.cortana_control.compactar_pasta(caminho)

    @agents.function_tool
    async def abrir_pasta(self, nome_pasta: str):
        """Abre uma pasta pelo nome."""
        return self.cortana_control.abrir_pasta(nome_pasta)

    @agents.function_tool
    async def buscar_e_abrir_arquivo(self, nome_arquivo: str):
        """Busca e abre um arquivo."""
        return self.cortana_control.buscar_e_abrir_arquivo(nome_arquivo)

    @agents.function_tool
    async def controle_volume(self, nivel: int):
        """Ajusta o volume (0-100)."""
        return self.cortana_control.controle_volume(nivel)

    @agents.function_tool
    async def controle_brilho(self, nivel: int):
        """Ajusta o brilho (0-100)."""
        return self.cortana_control.controle_brilho(nivel)

    @agents.function_tool
    async def energia_pc(self, acao: str):
        """Energia: 'desligar', 'reiniciar', 'bloquear'."""
        return self.cortana_control.energia_pc(acao)

    # ────────────────────────────────
    # WHATSAPP (Voz e Proativo)
    # ────────────────────────────────

    @agents.function_tool
    async def conectar_whatsapp(self) -> str:
        """Conecta ao WhatsApp via QR Code no navegador."""
        logger.info("[WPP TOOL] Iniciando conexão...")
        if not self._session:
            return "Erro: Sessão não inicializada."
        speech_adapter = _SpeechAdapter(self._session)
        success, message = await _wpp_bridge.connect_whatsapp(self, speech_adapter)
        return message

    @agents.function_tool
    async def enviar_whatsapp(self, contato: str, mensagem: str) -> str:
        """Envia uma mensagem de texto para um contato."""
        if not _wpp_bridge.is_whatsapp_connected():
            return "WhatsApp desconectado. Diga 'conecta meu whatsapp'."
        logger.info(f"[WPP TOOL] Enviando para {contato}...")
        result = await send_whatsapp_message(contato, mensagem)
        return "Enviado!" if result.get("success") else f"Erro: {result.get('message')}"

    @agents.function_tool
    async def status_whatsapp(self) -> str:
        """Verifica se o WhatsApp está conectado."""
        if _wpp_bridge.is_whatsapp_connected():
            res = await get_whatsapp_status()
            return "Conectado" if res.get("connected") else "Offline"
        return "Não conectado"

    @agents.function_tool
    async def desconectar_whatsapp(self) -> str:
        """Encerra o bridge do WhatsApp."""
        await _wpp_bridge.disconnect_whatsapp()
        return "WhatsApp desconectado."


# ─────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────

async def entrypoint(ctx: agents.JobContext):

    mem0_client = AsyncMemoryClient()
    user_id = "Guilherme"

    await ctx.connect()

    session = AgentSession()
    agent = Assistant(chat_ctx=ChatContext(), session=session)

    await session.start(
        room=ctx.room,
        agent=agent,
        room_input_options=RoomInputOptions(
            video_enabled=True,
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    # ── Carregar Memória de Longo Prazo ─────────────────
    # NOTA: Na API v2 do Mem0, user_id vai dentro de 'filters'
    memoria_str = ""
    try:
        logger.info(f"[Mem0] Carregando memórias para '{user_id}'...")
        response = await mem0_client.search(
            query="histórico, preferências e informações pessoais do usuário",
            filters={"user_id": user_id},
            limit=5,
        )
        # O retorno da v2 pode ser dict com "results" ou lista direta
        if isinstance(response, dict):
            results = response.get("results", [])
        elif isinstance(response, list):
            results = response
        else:
            results = []

        logger.info(f"[Mem0] {len(results)} memórias encontradas.")

        if results:
            memorias = []
            for r in results:
                texto = None
                if isinstance(r, dict):
                    texto = r.get("memory") or r.get("text") or r.get("content")
                if texto:
                    memorias.append(f"- {texto}")

            if memorias:
                bloco = "\n".join(memorias)
                memoria_str = f"\n\n[MEMÓRIAS DO USUÁRIO]\n{bloco}"
                logger.info(f"[Mem0] {len(memorias)} memórias carregadas com sucesso.")
    except Exception as e:
        logger.error(f"[Mem0] Erro ao carregar memória: {e}")

    # ── Salvar Memória ao Desligar ───────────────────────
    async def shutdown_hook():
        # Encerrar WhatsApp ao fechar o agente
        logger.info("[SHUTDOWN] Encerrando bridge do WhatsApp...")
        _wpp_bridge.stop_monitor()
        _wpp_bridge.stop_bridge_process()

        try:
            msgs = []
            for item in session._agent.chat_ctx.items:
                if not hasattr(item, "content") or not item.content:
                    continue
                if item.role not in ("user", "assistant"):
                    continue
                conteudo = "".join(item.content) if isinstance(item.content, list) else str(item.content)
                conteudo = conteudo.strip()
                if conteudo:
                    msgs.append({"role": item.role, "content": conteudo})
            if msgs:
                await mem0_client.add(msgs, user_id=user_id)
                logger.info(f"[Mem0] {len(msgs)} mensagens salvas na memória.")
        except Exception as e:
            logger.warning(f"[Mem0] Erro ao salvar memória: {e}")

    ctx.add_shutdown_callback(shutdown_hook)

    await session.generate_reply(
        instructions=SESSION_INSTRUCTION + "\nCumprimente o usuário de forma natural e confiante." + memoria_str
    )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
