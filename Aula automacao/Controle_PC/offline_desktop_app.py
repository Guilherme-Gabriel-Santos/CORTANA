from __future__ import annotations

import html
import logging
import sys
from pathlib import Path
from typing import Any, Callable

from PySide6 import QtCore, QtGui, QtWidgets

from offline_runtime import OfflineCortanaApp, _load_environment
from shared_memory import DB_PATH


LOGGER = logging.getLogger("cortana.offline.desktop")


class AtmosphereCanvas(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._phase = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(45)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.012) % 1.0
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        background = QtGui.QLinearGradient(rect.topLeft(), rect.bottomRight())
        background.setColorAt(0.0, QtGui.QColor("#050711"))
        background.setColorAt(0.45, QtGui.QColor("#090d1a"))
        background.setColorAt(1.0, QtGui.QColor("#03050d"))
        painter.fillRect(rect, background)

        orb_a = QtGui.QRadialGradient(
            QtCore.QPointF(rect.width() * (0.18 + self._phase * 0.05), rect.height() * 0.16),
            rect.width() * 0.32,
        )
        orb_a.setColorAt(0.0, QtGui.QColor(188, 19, 254, 120))
        orb_a.setColorAt(0.35, QtGui.QColor(91, 77, 255, 80))
        orb_a.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
        painter.fillRect(rect, orb_a)

        orb_b = QtGui.QRadialGradient(
            QtCore.QPointF(rect.width() * 0.88, rect.height() * (0.76 - self._phase * 0.06)),
            rect.width() * 0.28,
        )
        orb_b.setColorAt(0.0, QtGui.QColor(76, 208, 255, 65))
        orb_b.setColorAt(0.4, QtGui.QColor(50, 110, 255, 40))
        orb_b.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
        painter.fillRect(rect, orb_b)

        pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 10))
        pen.setWidth(1)
        painter.setPen(pen)
        spacing = 46
        for x in range(0, rect.width(), spacing):
            painter.drawLine(x, 0, x, rect.height())
        for y in range(0, rect.height(), spacing):
            painter.drawLine(0, y, rect.width(), y)

        super().paintEvent(event)


class FrostPanel(QtWidgets.QFrame):
    def __init__(self, panel_name: str | None = None) -> None:
        super().__init__()
        self.setObjectName(panel_name or "panel")


class FunctionWorker(QtCore.QThread):
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class CortanaOfflineWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        _load_environment()

        self.backend = OfflineCortanaApp(text_only=False, tts_enabled=True)
        self.current_worker: FunctionWorker | None = None

        self.setWindowTitle("Cortana Offline")
        self.resize(1360, 840)
        self.setMinimumSize(1120, 700)

        self._build_ui()
        self._apply_styles()
        self._load_settings_into_ui()
        QtCore.QTimer.singleShot(0, self.bootstrap_backend)

    def _build_ui(self) -> None:
        canvas = AtmosphereCanvas()
        self.setCentralWidget(canvas)

        root = QtWidgets.QHBoxLayout(canvas)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(18)

        left_column = QtWidgets.QVBoxLayout()
        left_column.setSpacing(18)
        root.addLayout(left_column, 5)

        self.hero_panel = FrostPanel("heroPanel")
        hero_layout = QtWidgets.QVBoxLayout(self.hero_panel)
        hero_layout.setContentsMargins(26, 24, 26, 24)
        hero_layout.setSpacing(10)

        eyebrow = QtWidgets.QLabel("VERSAO OFFLINE")
        eyebrow.setObjectName("eyebrow")
        self.title_label = QtWidgets.QLabel("Cortana")
        self.title_label.setObjectName("title")
        subtitle = QtWidgets.QLabel(
            "Assistente local com voz, Face ID opcional e memoria compartilhada com a versao online."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        chip_row = QtWidgets.QHBoxLayout()
        chip_row.setSpacing(10)
        self.model_chip = QtWidgets.QLabel(f"Modelo local  {self.backend.model_name}")
        self.voice_chip = QtWidgets.QLabel("Voz  carregando")
        self.memory_chip = QtWidgets.QLabel(f"Memoria  {Path(DB_PATH).as_posix()}")
        for chip in (self.model_chip, self.voice_chip, self.memory_chip):
            chip.setObjectName("chip")
            chip_row.addWidget(chip)
        chip_row.addStretch(1)

        hero_layout.addWidget(eyebrow)
        hero_layout.addWidget(self.title_label)
        hero_layout.addWidget(subtitle)
        hero_layout.addLayout(chip_row)
        left_column.addWidget(self.hero_panel)

        self.chat_panel = FrostPanel("chatPanel")
        chat_layout = QtWidgets.QVBoxLayout(self.chat_panel)
        chat_layout.setContentsMargins(18, 18, 18, 18)
        chat_layout.setSpacing(12)

        chat_header = QtWidgets.QHBoxLayout()
        chat_title = QtWidgets.QLabel("Workspace")
        chat_title.setObjectName("sectionTitle")
        self.status_label = QtWidgets.QLabel("Inicializando backend local...")
        self.status_label.setObjectName("statusPill")
        chat_header.addWidget(chat_title)
        chat_header.addStretch(1)
        chat_header.addWidget(self.status_label)

        self.chat_view = QtWidgets.QTextBrowser()
        self.chat_view.setReadOnly(True)
        self.chat_view.setOpenExternalLinks(False)

        chat_layout.addLayout(chat_header)
        chat_layout.addWidget(self.chat_view, 1)
        left_column.addWidget(self.chat_panel, 1)

        self.composer_panel = FrostPanel("composerPanel")
        composer_layout = QtWidgets.QVBoxLayout(self.composer_panel)
        composer_layout.setContentsMargins(18, 18, 18, 18)
        composer_layout.setSpacing(12)

        composer_header = QtWidgets.QHBoxLayout()
        composer_title = QtWidgets.QLabel("Conversa")
        composer_title.setObjectName("sectionTitle")
        composer_hint = QtWidgets.QLabel("Digite ou use o botao de voz para falar com a Cortana.")
        composer_hint.setObjectName("sectionHint")
        composer_header.addWidget(composer_title)
        composer_header.addStretch(1)
        composer_header.addWidget(composer_hint)

        self.input_edit = QtWidgets.QPlainTextEdit()
        self.input_edit.setPlaceholderText("Pergunte algo para a Cortana offline...")
        self.input_edit.setFixedHeight(118)

        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(10)
        self.send_button = QtWidgets.QPushButton("Enviar")
        self.send_button.clicked.connect(self.send_text)
        self.voice_button = QtWidgets.QPushButton("Falar")
        self.voice_button.clicked.connect(self.run_voice_turn)
        self.test_voice_button = QtWidgets.QPushButton("Testar voz")
        self.test_voice_button.clicked.connect(self.test_voice)
        action_row.addStretch(1)
        action_row.addWidget(self.test_voice_button)
        action_row.addWidget(self.voice_button)
        action_row.addWidget(self.send_button)

        composer_layout.addLayout(composer_header)
        composer_layout.addWidget(self.input_edit)
        composer_layout.addLayout(action_row)
        left_column.addWidget(self.composer_panel)

        self.sidebar = FrostPanel("sidebarPanel")
        self.sidebar.setFixedWidth(340)
        sidebar_layout = QtWidgets.QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(18, 18, 18, 18)
        sidebar_layout.setSpacing(16)
        root.addWidget(self.sidebar, 2)

        sidebar_title = QtWidgets.QLabel("Controle Local")
        sidebar_title.setObjectName("sidebarTitle")
        sidebar_layout.addWidget(sidebar_title)

        self.runtime_panel = FrostPanel("railPanel")
        runtime_layout = QtWidgets.QVBoxLayout(self.runtime_panel)
        runtime_layout.setContentsMargins(14, 14, 14, 14)
        runtime_layout.setSpacing(10)
        runtime_title = QtWidgets.QLabel("Sessao")
        runtime_title.setObjectName("railTitle")
        runtime_desc = QtWidgets.QLabel("Sincronize a memoria da online e acompanhe o estado do runtime local.")
        runtime_desc.setObjectName("railText")
        runtime_desc.setWordWrap(True)
        self.sync_button = QtWidgets.QPushButton("Importar memoria da online")
        self.sync_button.clicked.connect(self.sync_online_memory)
        runtime_layout.addWidget(runtime_title)
        runtime_layout.addWidget(runtime_desc)
        runtime_layout.addWidget(self.sync_button)
        sidebar_layout.addWidget(self.runtime_panel)

        self.voice_panel = FrostPanel("railPanel")
        voice_layout = QtWidgets.QVBoxLayout(self.voice_panel)
        voice_layout.setContentsMargins(14, 14, 14, 14)
        voice_layout.setSpacing(10)
        voice_title = QtWidgets.QLabel("Voz")
        voice_title.setObjectName("railTitle")
        voice_desc = QtWidgets.QLabel(
            "Edge TTS entrega uma voz muito melhor, mas volta a depender de internet para falar."
        )
        voice_desc.setObjectName("railText")
        voice_desc.setWordWrap(True)
        self.speak_checkbox = QtWidgets.QCheckBox("Falar respostas")
        self.speak_checkbox.toggled.connect(self.on_speak_toggled)
        self.provider_combo = QtWidgets.QComboBox()
        self.provider_combo.addItem("Edge TTS", "edge")
        self.provider_combo.addItem("Windows local", "local")
        self.provider_combo.currentIndexChanged.connect(self.on_provider_changed)
        self.voice_combo = QtWidgets.QComboBox()
        self.voice_combo.currentIndexChanged.connect(self.on_voice_changed)
        voice_layout.addWidget(voice_title)
        voice_layout.addWidget(voice_desc)
        voice_layout.addWidget(self.speak_checkbox)
        voice_layout.addWidget(self.provider_combo)
        voice_layout.addWidget(self.voice_combo)
        sidebar_layout.addWidget(self.voice_panel)

        self.mic_panel = FrostPanel("railPanel")
        mic_layout = QtWidgets.QVBoxLayout(self.mic_panel)
        mic_layout.setContentsMargins(14, 14, 14, 14)
        mic_layout.setSpacing(10)
        mic_title = QtWidgets.QLabel("Microfone")
        mic_title.setObjectName("railTitle")
        mic_desc = QtWidgets.QLabel(
            "Escolha o microfone certo. A captura agora tenta fallback automatico e aceita volume mais baixo."
        )
        mic_desc.setObjectName("railText")
        mic_desc.setWordWrap(True)
        self.mic_combo = QtWidgets.QComboBox()
        self.mic_combo.currentIndexChanged.connect(self.on_mic_changed)
        self.refresh_mics_button = QtWidgets.QPushButton("Atualizar microfones")
        self.refresh_mics_button.clicked.connect(self.refresh_microphones)
        mic_layout.addWidget(mic_title)
        mic_layout.addWidget(mic_desc)
        mic_layout.addWidget(self.mic_combo)
        mic_layout.addWidget(self.refresh_mics_button)
        sidebar_layout.addWidget(self.mic_panel)

        sidebar_layout.addStretch(1)

        footer = QtWidgets.QLabel(
            "A online continua intacta. Esta interface usa a mesma memoria compartilhada e pode ser empacotada como app desktop."
        )
        footer.setObjectName("footer")
        footer.setWordWrap(True)
        sidebar_layout.addWidget(footer)

        self._append_system_message("App desktop iniciado. Preparando modelo local, voz e memoria compartilhada.")
        self._set_busy(True)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                color: #eef2ff;
                font-family: "Segoe UI";
                font-size: 14px;
                background: transparent;
            }
            QFrame#heroPanel, QFrame#chatPanel, QFrame#composerPanel, QFrame#sidebarPanel, QFrame#railPanel {
                background: rgba(9, 13, 27, 0.82);
                border: 1px solid rgba(188, 19, 254, 0.22);
                border-radius: 24px;
            }
            QFrame#heroPanel {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 rgba(18, 12, 40, 0.94),
                    stop:0.45 rgba(10, 12, 24, 0.92),
                    stop:1 rgba(7, 10, 20, 0.88));
            }
            QLabel#eyebrow {
                color: #7cd8ff;
                letter-spacing: 2px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#title {
                font-size: 48px;
                font-weight: 800;
                color: #ffffff;
            }
            QLabel#subtitle {
                color: #c3c9db;
                font-size: 15px;
            }
            QLabel#chip {
                padding: 9px 14px;
                border-radius: 999px;
                background: rgba(17, 24, 41, 0.88);
                border: 1px solid rgba(124, 216, 255, 0.2);
                color: #dbe4ff;
            }
            QLabel#sectionTitle, QLabel#sidebarTitle, QLabel#railTitle {
                font-weight: 700;
                color: #f8fbff;
            }
            QLabel#sectionTitle {
                font-size: 18px;
            }
            QLabel#sidebarTitle {
                font-size: 20px;
            }
            QLabel#railTitle {
                font-size: 16px;
            }
            QLabel#sectionHint, QLabel#railText, QLabel#footer {
                color: #9aa7bf;
            }
            QLabel#statusPill {
                padding: 7px 12px;
                border-radius: 999px;
                background: rgba(26, 44, 33, 0.9);
                border: 1px solid rgba(111, 255, 174, 0.28);
                color: #bdfed2;
                font-weight: 700;
            }
            QTextBrowser {
                background: rgba(3, 7, 18, 0.8);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 18px;
                padding: 14px;
                selection-background-color: rgba(188, 19, 254, 0.45);
            }
            QPlainTextEdit, QComboBox {
                background: rgba(4, 8, 19, 0.92);
                border: 1px solid rgba(124, 216, 255, 0.16);
                border-radius: 16px;
                padding: 12px 14px;
                selection-background-color: rgba(188, 19, 254, 0.45);
            }
            QPlainTextEdit {
                font-size: 15px;
            }
            QComboBox::drop-down {
                border: none;
                width: 22px;
            }
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #7b2fbe,
                    stop:1 #bc13fe);
                border: none;
                border-radius: 14px;
                padding: 12px 16px;
                color: white;
                font-weight: 700;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #8d3fe0,
                    stop:1 #cf45ff);
            }
            QPushButton:disabled {
                background: rgba(66, 77, 102, 0.7);
                color: #a9b4cb;
            }
            QCheckBox {
                color: #d8deee;
            }
            """
        )

        title_shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        title_shadow.setBlurRadius(30)
        title_shadow.setOffset(0, 0)
        title_shadow.setColor(QtGui.QColor(188, 19, 254, 180))
        self.title_label.setGraphicsEffect(title_shadow)

    def _append_html(self, html_block: str) -> None:
        self.chat_view.append(html_block)
        scroll = self.chat_view.verticalScrollBar()
        scroll.setValue(scroll.maximum())

    def _append_system_message(self, text: str) -> None:
        safe = html.escape(text)
        self._append_html(
            "<div style='margin:8px 0;padding:12px 14px;border-radius:16px;"
            "background:rgba(21,31,56,0.95);color:#8ed9ff;border:1px solid rgba(124,216,255,0.18);'>"
            f"<b>Sistema</b><br>{safe}</div>"
        )

    def _append_user_message(self, text: str) -> None:
        safe = html.escape(text)
        self._append_html(
            "<div style='margin:10px 0 10px 120px;padding:14px 16px;border-radius:18px;"
            "background:rgba(24,53,92,0.92);border:1px solid rgba(124,216,255,0.15);'>"
            f"<b>Voce</b><br>{safe}</div>"
        )

    def _append_assistant_message(self, text: str) -> None:
        safe = html.escape(text)
        self._append_html(
            "<div style='margin:10px 120px 10px 0;padding:14px 16px;border-radius:18px;"
            "background:rgba(30,18,48,0.94);border:1px solid rgba(188,19,254,0.22);'>"
            f"<b>Cortana</b><br>{safe}</div>"
        )

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        for widget in (
            self.input_edit,
            self.send_button,
            self.voice_button,
            self.test_voice_button,
            self.sync_button,
            self.provider_combo,
            self.voice_combo,
            self.mic_combo,
            self.refresh_mics_button,
        ):
            widget.setDisabled(busy)
        if message is not None:
            self.status_label.setText(message)

    def _start_worker(
        self,
        fn: Callable[..., Any],
        *,
        on_success: Callable[[Any], None],
        busy_message: str,
        on_error_message: str,
    ) -> None:
        if self.current_worker is not None:
            return

        worker = FunctionWorker(fn)
        self.current_worker = worker
        self._set_busy(True, busy_message)

        def _finish() -> None:
            self.current_worker = None
            self._set_busy(False, "Pronta.")

        def _on_success(result: Any) -> None:
            _finish()
            on_success(result)

        def _on_failed(error: str) -> None:
            _finish()
            self._append_system_message(f"{on_error_message}: {error}")

        worker.completed.connect(_on_success)
        worker.failed.connect(_on_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _load_settings_into_ui(self) -> None:
        self.speak_checkbox.blockSignals(True)
        self.speak_checkbox.setChecked(self.backend.speaker.enabled)
        self.speak_checkbox.blockSignals(False)

        provider = self.backend.speaker.provider
        provider_index = max(self.provider_combo.findData(provider), 0)
        self.provider_combo.blockSignals(True)
        self.provider_combo.setCurrentIndex(provider_index)
        self.provider_combo.blockSignals(False)

        self.refresh_voice_options()
        self.refresh_microphones()
        self.voice_chip.setText(f"Voz  {self.backend.speaker.describe_voice()}")

    def refresh_voice_options(self) -> None:
        provider = self.provider_combo.currentData() or self.backend.speaker.provider
        options = self.backend.available_voice_options().get(provider, [])

        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for option in options:
            label = option.get("label") or option.get("name") or option.get("id") or "voz"
            if option.get("description"):
                label = f"{label}  {option['description']}"
            self.voice_combo.addItem(label, option.get("id") or option.get("name"))

        if provider == "edge":
            target = self.backend.speaker.edge_voice
        else:
            target = self.backend.speaker.voice_name
        index = self.voice_combo.findData(target)
        if index < 0:
            index = 0
        if index >= 0:
            self.voice_combo.setCurrentIndex(index)
        self.voice_combo.blockSignals(False)

    def refresh_microphones(self) -> None:
        devices = self.backend.available_input_devices()
        self.mic_combo.blockSignals(True)
        self.mic_combo.clear()
        for device in devices:
            self.mic_combo.addItem(device["label"], device["id"])
        current = self.backend.current_input_device()
        index = self.mic_combo.findData(current)
        if index >= 0:
            self.mic_combo.setCurrentIndex(index)
        self.mic_combo.blockSignals(False)

    def bootstrap_backend(self) -> None:
        self._start_worker(
            self.backend.bootstrap,
            on_success=lambda _result: self._append_system_message(
                f"Modelo local pronto. Voz ativa em {self.backend.speaker.describe_voice()}."
            ),
            busy_message="Inicializando modelo local, audio e Face ID...",
            on_error_message="Falha ao iniciar a Cortana offline",
        )

    def sync_online_memory(self) -> None:
        self._start_worker(
            self.backend.sync_online_memory,
            on_success=lambda stats: self._append_system_message(
                "Memoria online importada: "
                f"{stats['fetched']} lidas, {stats['inserted']} novas, {stats['updated']} ja existentes."
            ),
            busy_message="Importando memoria da versao online...",
            on_error_message="Falha ao importar a memoria da online",
        )

    def send_text(self) -> None:
        user_text = self.input_edit.toPlainText().strip()
        if not user_text:
            return

        self.input_edit.clear()
        self._append_user_message(user_text)
        speak = self.speak_checkbox.isChecked()

        self._start_worker(
            lambda: self.backend.handle_text_input(user_text, speak=speak),
            on_success=lambda reply: self._append_assistant_message(str(reply)),
            busy_message="Processando mensagem local...",
            on_error_message="Falha ao responder no modo texto",
        )

    def run_voice_turn(self) -> None:
        speak = self.speak_checkbox.isChecked()
        current_mic = self.mic_combo.currentText() or "microfone padrao"
        self._append_system_message(f"Gravando do microfone selecionado: {current_mic}")

        self._start_worker(
            lambda: self.backend.handle_voice_input(speak=speak),
            on_success=self._handle_voice_result,
            busy_message="Ouvindo voce e processando localmente...",
            on_error_message="Falha ao processar voz local",
        )

    def test_voice(self) -> None:
        sample = "Cortana offline pronta. Voz e interface carregadas."
        self._start_worker(
            lambda: self.backend.speak_text(sample),
            on_success=lambda _result: self._append_system_message("Teste de voz concluido."),
            busy_message="Reproduzindo voz configurada...",
            on_error_message="Falha ao testar a voz",
        )

    def _handle_voice_result(self, payload: Any) -> None:
        transcript, reply = payload
        if not transcript:
            self._append_system_message(
                "Nenhuma fala foi detectada. Se necessario, troque o microfone no painel lateral."
            )
            return
        self._append_user_message(transcript)
        self._append_assistant_message(reply)

    def on_speak_toggled(self, checked: bool) -> None:
        self.backend.set_tts_enabled(bool(checked))

    def on_provider_changed(self) -> None:
        provider = self.provider_combo.currentData() or "edge"
        self.refresh_voice_options()
        voice_id = self.voice_combo.currentData()
        if voice_id:
            self.backend.configure_tts(provider=provider, voice_id=str(voice_id))
            self.voice_chip.setText(f"Voz  {self.backend.speaker.describe_voice()}")

    def on_voice_changed(self) -> None:
        provider = self.provider_combo.currentData() or "edge"
        voice_id = self.voice_combo.currentData()
        if not voice_id:
            return
        self.backend.configure_tts(provider=provider, voice_id=str(voice_id))
        self.voice_chip.setText(f"Voz  {self.backend.speaker.describe_voice()}")

    def on_mic_changed(self) -> None:
        device_id = self.mic_combo.currentData()
        self.backend.set_input_device(device_id)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self.backend.shutdown()
        finally:
            super().closeEvent(event)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    window = CortanaOfflineWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
