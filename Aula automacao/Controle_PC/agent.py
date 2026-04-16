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
import requests
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentSession, ChatContext, RoomInputOptions, llm
from livekit.plugins import google, noise_cancellation
from mem0 import AsyncMemoryClient

from cloud_memory_sync import sync_mem0_to_shared
from face_auth import FaceAuthManager
import whatsapp_bridge as whatsapp_bridge
from automacao_cortana import CortanaControl
from cyber_audit import CyberSentry
from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION
from shared_memory import shared_memory
from whatsapp_runtime import get_whatsapp_status, send_whatsapp_message

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_USER_ID = os.getenv("CORTANA_USER_ID", "Guilherme")
EPISODIC_MEMORY_DIR = Path("memory") / "episodic"
CDP_URL = "http://localhost:9222"
MODEL_LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models"
REALTIME_MODEL_PREFERENCES = (
    "gemini-2.5-flash-native-audio-latest",
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-preview-12-2025",
    "gemini-2.5-flash-native-audio-preview-09-2025",
    "gemini-2.0-flash-exp",
)
FACE_AUTH_STATUS_INTERVAL_SECONDS = 1.0
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


def _env_flag(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _get_google_api_key() -> str | None:
    google_key = os.getenv("GOOGLE_API_KEY")
    if google_key:
        return google_key.strip()
    gemini_key = os.getenv("GEMINI_API_KEY")
    return gemini_key.strip() if gemini_key else None


def _normalize_api_key_env() -> None:
    google_key = os.getenv("GOOGLE_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    if google_key and gemini_key and google_key == gemini_key:
        os.environ.pop("GEMINI_API_KEY", None)
        logger.info("[Config] GEMINI_API_KEY removida do ambiente porque duplicava GOOGLE_API_KEY.")


def _normalize_model_name(model_name: str | None) -> str | None:
    if not model_name:
        return None
    cleaned = model_name.strip()
    if cleaned.startswith("models/"):
        cleaned = cleaned.split("/", 1)[1]
    return cleaned or None


def _list_realtime_models(api_key: str | None) -> set[str]:
    if not api_key:
        return set()

    try:
        response = requests.get(
            MODEL_LIST_URL,
            params={"key": api_key},
            timeout=15,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("[Config] Nao foi possivel listar modelos Gemini: %s", exc)
        return set()

    available_models: set[str] = set()
    for model in response.json().get("models", []):
        methods = model.get("supportedGenerationMethods", [])
        if "bidiGenerateContent" not in methods:
            continue
        normalized = _normalize_model_name(model.get("name"))
        if normalized:
            available_models.add(normalized)
    return available_models


def _resolve_realtime_model() -> str:
    configured_model = _normalize_model_name(os.getenv("CORTANA_MODEL"))
    api_key = _get_google_api_key()
    available_models = _list_realtime_models(api_key)

    if configured_model and configured_model in available_models:
        logger.info("[Config] Usando modelo Realtime configurado: %s", configured_model)
        return configured_model

    if configured_model:
        logger.warning(
            "[Config] Modelo '%s' nao suporta bidiGenerateContent nesta chave/API. Aplicando fallback.",
            configured_model,
        )

    for candidate in REALTIME_MODEL_PREFERENCES:
        if candidate in available_models:
            logger.info("[Config] Usando fallback Realtime compativel: %s", candidate)
            return candidate

    if available_models:
        fallback = sorted(available_models)[0]
        logger.warning("[Config] Nenhum modelo preferido encontrado. Usando %s.", fallback)
        return fallback

    fallback = REALTIME_MODEL_PREFERENCES[0]
    logger.warning("[Config] Lista de modelos indisponivel. Usando fallback estatico %s.", fallback)
    return fallback


_normalize_api_key_env()
DEFAULT_MODEL = _resolve_realtime_model()


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


async def _publish_room_data_direct(room, payload: dict) -> None:
    if room is None:
        return
    await room.local_participant.publish_data(json.dumps(payload).encode("utf-8"))


def _build_face_auth_manager() -> FaceAuthManager:
    return FaceAuthManager(
        enabled=_env_flag("FACE_AUTH_REQUIRED", "0"),
        profile_name=os.getenv("FACE_AUTH_PROFILE_NAME", DEFAULT_USER_ID),
        camera_index=int(os.getenv("FACE_AUTH_CAMERA_INDEX", "0")),
        confidence_threshold=float(os.getenv("FACE_AUTH_CONFIDENCE_THRESHOLD", "52")),
        unlock_streak=int(os.getenv("FACE_AUTH_UNLOCK_STREAK", "6")),
        sample_count=int(os.getenv("FACE_AUTH_SAMPLE_COUNT", "25")),
        lock_grace_seconds=float(os.getenv("FACE_AUTH_LOCK_GRACE_SECONDS", "8")),
        presence_grace_seconds=float(os.getenv("FACE_AUTH_PRESENCE_GRACE_SECONDS", "45")),
        unauthorized_grace_seconds=float(os.getenv("FACE_AUTH_UNAUTHORIZED_GRACE_SECONDS", "18")),
        frame_interval=float(os.getenv("FACE_AUTH_FRAME_INTERVAL", "0.20")),
        continuous_monitor=_env_flag("FACE_AUTH_CONTINUOUS_MONITOR", "0"),
        confidence_margin=float(os.getenv("FACE_AUTH_CONFIDENCE_MARGIN", "12")),
        adaptive_learning=_env_flag("FACE_AUTH_ADAPTIVE_LEARNING", "1"),
        adaptive_sample_limit=int(os.getenv("FACE_AUTH_ADAPTIVE_SAMPLE_LIMIT", "80")),
        adaptive_learning_cooldown_seconds=float(
            os.getenv("FACE_AUTH_ADAPTIVE_LEARNING_COOLDOWN_SECONDS", "1800")
        ),
    )


async def _load_user_memories(mem0_client: AsyncMemoryClient, user_id: str) -> str:
    sections: list[str] = []

    try:
        logger.info("[Mem0] Loading memories for '%s'...", user_id)
        sync_stats = await sync_mem0_to_shared(user_id, client=mem0_client)
        if sync_stats["fetched"]:
            logger.info(
                "[Mem0] %s memories hydrated to shared memory (%s new, %s existing).",
                sync_stats["fetched"],
                sync_stats["inserted"],
                sync_stats["updated"],
            )
        response = await mem0_client.search(
            query="historico, preferencias e informacoes pessoais do usuario",
            filters={"user_id": user_id},
            limit=5,
        )
    except Exception as exc:
        logger.error("[Mem0] Failed to load memories: %s", exc)
        local_context = shared_memory.build_context_block(user_id, fact_limit=12, episode_limit=2)
        if local_context:
            sections.append(local_context)
        return "\n\n".join(sections).strip()

    local_context = shared_memory.build_context_block(user_id, fact_limit=12, episode_limit=2)
    if local_context:
        sections.append(local_context)

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

    if memories:
        logger.info("[Mem0] %s memories loaded.", len(memories))
        sections.append("Memorias sincronizadas na nuvem:\n" + "\n".join(memories))

    return "\n\n".join(section for section in sections if section).strip()


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
    def __init__(
        self,
        chat_ctx: ChatContext | None = None,
        session: AgentSession | None = None,
        face_auth: FaceAuthManager | None = None,
    ):
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
        self._cyber_sentry = CyberSentry(headed=True)
        self._session = session
        self._last_whatsapp_contact: str | None = None
        self._last_whatsapp_message: str | None = None
        self._whatsapp_history: list[dict[str, str]] = []
        self._pending_whatsapp_messages: dict[str, list[str]] = {}
        self._last_whatsapp_notif: dict[str, float] = {}
        self._game_mode = False
        self._face_auth = face_auth

    def _require_face_auth(self) -> str | None:
        if not self._face_auth or not self._face_auth.enabled:
            return None
        if self._face_auth.is_authenticated():
            return None
        return (
            "Face ID bloqueado. A Cortana so responde apos reconhecer o rosto autorizado "
            "na webcam."
        )

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
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
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
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
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
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
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
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
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
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.tocar_musica_spotify(musica)

    @agents.function_tool
    async def abrir_aplicativo(self, nome_app: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.abrir_aplicativo(nome_app)

    @agents.function_tool
    async def criar_pasta(self, caminho: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.cria_pasta(caminho)

    @agents.function_tool
    async def deletar_item(self, caminho: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.deletar_arquivo(caminho)

    @agents.function_tool
    async def limpar_diretorio(self, caminho: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.limpar_diretorio(caminho)

    @agents.function_tool
    async def mover_item(self, origem: str, destino: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.mover_item(origem, destino)

    @agents.function_tool
    async def copiar_item(self, origem: str, destino: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.copiar_item(origem, destino)

    @agents.function_tool
    async def renomear_item(self, caminho: str, novo_nome: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.renomear_item(caminho, novo_nome)

    @agents.function_tool
    async def organizar_pasta(self, caminho: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.organizar_pasta(caminho)

    @agents.function_tool
    async def compactar_pasta(self, caminho: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.compactar_pasta(caminho)

    @agents.function_tool
    async def abrir_pasta(self, nome_pasta: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.abrir_pasta(nome_pasta)

    @agents.function_tool
    async def buscar_e_abrir_arquivo(self, nome_arquivo: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.buscar_e_abrir_arquivo(nome_arquivo)

    @agents.function_tool
    async def controle_volume(self, nivel: int) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.controle_volume(nivel)

    @agents.function_tool
    async def controle_brilho(self, nivel: int) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.controle_brilho(nivel)

    @agents.function_tool
    async def energia_pc(self, acao: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.energia_pc(acao)

    @agents.function_tool
    async def wake_on_lan(self, mac_address: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.wake_on_lan(mac_address)

    @agents.function_tool
    async def controle_tv_lg(self, ip: str, acao: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.controle_tv_lg(ip, acao)

    @agents.function_tool
    async def controle_tv_samsung(self, ip: str, acao: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.controle_tv_samsung(ip, acao)

    @agents.function_tool
    async def controle_dispositivo_broadlink(self, ip: str, acao: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return self.cortana_control.controle_dispositivo_broadlink(ip, acao)

    @agents.function_tool
    async def controle_tv_tcl(self, ip: str, acao: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        return await self.cortana_control.controle_tv_tcl(ip, acao)

    @agents.function_tool
    async def status_face_id(self) -> str:
        if not self._face_auth or not self._face_auth.enabled:
            return "Face ID desativado."
        snapshot = self._face_auth.snapshot()
        if not snapshot.enrolled:
            return "Face ID ainda nao cadastrado."
        profile = self._face_auth.profile_summary()
        monitor_mode = (
            "monitoramento continuo ligado"
            if profile.get("continuous_monitor")
            else "desbloqueio apenas no inicio da sessao"
        )
        if snapshot.authenticated:
            return (
                f"Face ID autenticado para {snapshot.profile_name}. "
                f"Modo: {monitor_mode}. "
                f"Desbloqueios: {profile.get('successful_unlocks', 0)}. "
                f"Amostras aprendidas: {profile.get('adaptive_sample_count', 0)}. "
                f"Confianca atual: {snapshot.confidence:.2f}."
                if snapshot.confidence is not None
                else (
                    f"Face ID autenticado para {snapshot.profile_name}. "
                    f"Modo: {monitor_mode}. "
                    f"Desbloqueios: {profile.get('successful_unlocks', 0)}. "
                    f"Amostras aprendidas: {profile.get('adaptive_sample_count', 0)}."
                )
            )
        return (
            f"Face ID bloqueado no momento para {snapshot.profile_name}. "
            f"Ultimo motivo: {snapshot.reason or 'desconhecido'}."
        )

    @agents.function_tool
    async def perfil_face_id(self) -> str:
        if not self._face_auth or not self._face_auth.enabled:
            return "Face ID desativado."
        if not self._face_auth.is_enrolled():
            return "Face ID ainda nao cadastrado."

        profile = self._face_auth.profile_summary()
        avg_confidence = profile.get("average_confidence")
        best_confidence = profile.get("best_confidence")
        return (
            f"Perfil facial de {profile.get('profile_name')}. "
            f"Criado em {profile.get('created_at')}. "
            f"Ultimo reconhecimento em {profile.get('last_seen_at') or 'ainda nao registrado'}. "
            f"Desbloqueios bem-sucedidos: {profile.get('successful_unlocks', 0)}. "
            f"Amostras base: {profile.get('base_sample_count', 0)}. "
            f"Amostras aprendidas: {profile.get('adaptive_sample_count', 0)}. "
            f"Aprendizado adaptativo: {'ativo' if profile.get('adaptive_learning') else 'desligado'}. "
            f"Melhor confianca: {best_confidence:.2f}. "
            f"Confianca media: {avg_confidence:.2f}."
            if isinstance(avg_confidence, (int, float)) and isinstance(best_confidence, (int, float))
            else (
                f"Perfil facial de {profile.get('profile_name')}. "
                f"Criado em {profile.get('created_at')}. "
                f"Ultimo reconhecimento em {profile.get('last_seen_at') or 'ainda nao registrado'}. "
                f"Desbloqueios bem-sucedidos: {profile.get('successful_unlocks', 0)}. "
                f"Amostras base: {profile.get('base_sample_count', 0)}. "
                f"Amostras aprendidas: {profile.get('adaptive_sample_count', 0)}. "
                f"Aprendizado adaptativo: {'ativo' if profile.get('adaptive_learning') else 'desligado'}."
            )
        )

    @agents.function_tool
    async def conectar_whatsapp(self) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        if not self._session:
            return "Erro: sessao do agente nao foi inicializada."
        logger.info("[WhatsApp] Starting connection flow.")
        speech_adapter = _SpeechAdapter(self._session)
        success, message = await whatsapp_bridge.connect_whatsapp(self, speech_adapter)
        return message if success else message

    @agents.function_tool
    async def enviar_whatsapp(self, contato: str, mensagem: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
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
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        if not fato.strip():
            return "Nada para memorizar."
        local_saved = shared_memory.add_fact(DEFAULT_USER_ID, fato, source="online")
        try:
            mem0_client = AsyncMemoryClient()
            await mem0_client.add([{"role": "user", "content": fato}], user_id=DEFAULT_USER_ID)
            logger.info("[Mem0] Learned new fact for %s.", DEFAULT_USER_ID)
            if local_saved:
                return f"Fato memorizado na memoria compartilhada e sincronizado: '{fato}'"
            return f"Fato atualizado e sincronizado: '{fato}'"
        except Exception as exc:
            if local_saved:
                logger.warning("[Mem0] Fact saved only in shared memory: %s", exc)
                return f"Fato memorizado localmente: '{fato}'. A sincronizacao online falhou: {exc}"
            return f"Erro ao memorizar fato: {exc}"

    @agents.function_tool
    async def pesquisar_no_passado(self, termo: str) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        try:
            fact_matches = shared_memory.search_facts(DEFAULT_USER_ID, termo, limit=4)
            episode_matches = shared_memory.search_episodes(DEFAULT_USER_ID, termo, limit=4)

            sections: list[str] = []
            if fact_matches:
                fact_lines = [f"- {match['content']}" for match in fact_matches]
                sections.append("Fatos relacionados:\n" + "\n".join(fact_lines))
            if episode_matches:
                sections.append("Historico episodico:\n" + "\n\n".join(episode_matches))

            if not sections:
                return f"Nao encontrei registros de '{termo}' na memoria compartilhada."

            return "Encontrei isto no meu passado:\n\n" + "\n\n".join(sections)
        except Exception as exc:
            return f"Erro na pesquisa local: {exc}"

    @agents.function_tool
    async def modo_game(self, ativar: bool) -> str:
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
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
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
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
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        await whatsapp_bridge.disconnect_whatsapp()
        return "WhatsApp desconectado."

    # ------------------------------------------------------------------
    # CyberSentry — Ferramentas de Auditoria de Segurança Web
    # ------------------------------------------------------------------

    @agents.function_tool
    async def iniciar_auditoria_completa(self, url: str) -> str:
        """Inicia auditoria de segurança completa em um alvo web: crawling, headers, TLS, cookies, fingerprint, arquivos sensíveis e gera relatório profissional."""
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        try:
            speech = _SpeechAdapter(self._session)

            async def _progress(msg: str) -> None:
                await speech.say(msg)

            result = await self._cyber_sentry.full_audit(url, callback=_progress)
            paths = await self._cyber_sentry.save_report(result)

            findings = result.sorted_findings()
            crits = sum(1 for f in findings if f.severity == "CRITICAL")
            highs = sum(1 for f in findings if f.severity == "HIGH")

            summary = (
                f"Auditoria finalizada para {result.domain}. "
                f"{len(findings)} observações encontradas"
            )
            if crits:
                summary += f", sendo {crits} críticas"
            if highs:
                summary += f" e {highs} de alta severidade"
            summary += f". Relatório salvo em: {paths.get('directory', 'N/A')}"

            return summary
        except Exception as exc:
            return f"Erro na auditoria: {exc}"

    @agents.function_tool
    async def mapear_superficie_web(self, url: str) -> str:
        """Faz crawling do site para descobrir rotas, formulários, assets JS e APIs sem executar análise de segurança."""
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        try:
            result = await self._cyber_sentry.crawl(url)
            return (
                f"Mapeamento de {result.domain} concluído. "
                f"Rotas: {len(result.routes)}, "
                f"Formulários: {len(result.forms)}, "
                f"JS assets: {len(result.js_assets)}, "
                f"Chamadas de API: {len(result.api_endpoints)}."
            )
        except Exception as exc:
            return f"Erro no crawling: {exc}"

    @agents.function_tool
    async def analisar_headers_e_tls(self, url: str) -> str:
        """Análise rápida de headers de segurança, cookies, CSP, CORS e TLS de um site, sem crawling."""
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        try:
            result = await self._cyber_sentry.quick_header_audit(url)
            findings = result.sorted_findings()
            if not findings:
                return f"Nenhum problema detectado nos headers e TLS de {result.domain}."

            lines = [f"Análise rápida de {result.domain}: {len(findings)} observações."]
            for f in findings[:8]:
                lines.append(f"- [{f.severity}] {f.title}")
            if len(findings) > 8:
                lines.append(f"... e mais {len(findings) - 8} observações.")
            return "\n".join(lines)
        except Exception as exc:
            return f"Erro na análise: {exc}"

    @agents.function_tool
    async def capturar_evidencia_visual(self, url: str, seletor_css: str = "") -> str:
        """Captura screenshot de página inteira ou de um elemento específico via CSS selector como evidência de auditoria."""
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        try:
            selector = seletor_css.strip() if seletor_css else None
            path = await self._cyber_sentry.capture_screenshot(url, css_selector=selector)
            return f"Screenshot salvo em: {path}"
        except Exception as exc:
            return f"Erro na captura: {exc}"

    @agents.function_tool
    async def gerar_relatorio_pentest(self, dominio: str) -> str:
        """Gera relatório profissional de pentest em Markdown e PDF com os dados coletados da última auditoria do domínio informado."""
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
        try:
            result = self._cyber_sentry._cache.get(dominio)
            if not result:
                return (
                    f"Sem dados de auditoria para '{dominio}'. "
                    "Execute iniciar_auditoria_completa ou analisar_headers_e_tls primeiro."
                )
            paths = await self._cyber_sentry.save_report(result)
            return (
                f"Relatório gerado para {dominio}. "
                f"PDF: {paths.get('pdf', 'N/A')}. "
                f"Markdown: {paths.get('markdown', 'N/A')}. "
                f"Pasta: {paths.get('directory', 'N/A')}."
            )
        except Exception as exc:
            return f"Erro ao gerar relatório: {exc}"

    @agents.function_tool
    async def tarefa_complexa_avancada(self, instrucao: str) -> str:
        """
        [USO RESTRITO] Use EXCLUSIVAMENTE como ULTIMO RECURSO se você nao tiver ferramentas prontas (nativas) capazes de resolver a demanda.
        Essa ferramenta usa inteligencia local Python (Open Interpreter) para criar e rodar rotinas que resolvem tarefas no SO.
        """
        auth_error = self._require_face_auth()
        if auth_error:
            return auth_error
            
        try:
            from interpreter import interpreter
            
            interpreter.auto_run = True
            
            google_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            if google_key and not os.getenv("INTERPRETER_MODEL", "").startswith("ollama/"):
                interpreter.llm.model = os.getenv("INTERPRETER_MODEL", "gemini/gemini-2.5-flash")
                interpreter.llm.api_key = google_key
            else:
                interpreter.llm.model = os.getenv("INTERPRETER_MODEL", "ollama/llama3")
                
            interpreter.llm.max_tokens = 2048
            
            # Aqui rola o loop agente->python->terminal
            results = interpreter.chat(instrucao)
            
            if results and isinstance(results, list):
                # Percorre revertido para achar a ultima string de assistente
                for msg in reversed(results):
                    if msg.get("role") == "assistant" and msg.get("type") == "message":
                        content = msg.get("content", "")
                        return f"O Cérebro Auxiliar completou a tarefa. Relato:\n{content}"
                        
            return f"O Cérebro Auxiliar rodou em segundo plano. Detalhe raw parcial: {str(results)[:150]}"
        except Exception as exc:
            return f"O Open Interpreter falhou: {exc}"



async def entrypoint(ctx: agents.JobContext) -> None:
    user_id = DEFAULT_USER_ID
    mem0_client = AsyncMemoryClient()
    face_auth = _build_face_auth_manager()
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

    if face_auth.enabled:
        if not face_auth.is_enrolled():
            message = (
                "Face ID ainda nao foi cadastrado. Rode 'python setup_face_auth.py' em "
                "'Aula automacao/Controle_PC' antes de iniciar a Cortana."
            )
            logger.error("[FaceAuth] %s", message)
            await _publish_room_data_direct(
                ctx.room,
                {
                    "type": "face_auth",
                    "state": "error",
                    "profile_name": face_auth.profile_name,
                    "message": message,
                },
            )
            await ctx.room.disconnect()
            return

        await _publish_room_data_direct(
            ctx.room,
            {
                "type": "face_auth",
                "state": "waiting",
                "profile_name": face_auth.profile_name,
            },
        )
        logger.info("[FaceAuth] Aguardando rosto autorizado para desbloquear a Cortana...")
        try:
            unlocked = await asyncio.to_thread(face_auth.wait_for_unlock, None, True)
        except RuntimeError as exc:
            message = str(exc)
            logger.error("[FaceAuth] %s", message)
            await _publish_room_data_direct(
                ctx.room,
                {
                    "type": "face_auth",
                    "state": "error",
                    "profile_name": face_auth.profile_name,
                    "message": message,
                },
            )
            await ctx.room.disconnect()
            return
        if not unlocked:
            message = "Nao foi possivel validar o Face ID antes de iniciar a sessao."
            logger.warning("[FaceAuth] %s", message)
            await _publish_room_data_direct(
                ctx.room,
                {
                    "type": "face_auth",
                    "state": "locked",
                    "profile_name": face_auth.profile_name,
                    "message": message,
                },
            )
            await ctx.room.disconnect()
            return
        await _publish_room_data_direct(
            ctx.room,
            {
                "type": "face_auth",
                "state": "authenticated",
                "profile_name": face_auth.profile_name,
            },
        )

    session = AgentSession()
    agent = Assistant(chat_ctx=initial_ctx, session=session, face_auth=face_auth)
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
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            file_path = shared_memory.save_episode(
                user_id,
                messages,
                source="online",
                timestamp_label=timestamp,
                write_json_snapshot=True,
            )
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
    face_auth_task: asyncio.Task | None = None

    if face_auth.enabled and face_auth.continuous_monitor:
        face_auth.start_monitor()

        async def face_auth_enforcer() -> None:
            last_state = None
            while True:
                snapshot = face_auth.snapshot()
                state = "authenticated" if snapshot.authenticated else "locked"
                if state != last_state:
                    await _publish_room_data(
                        session,
                        {
                            "type": "face_auth",
                            "state": state,
                            "profile_name": snapshot.profile_name,
                            "reason": snapshot.reason,
                            "confidence": snapshot.confidence,
                        },
                    )
                    last_state = state

                if not snapshot.authenticated:
                    logger.warning(
                        "[FaceAuth] Rosto autorizado perdido. Encerrando sessao. reason=%s confidence=%s",
                        snapshot.reason,
                        f"{snapshot.confidence:.2f}" if snapshot.confidence is not None else "n/a",
                    )
                    try:
                        session.interrupt()
                    except Exception:
                        pass
                    await session.aclose()
                    await ctx.room.disconnect()
                    break

                await asyncio.sleep(FACE_AUTH_STATUS_INTERVAL_SECONDS)

        face_auth_task = asyncio.create_task(face_auth_enforcer())

    async def shutdown_hook() -> None:
        logger.info("[Shutdown] Stopping background integrations.")
        whatsapp_bridge.stop_monitor()
        whatsapp_bridge.stop_bridge_process()
        face_auth.stop_monitor()

        active_tasks = [metrics_task, auto_save_task]
        if face_auth_task:
            active_tasks.append(face_auth_task)

        for task in active_tasks:
            task.cancel()

        await save_session_memory(sync_mem0=True)

        for task in active_tasks:
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
