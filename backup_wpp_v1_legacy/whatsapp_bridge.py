"""
whatsapp_bridge.py
Gerenciador central do bridge WhatsApp.
Controla ciclo de vida: iniciar, aguardar conexão, ocultar janela, monitorar, parar.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BRIDGE_URL = "http://127.0.0.1:5050"

_bridge_process: Optional[subprocess.Popen] = None
_monitor_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None
_is_connected = False
_notifier_fn = None  # callable(contact, text)


# ──────────────────────────────────────────────────────────────────────────────
# Iniciar / Parar processo bridge
# ─────────────────────────────────────────────────────────────────────────

def _start_bridge_process(visible: bool = True) -> bool:
    """
    Inicia o processo do kira_wpp/main.py.

    visible=True  → Chrome abre visível (para escanear QR Code)
    visible=False → Chrome roda em modo headless/invisível (após já ter sessão)

    ATENÇÃO: o whatsapp_controller.py do kira_wpp sempre abre Chrome com
    headless=False (requerido pelo WhatsApp Web). A invisibilidade é feita
    via win32gui ocultando a janela DEPOIS do login. Por isso usamos sempre
    --headless no main.py para evitar a GUI do PySide6, mas o Chrome em si
    sempre abre para fazer o trabalho — ele só some após o login.
    """
    global _bridge_process

    if _bridge_process and _bridge_process.poll() is None:
        logger.info("[BRIDGE] Processo já está rodando (PID %d).", _bridge_process.pid)
        return True

    kira_main = Path(__file__).parent / "kira_wpp" / "main.py"
    if not kira_main.exists():
        logger.error("[BRIDGE] Arquivo não encontrado: %s", kira_main)
        return False

    try:
        # Tenta liberar a porta 5050 antes de começar (limpeza preventiva)
        if sys.platform == "win32":
            subprocess.run("taskkill /f /im python.exe /fi \"windowtitle eq kira_wpp_bridge_server*\"", shell=True, capture_output=True)
            # Uma forma mais genérica de matar quem estiver na 5050
            subprocess.run("for /f \"tokens=5\" %a in ('netstat -ano ^| findstr :5050') do taskkill /f /pid %a", shell=True, capture_output=True)

        log_path = Path(__file__).parent / "bridge.log"
        log_file = open(log_path, "a", encoding="utf-8")
        log_file.write(f"\n--- [BRIDGE START] {Path(__file__).name} ---\n")
        log_file.flush()

        args = [sys.executable, str(kira_main), "--headless"]
        kwargs: dict = {
            "args": args,
            "stdout": log_file,
            "stderr": log_file,
            "bufsize": 1, # line buffered
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        _bridge_process = subprocess.Popen(**kwargs)
        logger.info("[BRIDGE] Processo iniciado (PID %d).", _bridge_process.pid)
        return True

    except Exception as exc:
        logger.error("[BRIDGE] Falha ao iniciar: %s", exc)
        return False


def stop_bridge_process():
    """Para o processo do bridge."""
    global _bridge_process, _is_connected
    _is_connected = False
    if _bridge_process and _bridge_process.poll() is None:
        logger.info("[BRIDGE] Encerrando processo (PID %d)...", _bridge_process.pid)
        _bridge_process.terminate()
        try:
            _bridge_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _bridge_process.kill()
    _bridge_process = None


# ──────────────────────────────────────────────────────────────────────────────
# Verificações de status
# ──────────────────────────────────────────────────────────────────────────────

async def _check_http_status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{BRIDGE_URL}/status")
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return {"connected": False, "controller_active": False}


async def _wait_bridge_http_online(timeout: int = 20) -> bool:
    """Aguarda o servidor HTTP na porta 5050 responder."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        status = await _check_http_status()
        if status.get("controller_active") or status.get("connected"):
            return True
        await asyncio.sleep(1.5)
    return False


async def _wait_whatsapp_connected(timeout: int = 300) -> bool:
    """
    Aguarda o WhatsApp estar efetivamente conectado (após login/QR).
    Timeout de 5 minutos para o usuário escanear o QR.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        status = await _check_http_status()
        if status.get("connected"):
            return True
        await asyncio.sleep(2.0)
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Monitor de mensagens recebidas
# ──────────────────────────────────────────────────────────────────────────────

async def _message_monitor_loop(stop_event: asyncio.Event):
    """Loop que consome /messages/new/agent e chama _notifier_fn para cada mensagem."""
    logger.info("[BRIDGE] Monitor de mensagens iniciado.")
    while not stop_event.is_set():
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{BRIDGE_URL}/messages/new/agent")
                if resp.status_code == 200:
                    msgs = resp.json().get("messages", [])
                    for msg in msgs:
                        contact = msg.get("contact", "Desconhecido")
                        text = msg.get("text", "")
                        if text and _notifier_fn:
                            logger.info("[BRIDGE] Nova mensagem de %s", contact)
                            asyncio.ensure_future(_notifier_fn(contact, text))
        except Exception as exc:
            logger.debug("[BRIDGE] Erro no monitor: %s", exc)

        # Aguarda intervalo ou sinal de parada
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

    logger.info("[BRIDGE] Monitor encerrado.")


def _start_monitor(notifier_fn):
    """Ativa o loop de monitoramento de mensagens."""
    global _monitor_task, _stop_event, _notifier_fn
    _notifier_fn = notifier_fn
    _stop_event = asyncio.Event()
    _monitor_task = asyncio.ensure_future(
        _message_monitor_loop(_stop_event)
    )


def stop_monitor():
    """Para o monitor de mensagens."""
    global _monitor_task, _stop_event
    if _stop_event:
        _stop_event.set()
    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()


# ──────────────────────────────────────────────────────────────────────────────
# API pública — chamada pelas tools do agente
# ──────────────────────────────────────────────────────────────────────────────

async def connect_whatsapp(agent, speech_coordinator) -> tuple[bool, str]:
    """
    Inicia a conexão em background para evitar timeout no agente.
    Retorna uma mensagem inicial e avisa por voz quando concluir.
    """
    global _is_connected

    if _is_connected:
        status = await _check_http_status()
        if status.get("connected"):
            return True, "WhatsApp já está conectado."

    # Passo 1: Inicia processo
    started = _start_bridge_process(visible=True)
    if not started:
        return False, "Não consegui abrir o bridge do WhatsApp."

    async def _async_connect_flow():
        # Aguarda HTTP
        if await _wait_bridge_http_online(timeout=20):
            # Avisa que o QR está pronto se necessário
            # (O navegador já abriu, o usuário deve ver)
            
            # Aguarda Login (longo)
            if await _wait_whatsapp_connected(timeout=300):
                global _is_connected
                _is_connected = True
                from whatsapp_runtime import build_whatsapp_notifier
                notifier = build_whatsapp_notifier(agent, speech_coordinator)
                _start_monitor(notifier)
                
                # Notificação proativa por voz
                await speech_coordinator.say("Chefe, o WhatsApp foi conectado com sucesso!")
            else:
                stop_bridge_process()
                await speech_coordinator.say("Chefe, o tempo para escanear o QR Code do WhatsApp esgotou.")
        else:
            stop_bridge_process()
            await speech_coordinator.say("Erro ao iniciar o servidor do WhatsApp.")

    # Dispara o flow longo em background
    asyncio.create_task(_async_connect_flow())

    return True, "Estou abrindo o navegador para você escanear o QR Code. Pode levar alguns segundos."


async def disconnect_whatsapp() -> tuple[bool, str]:
    """Para o monitor e encerra o processo bridge."""
    global _is_connected
    stop_monitor()
    stop_bridge_process()
    _is_connected = False
    return True, "WhatsApp desconectado."


def is_whatsapp_connected() -> bool:
    return _is_connected
