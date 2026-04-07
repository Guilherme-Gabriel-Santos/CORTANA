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
import psutil
import json
import glob
import time
from datetime import datetime
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
            logger.info(f"[SPEECH ADAPTER] Falando via LLM: {text[:50]}")
            # Em vez de session.say (que exige TTS), usamos generate_reply 
            # para que o Gemini Realtime fale a notificação naturalmente.
            await self._session.generate_reply(
                instructions=f"Sua tarefa agora é apenas repassar esta notificação para o usuário de forma curta e natural: {text}",
                add_to_chat_ctx=add_to_chat
            )
        except Exception as e:
            logger.error(f"[SPEECH ADAPTER] Erro ao falar: {e}")

    async def speak_proactive_message(self, text: str, label: str = "", add_to_chat_ctx: bool = True):
        pass # await self.say(text, add_to_chat=add_to_chat_ctx) # COMENTADO por estabilidade
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
                temperature=0.6,
            ),
            chat_ctx=chat_ctx,
        )
        self.cortana_control = CortanaControl()
        self._session = session
        
        # Atributos de suporte para integração WhatsApp
        self._last_whatsapp_contact: str | None = None
        self._last_whatsapp_message: str | None = None
        self._whatsapp_history: list = []
        self._game_mode: bool = False # Estado do Modo Game
        
        # Controle de Proatividade (JARVIS-style)
        self._pending_whatsapp_messages: dict[str, list[str]] = {} # contato -> lista de msgs
        self._last_whatsapp_notif: dict[str, float] = {} # contato -> timestamp

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
        """Busca no Google, YouTube ou abre uma URL."""
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
        """Pausa ou retoma o vídeo do YouTube no Chrome."""
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
        """Encerra um programa pelo nome (ex: chrome, spotify)."""
        exe = programa if programa.lower().endswith(".exe") else f"{programa}.exe"
        res = subprocess.run(["taskkill", "/f", "/im", exe], capture_output=True)
        if res.returncode == 0:
            return f"Programa '{programa}' fechado com sucesso."
        return f"Não foi possível fechar '{programa}'. Verifique o nome do processo."

    @agents.function_tool
    async def abrir_programa(self, comando: str):
        """Executa um comando ou abre um programa."""
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
        """Toca música, artista ou álbum no Spotify."""
        return self.cortana_control.tocar_musica_spotify(musica)

    @agents.function_tool
    async def abrir_aplicativo(self, nome_app: str):
        """Abre aplicativos instalados (ex: vscode, spotify)."""
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

    @agents.function_tool
    async def wake_on_lan(self, mac_address: str):
        """Envia um pacote Wake-on-LAN para ligar um dispositivo (PC, TV)."""
        return self.cortana_control.wake_on_lan(mac_address)

    @agents.function_tool
    async def controle_tv_lg(self, ip: str, acao: str):
        """Controla Smart TVs LG (WebOS). Ações: 'desligar', 'mute', 'unmute'."""
        return self.cortana_control.controle_tv_lg(ip, acao)

    @agents.function_tool
    async def controle_tv_samsung(self, ip: str, acao: str):
        """Controla Smart TVs Samsung (Tizen). Ações: 'desligar', 'volume_up', 'volume_down'."""
        return self.cortana_control.controle_tv_samsung(ip, acao)

    @agents.function_tool
    async def controle_dispositivo_broadlink(self, ip: str, acao: str):
        """Controla dispositivos via Broadlink (Ar Condicionado, etc). Ações: 'aprender'."""
        return self.cortana_control.controle_dispositivo_broadlink(ip, acao)

    @agents.function_tool
    async def controle_tv_tcl(self, ip: str, acao: str):
        """Controla Smart TVs TCL (Android TV) via protocolo V2 (PIN). Ações: 'ligar', 'desligar', 'confirmar', 'home', 'vol_up', 'vol_down'."""
        return await self.cortana_control.controle_tv_tcl(ip, acao)

    # ────────────────────────────────
    # WHATSAPP (Voz e Proativo)
    # ────────────────────────────────

    @agents.function_tool
    async def conectar_whatsapp(self) -> str:
        """Inicia conexão com o WhatsApp (gera QR Code)."""
        logger.info("[WPP TOOL] Iniciando conexão...")
        if not self._session:
            return "Erro: Sessão não inicializada."
        speech_adapter = _SpeechAdapter(self._session)
        success, message = await _wpp_bridge.connect_whatsapp(self, speech_adapter)
        return message

    @agents.function_tool
    async def enviar_whatsapp(self, contato: str, mensagem: str) -> str:
        """Envia mensagem de texto para um contato."""
        if not await _wpp_bridge.is_whatsapp_connected_async():
            return "WhatsApp desconectado. Diga 'conecta meu whatsapp'."
        logger.info(f"[WPP TOOL] Enviando para {contato}...")
        result = await send_whatsapp_message(contato, mensagem)
        return "Enviado!" if result.get("success") else f"Erro: {result.get('message')}"

    @agents.function_tool
    async def status_whatsapp(self) -> str:
        """Verifica se o WhatsApp está conectado."""
        if await _wpp_bridge.is_whatsapp_connected_async():
            res = await get_whatsapp_status()
            return "Conectado" if res.get("connected") else "Offline"
        return "Não conectado"

    @agents.function_tool
    async def aprender_fato(self, fato: str) -> str:
        """Salva preferências ou fatos novos sobre o usuário na memória de longo prazo."""
        try:
            mem0_client = AsyncMemoryClient()
            user_id = "Guilherme"
            await mem0_client.add([{"role": "user", "content": fato}], user_id=user_id)
            logger.info(f"[MEM0] Fato aprendido: {fato}")
            return f"Fato memorizado: '{fato}'"
        except Exception as e:
            return f"Erro ao memorizar fato: {e}"

    @agents.function_tool
    async def pesquisar_no_passado(self, termo: str) -> str:
        """Busca palavras-chave em conversas passadas guardadas localmente."""
        try:
            arquivos = glob.glob("memory/episodic/*.json")
            if not arquivos:
                return "Ainda não tenho registros no meu histórico local."
            
            resumos = []
            # Ordena por mais recente
            arquivos.sort(reverse=True)
            
            for arq in arquivos[:10]: # Limita aos últimos 10 logs de sessão
                with open(arq, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Busca simples por palavra-chave
                    if termo.lower() in str(data).lower():
                        msgs = data.get("messages", [])
                        # Pega o trecho relevante (aprox 3 mensagens ao redor)
                        for i, m in enumerate(msgs):
                            if termo.lower() in m.get("content", "").lower():
                                context = msgs[max(0, i-1):min(len(msgs), i+2)]
                    await browser.disconnect()
                return "Nenhum vídeo do YouTube encontrado no Chrome."

            return ("Não foi possível controlar o YouTube. "
                    "Instale pygetwindow e pyautogui: pip install pygetwindow pyautogui")
        except Exception as e:
            return f"Erro no controle de mídia: {e}"

    @agents.function_tool
    async def fechar_programa(self, programa: str):
        """Encerra um programa pelo nome (ex: chrome, spotify)."""
        exe = programa if programa.lower().endswith(".exe") else f"{programa}.exe"
        res = subprocess.run(["taskkill", "/f", "/im", exe], capture_output=True)
        if res.returncode == 0:
            return f"Programa '{programa}' fechado com sucesso."
        return f"Não foi possível fechar '{programa}'. Verifique o nome do processo."

    @agents.function_tool
    async def abrir_programa(self, comando: str):
        """Executa um comando ou abre um programa."""
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
        """Toca música, artista ou álbum no Spotify."""
        return self.cortana_control.tocar_musica_spotify(musica)

    @agents.function_tool
    async def abrir_aplicativo(self, nome_app: str):
        """Abre aplicativos instalados (ex: vscode, spotify)."""
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

    @agents.function_tool
    async def wake_on_lan(self, mac_address: str):
        """Envia um pacote Wake-on-LAN para ligar um dispositivo (PC, TV)."""
        return self.cortana_control.wake_on_lan(mac_address)

    @agents.function_tool
    async def controle_tv_lg(self, ip: str, acao: str):
        """Controla Smart TVs LG (WebOS). Ações: 'desligar', 'mute', 'unmute'."""
        return self.cortana_control.controle_tv_lg(ip, acao)

    @agents.function_tool
    async def controle_tv_samsung(self, ip: str, acao: str):
        """Controla Smart TVs Samsung (Tizen). Ações: 'desligar', 'volume_up', 'volume_down'."""
        return self.cortana_control.controle_tv_samsung(ip, acao)

    @agents.function_tool
    async def controle_dispositivo_broadlink(self, ip: str, acao: str):
        """Controla dispositivos via Broadlink (Ar Condicionado, etc). Ações: 'aprender'."""
        return self.cortana_control.controle_dispositivo_broadlink(ip, acao)

    @agents.function_tool
    async def controle_tv_tcl(self, ip: str, acao: str):
        """Controla Smart TVs TCL (Android TV) via protocolo V2 (PIN). Ações: 'ligar', 'desligar', 'confirmar', 'home', 'vol_up', 'vol_down'."""
        return await self.cortana_control.controle_tv_tcl(ip, acao)

    # ── Lógica de Notificações Inteligentes ───────────────────
    
    async def handle_whatsapp_notif(self, contact: str, text: str):
        """Decide se deve interromper o usuário por voz ou apenas acumular a mensagem."""
        now = time.time()
        last_time = self._last_whatsapp_notif.get(contact, 0)
        
        # Registra no histórico geral
        self.remember_whatsapp_message(contact, text)
        
        # Adiciona à fila de pendentes para resumo posterior
        if contact not in self._pending_whatsapp_messages:
            self._pending_whatsapp_messages[contact] = []
        self._pending_whatsapp_messages[contact].append(text)
        
        # Lógica de Interrupção (Rate Limiting de 3 minutos)
        if (now - last_time) > 180: # 3 minutos
            self._last_whatsapp_notif[contact] = now
            prompt_msg = f"Chefe, o {contact} mandou uma mensagem: {text}. Quer responder?"
            
            # Chama o adaptador de fala (que deve estar habilitado)
            speech = _SpeechAdapter(self._session)
            await speech.say(prompt_msg)
            logger.info(f"[NOTIF] Usuário notificado via voz de {contact}")
        else:
            logger.info(f"[NOTIF] Mensagem de {contact} acumulada silenciosamente.")

    # ────────────────────────────────
    # WHATSAPP (Voz e Proativo)
    # ────────────────────────────────

    @agents.function_tool
    async def conectar_whatsapp(self) -> str:
        """Inicia conexão com o WhatsApp (gera QR Code)."""
        logger.info("[WPP TOOL] Iniciando conexão...")
        if not self._session:
            return "Erro: Sessão não inicializada."
        speech_adapter = _SpeechAdapter(self._session)
        success, message = await _wpp_bridge.connect_whatsapp(self, speech_adapter)
        return message

    @agents.function_tool
    async def enviar_whatsapp(self, contato: str, mensagem: str) -> str:
        """Envia mensagem de texto para um contato."""
        if not await _wpp_bridge.is_whatsapp_connected_async():
            return "WhatsApp desconectado. Diga 'conecta meu whatsapp'."
        logger.info(f"[WPP TOOL] Enviando para {contato}...")
        result = await send_whatsapp_message(contato, mensagem)
        return "Enviado!" if result.get("success") else f"Erro: {result.get('message')}"

    @agents.function_tool
    async def status_whatsapp(self) -> str:
        """Verifica se o WhatsApp está conectado."""
        if await _wpp_bridge.is_whatsapp_connected_async():
            res = await get_whatsapp_status()
            return "Conectado" if res.get("connected") else "Offline"
        return "Não conectado"

    @agents.function_tool
    async def aprender_fato(self, fato: str) -> str:
        """Salva preferências ou fatos novos sobre o usuário na memória de longo prazo."""
        try:
            mem0_client = AsyncMemoryClient()
            user_id = "Guilherme"
            await mem0_client.add([{"role": "user", "content": fato}], user_id=user_id)
            logger.info(f"[MEM0] Fato aprendido: {fato}")
            return f"Fato memorizado: '{fato}'"
        except Exception as e:
            return f"Erro ao memorizar fato: {e}"

    @agents.function_tool
    async def pesquisar_no_passado(self, termo: str) -> str:
        """Busca palavras-chave em conversas passadas guardadas localmente."""
        try:
            arquivos = glob.glob("memory/episodic/*.json")
            if not arquivos:
                return "Ainda não tenho registros no meu histórico local."
            
            resumos = []
            # Ordena por mais recente
            arquivos.sort(reverse=True)
            
            for arq in arquivos[:10]: # Limita aos últimos 10 logs de sessão
                with open(arq, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Busca simples por palavra-chave
                    if termo.lower() in str(data).lower():
                        msgs = data.get("messages", [])
                        # Pega o trecho relevante (aprox 3 mensagens ao redor)
                        for i, m in enumerate(msgs):
                            if termo.lower() in m.get("content", "").lower():
                                context = msgs[max(0, i-1):min(len(msgs), i+2)]
                                snippets = [f"[{m.get('role')}]: {m.get('content')}" for m in context]
                                resumos.append(f"Em {data.get('timestamp')}:\n" + "\n".join(snippets))
                                break
            
            if not resumos:
                return f"Não encontrei registros de '{termo}' nas minhas conversas passadas."
            
            return "--- REGISTROS ENCONTRADOS ---\n\n" + "\n\n---\n\n".join(resumos)
        except Exception as e:
            return f"Erro na pesquisa local: {e}"

    @agents.function_tool
    async def modo_game(self, ativar: bool) -> str:
        """Ativa ou desativa o Modo Game (reduz uso de CPU/GPU)."""
        self._game_mode = ativar
        logger.info(f"[MODE] Modo Game {'ATIVADO' if ativar else 'DESATIVADO'}")
        
        # Avisa o frontend via Data Channel
        try:
            msg = {"type": "game_mode", "active": ativar}
            await self._session.room.local_participant.publish_data(
                json.dumps(msg).encode('utf-8')
            )
        except Exception as e:
            logger.error(f"[MODE] Erro ao avisar frontend: {e}")
            
        res = "Modo Game ativado. Vou economizar recursos agora, Chefe." if ativar else "Modo Game desativado. Voltando ao poder total."
        return res

    @agents.function_tool
    async def resumo_whatsapp(self, contato: str = None) -> str:
        """Resume as mensagens pendentes (não lidas) de um contato específico ou de todos."""
        if not self._pending_whatsapp_messages:
            return "Não há mensagens pendentes para resumir, Chefe."
        
        target_contacts = [contato] if contato else list(self._pending_whatsapp_messages.keys())
        full_report = []
        
        for c in target_contacts:
            messages = self._pending_whatsapp_messages.get(c, [])
            if not messages: continue
            
            # Usa o próprio LLM para gerar um resumo curto
            combined_text = "\n".join(messages)
            try:
                # Limpa a fila após preparar para o resumo
                self._pending_whatsapp_messages[c] = []
                
                # Pedimos um resumo via generate_reply (ou apenas retornamos o texto para o LLM processar no contexto da tool)
                # Como é uma tool, o retorno será visto pelo LLM que chamou a tool.
                full_report.append(f"Mensagens de {c}:\n{combined_text}")
            except Exception as e:
                logger.error(f"[RESUMO] Erro em {c}: {e}")
        
        if not full_report:
            return "Nenhuma mensagem encontrada para os contatos informados."
            
        return "Aqui estão as mensagens que acumulei:\n\n" + "\n---\n".join(full_report)

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
            # RNNoise é menos agressivo que BVC e ajuda a evitar cortes na voz 
            # se o microfone não for de alta sensibilidade.
            noise_cancellation=noise_cancellation.NC(), 
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
    async def save_session_memory():
        """Gera um log detalhado da sessão para a memória episódica."""
        msgs = []
        for item in session._agent.chat_ctx.items:
            if not hasattr(item, "content") or not item.content:
                continue
            if item.role not in ("user", "assistant"):
                continue
            conteudo = "".join(item.content) if isinstance(item.content, list) else str(item.content)
            msgs.append({"role": item.role, "content": conteudo.strip()})
        
        if msgs:
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            data = {
                "timestamp": ts,
                "messages": msgs
            }
            path = f"memory/episodic/session_{ts}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"[EPISODIC] Sessão salva em {path}")

            # Sincroniza com Mem0
            try:
                await mem0_client.add(msgs, user_id=user_id)
                logger.info(f"[Mem0] {len(msgs)} mensagens sincronizadas.")
            except Exception as e:
                logger.warning(f"[Mem0] Erro ao sincronizar: {e}")

    async def shutdown_hook():
        # Encerrar WhatsApp ao fechar o agente
        logger.info("[SHUTDOWN] Encerrando bridge do WhatsApp...")
        _wpp_bridge.stop_monitor()
        _wpp_bridge.stop_bridge_process()
        
        # Parar tarefas em background
        metrics_task.cancel()
        auto_save_task.cancel()
        
        # Salvar memória final
        await save_session_memory()

        try:
            await metrics_task
            await auto_save_task
        except asyncio.CancelledError:
            pass

    ctx.add_shutdown_callback(shutdown_hook)

    # ── Métrica do Sistema (HUD) ────────────────────────
    async def metrics_publisher():
        while True:
            try:
                # Se estiver em modo game, envia a cada 10s. Senão, a cada 2s.
                sleep_time = 10 if agent._game_mode else 2
                
                metrics = {
                    "type": "metrics",
                    "data": {
                        "cpu": psutil.cpu_percent(),
                        "ram": psutil.virtual_memory().percent,
                        "disk": psutil.disk_usage('C:\\').percent,
                        "gpu": 0
                    }
                }
                await ctx.room.local_participant.publish_data(
                    json.dumps(metrics).encode('utf-8')
                )
            except Exception as e:
                logger.error(f"[METRICS] Erro ao publicar: {e}")
            
            # Reconhece mudança de modo mais rápido que o sleep total
            for _ in range(sleep_time):
                await asyncio.sleep(1)
                if agent._game_mode != (sleep_time == 10): break

    # Inicia a tarefa de métricas em background
    metrics_task = asyncio.create_task(metrics_publisher())

    # ── Auto-Save Periódico (5 min) ───────────────────
    async def periodic_autosave():
        while True:
            await asyncio.sleep(300) # 5 minutos
            await save_session_memory()
            logger.info("[AUTO-SAVE] Histórico atualizado com sucesso.")

    auto_save_task = asyncio.create_task(periodic_autosave())

    try:
        await session.generate_reply(
            instructions=SESSION_INSTRUCTION + "\nUse uma das saudações curtas sugeridas (estilo JARVIS)." + memoria_str
        )
    except Exception as e:
        logger.warning(f"[ENTRYPOINT] Falha ao gerar resposta inicial (provável desconexão): {e}")


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
