from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import subprocess
import time
import urllib.request as urllib_request
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import psutil
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, ChatContext, RoomInputOptions, llm
from livekit.plugins import google, noise_cancellation
from mem0 import AsyncMemoryClient

import whatsapp_bridge as whatsapp_bridge
from automacao_cortana import CortanaControl
from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION
from whatsapp_runtime import get_whatsapp_status, send_whatsapp_message

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("CORTANA_MODEL", "gemini-2.0-flash-exp")
DEFAULT_USER_ID = os.getenv("CORTANA_USER_ID", "Guilherme")
EPISODIC_MEMORY_DIR = Path("memory") / "episodic"
CDP_URL = "http://localhost:9222"
BLOCKED_COMMAND_TOKENS = (
    "&&",
    "||",
    ";",
    "|",
    ">",
    "<",
    "powershell",
    "pwsh",
    "cmd /c",
    "remove-item",
    "del ",
    "rmdir",
    "shutdown",
    "format",
    "diskpart",
    "reg delete",
)


def _get_google_api_key() -> str | None:
    google_key = os.getenv("GOOGLE_API_KEY")
    if google_key:
        return google_key.strip()
    gemini_key = os.getenv("GEMINI_API_KEY")
    return gemini_key.strip() if gemini_key else None


def _get_chrome_path() -> str | None:
    candidates = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    )
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


CHROME_PATH = _get_chrome_path()


def _cdp_available() -> bool:
    try:
        with urllib_request.urlopen(f"{CDP_URL}/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


async def _open_chrome_with_cdp(url: str = "about:blank") -> bool:
    if not CHROME_PATH:
        webbrowser.open(url)
        return False

    if _cdp_available() and PLAYWRIGHT_AVAILABLE:
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(CDP_URL)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
                await page.goto(url)
                await browser.disconnect()
            return True
        except Exception as exc:
            logger.warning("[CDP] Reusing existing Chrome failed: %s", exc)

    subprocess.Popen([CHROME_PATH, "--remote-debugging-port=9222", url])
    await asyncio.sleep(2.5)
    return _cdp_available()


def _ensure_episodic_dir() -> None:
    EPISODIC_MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _command_is_blocked(command: str) -> bool:
    normalized = " ".join(command.lower().split())
    return any(token in normalized for token in BLOCKED_COMMAND_TOKENS)


def _chat_item_to_text(item) -> str:
    content = getattr(item, "content", None)
    if content is None:
        return ""
    if isinstance(content, list):
        return "".join(str(part) for part in content).strip()
    return str(content).strip()


async def _publish_room_data(session: AgentSession | None, payload: dict) -> None:
    if not session or not getattr(session, "room", None):
        return
    await session.room.local_participant.publish_data(json.dumps(payload).encode("utf-8"))


async def _load_user_memories(mem0_client: AsyncMemoryClient, user_id: str) -> str:
    try:
        logger.info("[Mem0] Loading memories for '%s'...", user_id)
        response = await mem0_client.search(
            query="historico, preferencias e informacoes pessoais do usuario",
            filters={"user_id": user_id},
            limit=5,
        )
    except Exception as exc:
        logger.error("[Mem0] Failed to load memories: %s", exc)
        return ""

    if isinstance(response, dict):
        results = response.get("results", [])
    elif isinstance(response, list):
        results = response
    else:
        results = []

    memories: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        text = result.get("memory") or result.get("text") or result.get("content")
        if text:
            memories.append(f"- {text}")

    if not memories:
        return ""

    logger.info("[Mem0] %s memories loaded.", len(memories))
    return "\n".join(memories)


class _SpeechAdapter:
    def __init__(self, session: AgentSession | None):
        self._session = session

    async def say(self, text: str, add_to_chat: bool = True) -> None:
        if not self._session:
            return
        try:
            logger.info("[SpeechAdapter] Speaking proactive message.")
            await self._session.generate_reply(
                instructions=(
                    "Sua tarefa agora e apenas repassar esta notificacao para o usuario "
                    f"de forma curta e natural: {text}"
                ),
                add_to_chat_ctx=add_to_chat,
            )
        except Exception as exc:
            logger.error("[SpeechAdapter] Error while speaking: %s", exc)

    async def speak_proactive_message(
        self,
        text: str,
        label: str = "",
        add_to_chat_ctx: bool = True,
    ) -> None:
        await self.say(text, add_to_chat=add_to_chat_ctx)


class Assistant(Agent, llm.ToolContext):
    def __init__(self, chat_ctx: ChatContext | None = None, session: AgentSession | None = None):
        super().__init__(
            instructions=AGENT_INSTRUCTION,
            llm=google.beta.realtime.RealtimeModel(
                model=DEFAULT_MODEL,
                api_key=_get_google_api_key(),
                voice="Aoede",
                temperature=0.6,
            ),
            chat_ctx=chat_ctx or ChatContext(),
        )
        self.cortana_control = CortanaControl()
        self._session = session
        self._last_whatsapp_contact: str | None = None
        self._last_whatsapp_message: str | None = None
        self._whatsapp_history: list[dict[str, str]] = []
        self._pending_whatsapp_messages: dict[str, list[str]] = {}
        self._last_whatsapp_notif: dict[str, float] = {}
        self._game_mode = False

    def remember_whatsapp_message(self, contact: str, text: str) -> None:
        entry = {
            "contact": contact,
            "text": text,
            "at": time.strftime("%H:%M:%S"),
        }
        self._whatsapp_history.append(entry)
        self._last_whatsapp_contact = contact
        self._last_whatsapp_message = text
        if len(self._whatsapp_history) > 50:
            self._whatsapp_history = self._whatsapp_history[-50:]

    async def handle_whatsapp_notif(self, contact: str, text: str) -> None:
        now = time.time()
        last_time = self._last_whatsapp_notif.get(contact, 0)

        self.remember_whatsapp_message(contact, text)
        self._pending_whatsapp_messages.setdefault(contact, []).append(text)

        if (now - last_time) <= 180:
            logger.info("[WhatsApp] Silent accumulation for %s.", contact)
            return

        self._last_whatsapp_notif[contact] = now
        speech = _SpeechAdapter(self._session)
        await speech.say(f"Chefe, o {contact} mandou uma mensagem: {text}. Quer responder?")

    @agents.function_tool
    async def pesquisar_na_web(self, consulta: str, tipo: str = "google") -> str:
        try:
            search_type = tipo.lower().strip()
            if search_type == "youtube":
                url = f"https://www.youtube.com/results?search_query={quote_plus(consulta)}"
                await _open_chrome_with_cdp(url)
                return f"Abrindo busca do YouTube por '{consulta}'."
            if search_type == "url":
                await _open_chrome_with_cdp(consulta)
                return f"Abrindo: {consulta}"

            url = f"https://www.google.com/search?q={quote_plus(consulta)}"
            await _open_chrome_with_cdp(url)
            return f"Pesquisando '{consulta}' no Google."
        except Exception as exc:
            return f"Erro na pesquisa: {exc}"

    @agents.function_tool
    async def pausar_retomar_youtube(self) -> str:
        try:
            try:
                import pyautogui
                import pygetwindow as gw

                youtube_windows = [
                    window
                    for window in gw.getAllWindows()
                    if window.visible and "youtube" in window.title.lower()
                ]
                if youtube_windows:
                    youtube_windows[0].activate()
                    time.sleep(0.4)
                    pyautogui.press("k")
                    return "Play/pause alternado no YouTube."
            except ImportError:
                pass

            if PLAYWRIGHT_AVAILABLE and _cdp_available():
                async with async_playwright() as playwright:
                    browser = await playwright.chromium.connect_over_cdp(CDP_URL)
                    for context in browser.contexts:
                        for page in context.pages:
                            if "youtube.com/watch" not in page.url:
                                continue
                            await page.evaluate(
                                "const video = document.querySelector('video');"
                                "if (video) { video.paused ? video.play() : video.pause(); }"
                            )
                            await browser.disconnect()
                            return "Play/pause alternado via CDP."
                    await browser.disconnect()
                return "Nenhum video do YouTube foi encontrado no Chrome."

            return (
                "Nao foi possivel controlar o YouTube. "
                "Instale pygetwindow e pyautogui para habilitar o atalho de teclado."
            )
        except Exception as exc:
            return f"Erro no controle de midia: {exc}"

    @agents.function_tool
    async def fechar_programa(self, programa: str) -> str:
        cleaned = programa.strip()
        if not cleaned or any(char in cleaned for char in "&|;<>"):
            return "Nome de processo invalido."

        executable = cleaned if cleaned.lower().endswith(".exe") else f"{cleaned}.exe"
        result = subprocess.run(["taskkill", "/f", "/im", executable], capture_output=True, text=True)
        if result.returncode == 0:
            return f"Programa '{cleaned}' fechado com sucesso."
        return f"Nao foi possivel fechar '{cleaned}'. Verifique o nome do processo."

    @agents.function_tool
    async def abrir_programa(self, comando: str) -> str:
        cleaned = comando.strip()
        if not cleaned:
            return "Comando vazio."
        if _command_is_blocked(cleaned):
            return "Comando bloqueado por seguranca. Use abrir_aplicativo ou uma instrucao mais especifica."

        expanded = os.path.expandvars(os.path.expanduser(cleaned))
        try:
            if expanded.startswith(("http://", "https://")) or os.path.exists(expanded):
                os.startfile(expanded)
                return f"Abrindo '{cleaned}'."
        except OSError:
            pass

        try:
            args = shlex.split(cleaned, posix=False)
            if not args:
                return "Nao consegui interpretar o comando informado."
            subprocess.Popen(args, shell=False)
            return f"'{cleaned}' aberto."
        except Exception as exc:
            return f"Erro ao abrir '{cleaned}': {exc}"

    @agents.function_tool
    async def tocar_musica(self, musica: str) -> str:
        return self.cortana_control.tocar_musica_spotify(musica)

    @agents.function_tool
    async def abrir_aplicativo(self, nome_app: str) -> str:
        return self.cortana_control.abrir_aplicativo(nome_app)

    @agents.function_tool
    async def criar_pasta(self, caminho: str) -> str:
        return self.cortana_control.cria_pasta(caminho)

    @agents.function_tool
    async def deletar_item(self, caminho: str) -> str:
        return self.cortana_control.deletar_arquivo(caminho)

    @agents.function_tool
    async def limpar_diretorio(self, caminho: str) -> str:
        return self.cortana_control.limpar_diretorio(caminho)

    @agents.function_tool
    async def mover_item(self, origem: str, destino: str) -> str:
        return self.cortana_control.mover_item(origem, destino)

    @agents.function_tool
    async def copiar_item(self, origem: str, destino: str) -> str:
        return self.cortana_control.copiar_item(origem, destino)

    @agents.function_tool
    async def renomear_item(self, caminho: str, novo_nome: str) -> str:
        return self.cortana_control.renomear_item(caminho, novo_nome)

    @agents.function_tool
    async def organizar_pasta(self, caminho: str) -> str:
        return self.cortana_control.organizar_pasta(caminho)

    @agents.function_tool
    async def compactar_pasta(self, caminho: str) -> str:
        return self.cortana_control.compactar_pasta(caminho)

    @agents.function_tool
    async def abrir_pasta(self, nome_pasta: str) -> str:
        return self.cortana_control.abrir_pasta(nome_pasta)

    @agents.function_tool
    async def buscar_e_abrir_arquivo(self, nome_arquivo: str) -> str:
        return self.cortana_control.buscar_e_abrir_arquivo(nome_arquivo)

    @agents.function_tool
    async def controle_volume(self, nivel: int) -> str:
        return self.cortana_control.controle_volume(nivel)

    @agents.function_tool
    async def controle_brilho(self, nivel: int) -> str:
        return self.cortana_control.controle_brilho(nivel)

    @agents.function_tool
    async def energia_pc(self, acao: str) -> str:
        return self.cortana_control.energia_pc(acao)

    @agents.function_tool
    async def wake_on_lan(self, mac_address: str) -> str:
        return self.cortana_control.wake_on_lan(mac_address)

    @agents.function_tool
    async def controle_tv_lg(self, ip: str, acao: str) -> str:
        return self.cortana_control.controle_tv_lg(ip, acao)

    @agents.function_tool
    async def controle_tv_samsung(self, ip: str, acao: str) -> str:
        return self.cortana_control.controle_tv_samsung(ip, acao)

    @agents.function_tool
    async def controle_dispositivo_broadlink(self, ip: str, acao: str) -> str:
        return self.cortana_control.controle_dispositivo_broadlink(ip, acao)

    @agents.function_tool
    async def controle_tv_tcl(self, ip: str, acao: str) -> str:
        return await self.cortana_control.controle_tv_tcl(ip, acao)

    @agents.function_tool
    async def conectar_whatsapp(self) -> str:
        if not self._session:
            return "Erro: sessao do agente nao foi inicializada."
        logger.info("[WhatsApp] Starting connection flow.")
        speech_adapter = _SpeechAdapter(self._session)
        success, message = await whatsapp_bridge.connect_whatsapp(self, speech_adapter)
        return message if success else message

    @agents.function_tool
    async def enviar_whatsapp(self, contato: str, mensagem: str) -> str:
        if not await whatsapp_bridge.is_whatsapp_connected_async():
            return "WhatsApp desconectado. Diga 'conecta meu whatsapp'."
        result = await send_whatsapp_message(contato, mensagem)
        return "Enviado!" if result.get("success") else f"Erro: {result.get('message')}"

    @agents.function_tool
    async def status_whatsapp(self) -> str:
        if not await whatsapp_bridge.is_whatsapp_connected_async():
            return "Nao conectado"
        response = await get_whatsapp_status()
        return "Conectado" if response.get("connected") else "Offline"

    @agents.function_tool
    async def aprender_fato(self, fato: str) -> str:
        if not fato.strip():
            return "Nada para memorizar."
        try:
            mem0_client = AsyncMemoryClient()
            await mem0_client.add([{"role": "user", "content": fato}], user_id=DEFAULT_USER_ID)
            logger.info("[Mem0] Learned new fact for %s.", DEFAULT_USER_ID)
            return f"Fato memorizado: '{fato}'"
        except Exception as exc:
            return f"Erro ao memorizar fato: {exc}"

    @agents.function_tool
    async def pesquisar_no_passado(self, termo: str) -> str:
        try:
            files = sorted(EPISODIC_MEMORY_DIR.glob("session_*.json"), reverse=True)
            if not files:
                return "Ainda nao tenho registros no meu historico local."

            matches: list[str] = []
            search_term = termo.lower()
            for file_path in files[:10]:
                with file_path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)

                messages = data.get("messages", [])
                for index, message in enumerate(messages):
                    content = str(message.get("content", ""))
                    if search_term not in content.lower():
                        continue

                    context = messages[max(0, index - 1) : min(len(messages), index + 2)]
                    snippets = [
                        f"[{item.get('role', 'unknown')}]: {item.get('content', '')}"
                        for item in context
                    ]
                    matches.append(f"Em {data.get('timestamp', file_path.stem)}:\n" + "\n".join(snippets))
                    break

            if not matches:
                return f"Nao encontrei registros de '{termo}' nas minhas conversas passadas."

            return "--- REGISTROS ENCONTRADOS ---\n\n" + "\n\n---\n\n".join(matches)
        except Exception as exc:
            return f"Erro na pesquisa local: {exc}"

    @agents.function_tool
    async def modo_game(self, ativar: bool) -> str:
        self._game_mode = ativar
        try:
            await _publish_room_data(self._session, {"type": "game_mode", "active": ativar})
        except Exception as exc:
            logger.error("[Mode] Failed to notify frontend: %s", exc)
        return (
            "Modo Game ativado. Vou economizar recursos agora, chefe."
            if ativar
            else "Modo Game desativado. Voltando ao poder total."
        )

    @agents.function_tool
    async def resumo_whatsapp(self, contato: str | None = None) -> str:
        if not self._pending_whatsapp_messages:
            return "Nao ha mensagens pendentes para resumir, chefe."

        target_contacts = [contato] if contato else list(self._pending_whatsapp_messages.keys())
        report: list[str] = []
        for current_contact in target_contacts:
            messages = self._pending_whatsapp_messages.get(current_contact, [])
            if not messages:
                continue
            self._pending_whatsapp_messages[current_contact] = []
            report.append(f"Mensagens de {current_contact}:\n" + "\n".join(messages))

        if not report:
            return "Nenhuma mensagem encontrada para os contatos informados."
        return "Aqui estao as mensagens que acumulei:\n\n" + "\n---\n".join(report)

    @agents.function_tool
    async def desconectar_whatsapp(self) -> str:
        await whatsapp_bridge.disconnect_whatsapp()
        return "WhatsApp desconectado."


async def entrypoint(ctx: agents.JobContext) -> None:
    user_id = DEFAULT_USER_ID
    mem0_client = AsyncMemoryClient()
    initial_ctx = ChatContext()
    memory_block = await _load_user_memories(mem0_client, user_id)
    if memory_block:
        initial_ctx.add_message(
            role="assistant",
            content=(
                f"O usuario se chama {user_id}. Use estas memorias como contexto quando forem relevantes:\n"
                f"{memory_block}"
            ),
        )

    await ctx.connect()

    session = AgentSession()
    agent = Assistant(chat_ctx=initial_ctx, session=session)
    await session.start(
        room=ctx.room,
        agent=agent,
        room_input_options=RoomInputOptions(
            video_enabled=True,
            noise_cancellation=noise_cancellation.NC(),
        ),
    )

    last_saved_payload: str | None = None
    last_synced_payload: str | None = None

    def _session_messages() -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for item in agent.chat_ctx.items:
            role = getattr(item, "role", None)
            if role not in ("user", "assistant"):
                continue
            content = _chat_item_to_text(item)
            if content:
                messages.append({"role": role, "content": content})
        return messages

    async def save_session_memory(sync_mem0: bool = False) -> None:
        nonlocal last_saved_payload, last_synced_payload

        messages = _session_messages()
        if not messages:
            return

        payload = json.dumps(messages, ensure_ascii=False)
        if payload != last_saved_payload:
            _ensure_episodic_dir()
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            file_path = EPISODIC_MEMORY_DIR / f"session_{timestamp}.json"
            with file_path.open("w", encoding="utf-8") as handle:
                json.dump({"timestamp": timestamp, "messages": messages}, handle, ensure_ascii=False, indent=2)
            logger.info("[Episodic] Session saved to %s", file_path)
            last_saved_payload = payload

        if sync_mem0 and payload != last_synced_payload:
            try:
                await mem0_client.add(messages, user_id=user_id)
                last_synced_payload = payload
                logger.info("[Mem0] %s messages synchronized.", len(messages))
            except Exception as exc:
                logger.warning("[Mem0] Synchronization failed: %s", exc)

    async def metrics_publisher() -> None:
        while True:
            try:
                sleep_time = 10 if agent._game_mode else 2
                metrics = {
                    "type": "metrics",
                    "data": {
                        "cpu": psutil.cpu_percent(),
                        "ram": psutil.virtual_memory().percent,
                        "disk": psutil.disk_usage("C:\\").percent,
                        "gpu": 0,
                    },
                }
                await ctx.room.local_participant.publish_data(json.dumps(metrics).encode("utf-8"))
            except Exception as exc:
                logger.error("[Metrics] Failed to publish metrics: %s", exc)

            for _ in range(sleep_time):
                await asyncio.sleep(1)
                if agent._game_mode != (sleep_time == 10):
                    break

    async def periodic_autosave() -> None:
        while True:
            await asyncio.sleep(300)
            await save_session_memory(sync_mem0=False)
            logger.info("[AutoSave] Episodic memory updated.")

    metrics_task = asyncio.create_task(metrics_publisher())
    auto_save_task = asyncio.create_task(periodic_autosave())

    async def shutdown_hook() -> None:
        logger.info("[Shutdown] Stopping background integrations.")
        whatsapp_bridge.stop_monitor()
        whatsapp_bridge.stop_bridge_process()

        for task in (metrics_task, auto_save_task):
            task.cancel()

        await save_session_memory(sync_mem0=True)

        for task in (metrics_task, auto_save_task):
            try:
                await task
            except asyncio.CancelledError:
                pass

    ctx.add_shutdown_callback(shutdown_hook)

    try:
        await session.generate_reply(
            instructions=SESSION_INSTRUCTION + "\nUse uma saudacao curta no estilo JARVIS."
        )
    except Exception as exc:
        logger.warning("[Entrypoint] Failed to generate initial reply: %s", exc)


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
