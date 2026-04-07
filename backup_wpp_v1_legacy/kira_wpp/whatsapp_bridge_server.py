"""
Servidor HTTP que expoe o WhatsApp Controller para o agente Gemini.
Roda na porta 5050.
"""

import asyncio
import threading
import time
import uuid
from collections import deque
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from whatsapp_controller import WhatsAppController


def _safe(text: str) -> str:
    """Sanitiza texto para saída segura em terminais Windows (cp1252)."""
    if not isinstance(text, str):
        text = str(text)
    return text.encode("utf-8", errors="replace").decode("utf-8")


app = FastAPI(title="Kira WhatsApp Bridge")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Servidor admin (porta 5100) — usado pelo kira_app.py para acionar a bridge
_admin_app = FastAPI(title="Kira WhatsApp Admin")
_admin_app.add_middleware(
    CORSMiddleware,
    allow_origins=["127.0.0.1", "localhost"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@_admin_app.get("/start_bridge")
def admin_start_bridge():
    """
    Chamado pelo kira_app.py automaticamente após iniciar o WhatsApp Controller.
    Garante que a bridge HTTP (porta 5050) está ativa e o controller conectado.
    """
    try:
        # Se já tem controller registrado e conectado, retorna OK imediatamente
        with _state_lock:
            already = _connected and _controller is not None
        if already:
            return {"status": "already_connected"}

        # Cria e registra um controller se ainda não existir
        with _state_lock:
            has_ctrl = _controller is not None

        if not has_ctrl:
            ctrl = WhatsAppController(
                on_message=handle_message,
                on_log=handle_log,
                on_connected=handle_connected,
                on_status=handle_status,
                on_progress=lambda v, m: None,
                on_send_done=lambda ok, m: None,
            )
            _register_controller(ctrl)
            ctrl.start()

        _ensure_server_started()
        return {"status": "bridge_started"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def _run_admin_server():
    uvicorn.run(_admin_app, host="127.0.0.1", port=5100, log_level="error")


def _ensure_admin_server_started():
    """Inicia o servidor admin (porta 5100) em thread daemon."""
    t = threading.Thread(target=_run_admin_server, daemon=True)
    t.start()


# Estado global
_controller: Optional[WhatsAppController] = None
_connected = False
_log_buffer = deque(maxlen=100)
_message_queue_ui    = deque(maxlen=200)   # consumida pela interface web
_message_queue_agent = deque(maxlen=200)   # consumida pelo agente Gemini
_server_thread: Optional[threading.Thread] = None
_state_lock = threading.Lock()


class SendRequest(BaseModel):
    contact: str
    message: str


def _stamp() -> str:
    return time.strftime("%H:%M:%S")


def _append_log(message: str):
    with _state_lock:
        _log_buffer.append(f"[{_stamp()}] {_safe(message)}")


def handle_message(contact: str, text: str):
    contact = _safe(contact)
    text = _safe(text)
    payload = {
        "id": str(uuid.uuid4())[:8],
        "contact": contact,
        "text": text,
        "timestamp": _stamp(),
    }
    with _state_lock:
        _message_queue_ui.append(payload)       # para a interface
        _message_queue_agent.append(payload)    # para o agente Gemini
    _append_log(f"📩 {contact}: {text[:60]}")


def handle_log(message: str):
    _append_log(message)


def handle_connected():
    global _connected
    with _state_lock:
        _connected = True
    _append_log("✅ WhatsApp conectado")


def handle_status(status: str):
    _append_log(f"STATUS: {status}")


def handle_disconnected():
    global _controller, _connected
    with _state_lock:
        _controller = None
        _connected = False
    _append_log("WhatsApp desconectado")


def _register_controller(controller: WhatsAppController) -> WhatsAppController:
    global _controller, _connected
    with _state_lock:
        _controller = controller
        _connected = bool(getattr(controller, "_connected", False))
    return controller


def _run_server():
    uvicorn.run(app, host="127.0.0.1", port=5050, log_level="error")


def _ensure_server_started():
    global _server_thread
    with _state_lock:
        if _server_thread and _server_thread.is_alive():
            return

        _server_thread = threading.Thread(target=_run_server, daemon=True)
        _server_thread.start()

    time.sleep(0.5)
    if not _server_thread or not _server_thread.is_alive():
        raise RuntimeError("Nao foi possivel iniciar o servidor HTTP na porta 5050.")

    _append_log("Bridge HTTP ativa em http://127.0.0.1:5050")


@app.get("/status")
def status():
    with _state_lock:
        return {
            "connected": _connected,
            "controller_active": _controller is not None,
        }


@app.get("/messages/new")
def get_new_messages():
    """Consumido pela interface web (JavaScript frontend)."""
    with _state_lock:
        messages = list(_message_queue_ui)
        _message_queue_ui.clear()
    return {"messages": messages}


@app.get("/messages/new/agent")
def get_new_messages_agent():
    """Consumido pelo agente Gemini (integrations/whatsapp_monitor.py)."""
    with _state_lock:
        messages = list(_message_queue_agent)
        _message_queue_agent.clear()
    return {"messages": messages}


@app.post("/send")
async def send_message(req: SendRequest):
    with _state_lock:
        controller = _controller
        connected = _connected

    if not controller or not connected:
        raise HTTPException(status_code=503, detail="WhatsApp nao esta conectado.")

    result = {"success": False, "message": "Timeout"}
    done_event = threading.Event()

    def _on_done(ok: bool, message: str):
        result["success"] = ok
        result["message"] = message
        done_event.set()

    controller.request_send(
        req.contact,
        req.message,
        on_done=_on_done,
        notify_global=False,
    )

    finished = await asyncio.to_thread(done_event.wait, 35)
    if not finished:
        _append_log(f"Timeout no envio para {req.contact}")
    return result


@app.get("/log")
def get_log():
    with _state_lock:
        return {"log": list(_log_buffer)}


def start_bridge(controller: Optional[WhatsAppController] = None,
                 auto_connect: bool = True) -> WhatsAppController:
    """
    Inicia o servidor HTTP e registra o controller usado pela bridge.
    Se nenhum controller for fornecido, cria um controller proprio.
    """
    created_controller = False
    target = controller

    if target is None:
        with _state_lock:
            target = _controller

    if target is None:
        created_controller = True
        target = WhatsAppController(
            on_message=handle_message,
            on_log=handle_log,
            on_connected=handle_connected,
            on_status=handle_status,
            on_progress=lambda v, m: None,
            on_send_done=lambda ok, m: None,
        )

    _register_controller(target)
    _ensure_server_started()

    if created_controller and auto_connect:
        target.start()

    return target


if __name__ == "__main__":
    print("[KIRA WHATSAPP BRIDGE] Iniciando servidores nas portas 5050 e 5100...")
    _ensure_admin_server_started()
    ctrl = start_bridge(auto_connect=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        handle_disconnected()
        ctrl.stop()
        print("[KIRA WHATSAPP BRIDGE] Encerrado.")
