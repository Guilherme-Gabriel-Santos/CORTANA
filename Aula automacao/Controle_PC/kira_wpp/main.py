"""
main.py — Kira v3
Interface gráfica da assistente WhatsApp.
"""

import os
import re
import sys
import time
import threading

os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.qpa.*=false"

from PySide6.QtCore import Qt, QObject, Signal, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QTextEdit, QPlainTextEdit,
    QPushButton, QProgressBar, QFrame,
)

from whatsapp_controller import WhatsAppController

SENDER_NAME    = "Alan"
ASSISTANT_NAME = "Kira"


class Signals(QObject):
    progress   = Signal(int, str)
    log        = Signal(str)
    status     = Signal(str)
    connected  = Signal()
    message    = Signal(str, str)
    send_done  = Signal(bool, str)


class KiraWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kira — Assistente WhatsApp")
        self.resize(980, 700)
        self.setMinimumSize(800, 560)

        self._sig = Signals()
        self._wa  = None
        self._connected = False
        self._bridge_active = False
        self._msg_count = 0

        self._build_ui()
        self._wire_signals()

    # ─────────────────────── UI ─────────────────────────────────────────────

    def _build_ui(self):
        root_w = QWidget()
        self.setCentralWidget(root_w)
        root = QVBoxLayout(root_w)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── Header ──
        hdr = QFrame()
        hdr.setFixedHeight(56)
        hdr.setStyleSheet("background:#075E54; border-radius:8px;")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(18, 0, 18, 0)
        title = QLabel("🤖  Kira — Assistente WhatsApp")
        title.setStyleSheet("color:white; font-size:19px; font-weight:700; background:transparent;")
        hl.addWidget(title)
        hl.addStretch()
        self.status_lbl = QLabel("Aguardando conexão")
        self.status_lbl.setStyleSheet("color:#b2dfdb; font-size:11px; background:transparent;")
        hl.addWidget(self.status_lbl)
        root.addWidget(hdr)

        # ── Barra de progresso + botões ──
        pb_row = QHBoxLayout()
        self.prog = QProgressBar()
        self.prog.setRange(0, 100)
        self.prog.setValue(0)
        self.prog.setFixedHeight(20)
        self.prog.setStyleSheet("""
            QProgressBar{background:#111;border-radius:8px;border:none;color:white;font-size:10px;}
            QProgressBar::chunk{background:#25D366;border-radius:8px;}
        """)
        pb_row.addWidget(self.prog)

        self.btn_connect = QPushButton("🔌  Conectar")
        self.btn_connect.setFixedSize(140, 32)
        self.btn_connect.setStyleSheet(
            "background:#25D366;color:white;font-weight:700;border-radius:6px;font-size:12px;")
        self.btn_connect.clicked.connect(self._start_connect)
        pb_row.addWidget(self.btn_connect)

        self.btn_bridge = QPushButton("🔗  Bridge p/ Agente")
        self.btn_bridge.setFixedSize(160, 32)
        self.btn_bridge.setStyleSheet(
            "background:#7c6af7;color:white;font-weight:700;border-radius:6px;font-size:11px;")
        self.btn_bridge.clicked.connect(self._start_bridge)
        pb_row.addWidget(self.btn_bridge)

        self.bridge_lbl = QLabel("Bridge: OFF")
        self.bridge_lbl.setStyleSheet("color:gray;font-size:10px;")
        pb_row.addWidget(self.bridge_lbl)

        self.btn_disconnect = QPushButton("✖  Desconectar")
        self.btn_disconnect.setFixedSize(130, 32)
        self.btn_disconnect.setEnabled(False)
        self.btn_disconnect.setStyleSheet(
            "background:#c0392b;color:white;border-radius:6px;font-size:12px;")
        self.btn_disconnect.clicked.connect(self._disconnect)
        pb_row.addWidget(self.btn_disconnect)
        root.addLayout(pb_row)

        # ── Corpo ──
        body = QHBoxLayout()
        body.setSpacing(10)

        # Mensagens recebidas
        msg_box = QGroupBox("📨  Mensagens Recebidas")
        msg_box.setStyleSheet("QGroupBox{font-weight:700;font-size:13px;}")
        ml = QVBoxLayout(msg_box)
        self.feed = QPlainTextEdit()
        self.feed.setReadOnly(True)
        self.feed.setPlaceholderText("Kira monitorando... mensagens de contatos aparecerão aqui.")
        self.feed.setStyleSheet(
            "background:#0d1117;color:#e6edf3;font-family:Consolas;font-size:12px;"
            "border-radius:6px;border:1px solid #30363d;")
        ml.addWidget(self.feed)
        body.addWidget(msg_box, stretch=3)

        # Painel de envio
        send_box = QGroupBox("📤  Enviar Mensagem")
        send_box.setFixedWidth(340)
        send_box.setStyleSheet("QGroupBox{font-weight:700;font-size:13px;}")
        sl = QVBoxLayout(send_box)
        sl.setSpacing(8)

        sl.addWidget(QLabel("Seu nome:"))
        self.inp_sender = QLineEdit(SENDER_NAME)
        self.inp_sender.setFixedHeight(34)
        sl.addWidget(self.inp_sender)

        sl.addWidget(QLabel("Para (contato):"))
        self.inp_contact = QLineEdit()
        self.inp_contact.setPlaceholderText("Ex: Mãe, Edson, João...")
        self.inp_contact.setFixedHeight(34)
        sl.addWidget(self.inp_contact)

        sl.addWidget(QLabel("Sua mensagem:"))
        self.inp_msg = QTextEdit()
        self.inp_msg.setPlaceholderText("Ex: vou levar o café agora")
        self.inp_msg.setFixedHeight(90)
        sl.addWidget(self.inp_msg)

        self.preview_lbl = QLabel(f'🤖 "{ASSISTANT_NAME}: {SENDER_NAME} disse que vai levar o café."')
        self.preview_lbl.setWordWrap(True)
        self.preview_lbl.setStyleSheet(
            "background:#0d2618;color:#66bb6a;border-radius:6px;padding:8px;font-size:11px;")
        sl.addWidget(self.preview_lbl)

        self.btn_send = QPushButton("📨  Enviar pelo WhatsApp")
        self.btn_send.setFixedHeight(42)
        self.btn_send.setEnabled(False)
        self.btn_send.setStyleSheet(
            "background:#25D366;color:white;font-weight:700;border-radius:8px;font-size:13px;")
        self.btn_send.clicked.connect(self._send)
        sl.addWidget(self.btn_send)

        self.send_status = QLabel("")
        self.send_status.setWordWrap(True)
        self.send_status.setStyleSheet("font-size:11px;color:gray;")
        sl.addWidget(self.send_status)
        sl.addStretch()
        body.addWidget(send_box)
        root.addLayout(body, stretch=3)

        # ── Log ──
        log_box = QGroupBox("📋  Log")
        log_box.setMaximumHeight(130)
        log_box.setStyleSheet("QGroupBox{font-weight:700;font-size:11px;}")
        ll = QVBoxLayout(log_box)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(300)
        self.log_box.setStyleSheet(
            "background:#0d1117;color:#8b949e;font-family:Consolas;font-size:10px;"
            "border-radius:4px;border:1px solid #21262d;")
        ll.addWidget(self.log_box)
        root.addWidget(log_box)

        # Preview em tempo real
        self.inp_msg.textChanged.connect(self._update_preview)
        self.inp_sender.textChanged.connect(self._update_preview)

    def _wire_signals(self):
        s = self._sig
        s.progress.connect(self._on_progress)
        s.log.connect(self._on_log)
        s.status.connect(self.status_lbl.setText)
        s.connected.connect(self._on_connected)
        s.message.connect(self._on_message)
        s.send_done.connect(self._on_send_done)

    def _bridge_call(self, method_name, *args):
        if not self._bridge_active:
            return
        try:
            import whatsapp_bridge_server as bridge
            getattr(bridge, method_name)(*args)
        except Exception as exc:
            print(f"(bridge) {exc}")

    def _handle_controller_progress(self, value, message):
        self._sig.progress.emit(value, message)

    def _handle_controller_log(self, message):
        self._sig.log.emit(message)
        self._bridge_call("handle_log", message)

    def _handle_controller_message(self, contact, text):
        self._sig.message.emit(contact, text)
        self._bridge_call("handle_message", contact, text)

    def _handle_controller_status(self, status):
        self._sig.status.emit(status)
        self._bridge_call("handle_status", status)

    def _handle_controller_connected(self):
        self._sig.connected.emit()
        self._bridge_call("handle_connected")

    def _handle_controller_send_done(self, ok, message):
        self._sig.send_done.emit(ok, message)

    def _build_controller(self):
        wa = WhatsAppController(
            on_progress=self._handle_controller_progress,
            on_log=self._handle_controller_log,
            on_message=self._handle_controller_message,
            on_status=self._handle_controller_status,
            on_connected=self._handle_controller_connected,
            on_send_done=self._handle_controller_send_done,
        )
        self._wa = wa

        if self._bridge_active:
            from whatsapp_bridge_server import start_bridge
            start_bridge(controller=wa, auto_connect=False)

        return wa

    def _prepare_connect_ui(self):
        self.btn_connect.setEnabled(False)
        self.btn_connect.setText("⏳  Conectando...")
        self.prog.setValue(0)
        self.status_lbl.setStyleSheet("color:#b2dfdb;font-size:11px;background:transparent;")

    def _reset_connect_ui(self):
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("🔌  Conectar")
        self.prog.setValue(0)
        self.status_lbl.setText("Aguardando conexão")
        self.status_lbl.setStyleSheet("color:#b2dfdb;font-size:11px;background:transparent;")

    # ─────────────────────── slots ──────────────────────────────────────────

    @Slot(int, str)
    def _on_progress(self, v, msg):
        self.prog.setValue(v)
        if msg:
            self.status_lbl.setText(msg)

    @Slot(str)
    def _on_log(self, msg):
        stamp = time.strftime("%H:%M:%S")
        self.log_box.appendPlainText(f"[{stamp}] {msg}")
        self.log_box.moveCursor(QTextCursor.End)

    @Slot()
    def _on_connected(self):
        self._connected = True
        self.btn_connect.setEnabled(False)
        self.btn_disconnect.setEnabled(True)
        self.btn_send.setEnabled(True)
        self.status_lbl.setText("✅ WhatsApp conectado — monitorando")
        self.status_lbl.setStyleSheet("color:#66bb6a;font-size:11px;background:transparent;")

    @Slot(str, str)
    def _on_message(self, contact, raw):
        self._msg_count += 1
        stamp   = time.strftime("%H:%M")
        summary = f"Chefe, {contact} mandou: {raw[:100]}{'...' if len(raw)>100 else ''}"
        block = (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"[{stamp}]  👤 {contact}\n"
            f"🤖  {summary}\n"
            f"💬  Original: {raw[:120]}{'...' if len(raw)>120 else ''}\n"
        )
        self.feed.appendPlainText(block)
        self.feed.moveCursor(QTextCursor.End)
        self._on_log(f"📩 {contact}: {raw[:55]}")

    @Slot(bool, str)
    def _on_send_done(self, ok, msg):
        self.btn_send.setEnabled(True)
        self.btn_send.setText("📨  Enviar pelo WhatsApp")
        color = "#25D366" if ok else "#f44336"
        self.send_status.setStyleSheet(f"font-size:11px;color:{color};")
        self.send_status.setText(msg)
        if ok:
            self.inp_msg.clear()

    # ─────────────────────── ações ──────────────────────────────────────────

    def _start_connect(self):
        self._prepare_connect_ui()
        try:
            wa = self._build_controller()
            wa.start()
        except Exception as e:
            self._wa = None
            self._reset_connect_ui()
            self._on_log(f"Erro ao conectar: {e}")

    def _start_bridge(self):
        controller_started = False
        self.btn_bridge.setEnabled(False)
        self.btn_bridge.setText("⏳  Iniciando...")
        try:
            from whatsapp_bridge_server import start_bridge

            self._bridge_active = True
            if not self._wa:
                self._prepare_connect_ui()
                wa = self._build_controller()
                wa.start()
                controller_started = True
            else:
                start_bridge(controller=self._wa, auto_connect=False)

            self.bridge_lbl.setText("Bridge: ON :5050")
            self.bridge_lbl.setStyleSheet("color:#25D366;font-size:10px;font-weight:700;")
            self.btn_bridge.setText("🔗  Bridge ATIVA")
            self._on_log("Bridge HTTP iniciada na porta 5050.")
        except Exception as e:
            self._bridge_active = False
            self.btn_bridge.setEnabled(True)
            self.btn_bridge.setText("🔗  Bridge p/ Agente")
            self.bridge_lbl.setText("Bridge: OFF")
            self.bridge_lbl.setStyleSheet("color:gray;font-size:10px;")
            if not controller_started and not self._connected:
                self._wa = None
                self._reset_connect_ui()
            self._on_log(f"Erro ao iniciar bridge: {e}")

    def _disconnect(self):
        if self._wa:
            self._wa.stop()
        if self._bridge_active:
            try:
                from whatsapp_bridge_server import handle_disconnected
                handle_disconnected()
            except Exception:
                pass
        self._connected = False
        self._wa = None
        self.btn_connect.setEnabled(True)
        self.btn_connect.setText("🔌  Conectar")
        self.btn_disconnect.setEnabled(False)
        self.btn_send.setEnabled(False)
        self.prog.setValue(0)
        self.status_lbl.setText("Desconectado")
        self.status_lbl.setStyleSheet("color:#b2dfdb;font-size:11px;background:transparent;")
        self._on_log("Desconectado.")

    def _send(self):
        contact = self.inp_contact.text().strip()
        message = self.inp_msg.toPlainText().strip()
        sender  = self.inp_sender.text().strip() or SENDER_NAME

        if not contact:
            self.send_status.setText("⚠️  Informe o contato.")
            return
        if not message:
            self.send_status.setText("⚠️  Escreva a mensagem.")
            return
        if not self._wa or not self._connected:
            self.send_status.setText("⚠️  Conecte primeiro.")
            return

        formatted = self._format(sender, message)
        self.btn_send.setEnabled(False)
        self.btn_send.setText("⏳  Enviando...")
        self.send_status.setText("🤖 Kira processando...")
        self.send_status.setStyleSheet("font-size:11px;color:gray;")
        self._on_log(f"Enviando para {contact}: {formatted}")
        self._wa.request_send(contact, formatted)

    def _update_preview(self):
        sender  = self.inp_sender.text().strip() or SENDER_NAME
        message = self.inp_msg.toPlainText().strip() or "sua mensagem aqui"
        self.preview_lbl.setText(f'🤖 "{self._format(sender, message)}"')

    def _format(self, sender: str, text: str) -> str:
        text = re.sub(r"\s+", " ", text.strip())
        for pfx in ["avisando que ", "avisa que ", "fala que ", "diz que "]:
            if text.lower().startswith(pfx):
                text = text[len(pfx):].strip()
                break
        return f"{ASSISTANT_NAME}: {sender} disse que {text}"

    def closeEvent(self, event):
        if self._wa:
            self._wa.stop()
        if self._bridge_active:
            try:
                from whatsapp_bridge_server import handle_disconnected
                handle_disconnected()
            except Exception:
                pass
        super().closeEvent(event)


if __name__ == "__main__":
    import sys as _sys
    import os as _os

    # Modo headless: sem interface PySide6 — roda só a bridge HTTP
    # Ativado quando:
    #   a) argumento --headless passado na linha de comando
    #   b) variável de ambiente KIRA_WPP_HEADLESS=1
    #   c) não há display disponível (Linux headless)
    headless = (
        "--headless" in _sys.argv
        or _os.environ.get("KIRA_WPP_HEADLESS", "0") == "1"
    )

    if headless:
        # ── MODO HEADLESS: bridge + controller, zero janela ───────────────
        print("[KIRA WPP] Modo headless — sem interface gráfica.")
        from whatsapp_bridge_server import (
            _ensure_admin_server_started,
            start_bridge,
            handle_disconnected,
        )
        import time as _time

        _ensure_admin_server_started()
        ctrl = start_bridge(auto_connect=True)
        print("[KIRA WPP] Bridge ativa. Aguardando...")
        try:
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            handle_disconnected()
            ctrl.stop()
    else:
        # ── MODO NORMAL: abre a janela PySide6 ────────────────────────────
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        win = KiraWindow()
        win.show()
        sys.exit(app.exec())
