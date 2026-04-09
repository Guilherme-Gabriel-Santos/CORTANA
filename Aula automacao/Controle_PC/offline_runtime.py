from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import edge_tts
import pygame
import pyttsx3
import requests
import sounddevice as sd
import soundfile as sf
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from openai import OpenAI

from automacao_cortana import CortanaControl
from cloud_memory_sync import sync_mem0_to_shared
from face_auth import FaceAuthManager
from offline_prompts import OFFLINE_SYSTEM_PROMPT
from shared_memory import shared_memory

LOGGER = logging.getLogger("cortana.offline")
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = Path(os.getenv("CORTANA_PROJECT_DIR") or os.getcwd()).resolve()
OFFLINE_ENV_PATH = PROJECT_DIR / ".env.offline"
OLLAMA_API_URL = "http://127.0.0.1:11434/api/tags"
OLLAMA_OPENAI_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "llama3.2:3b"
EXIT_COMMANDS = {"sair", "exit", "quit", "fechar", "encerrar"}
TOOL_TRIGGER_KEYWORDS = (
    "abra ",
    "abrir ",
    "feche ",
    "fechar ",
    "crie ",
    "criar ",
    "delete ",
    "delet",
    "apague ",
    "remova ",
    "mova ",
    "copie ",
    "renome",
    "organize ",
    "compacte ",
    "volume",
    "brilho",
    "desliga",
    "reinicia",
    "suspende",
    "wake on lan",
    "memorize",
    "memoriz",
    "aprenda que",
    "guarde que",
    "lembre que",
    "o que voce sabe",
    "pesquise no passado",
    "pesquisa no passado",
    "historico",
    "memoria",
    "face id",
    "perfil facial",
    "modo game",
    "arquivo",
    "pasta",
)
DIRECT_LEARN_PREFIXES = (
    "aprenda que ",
    "guarde que ",
    "lembre que ",
    "memorize que ",
    "memoriza que ",
)
DIRECT_MEMORY_QUERIES = (
    "o que voce sabe sobre mim",
    "o que você sabe sobre mim",
    "quem sou eu",
    "me descreva",
)
DIRECT_NAME_QUERIES = (
    "qual e meu nome",
    "qual é meu nome",
    "como e meu nome",
    "como é meu nome",
    "meu nome",
)
DIRECT_BEST_FRIEND_QUERIES = (
    "qual o meu melhor amigo",
    "qual o meu melhor amigo?",
    "quem e meu melhor amigo",
    "quem é meu melhor amigo",
    "meu melhor amigo",
)
DIRECT_SEARCH_PREFIXES = (
    "pesquise no passado ",
    "pesquisa no passado ",
    "procure no passado ",
    "busque no passado ",
)


def _load_environment() -> None:
    load_dotenv(PROJECT_DIR / ".env", override=False)
    load_dotenv(OFFLINE_ENV_PATH, override=True)


def save_offline_setting(name: str, value: str) -> None:
    current_lines: list[str] = []
    if OFFLINE_ENV_PATH.exists():
        current_lines = OFFLINE_ENV_PATH.read_text(encoding="utf-8").splitlines()

    updated_lines: list[str] = []
    replaced = False
    for line in current_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key == name:
                updated_lines.append(f"{name}={value}")
                replaced = True
                continue
        updated_lines.append(line)

    if not replaced:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.append(f"{name}={value}")

    OFFLINE_ENV_PATH.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")
    os.environ[name] = value


DIRECT_MEMORY_QUERIES = (
    "o que voce sabe sobre mim",
    "quem sou eu",
    "me descreva",
)
DIRECT_NAME_QUERIES = (
    "qual e meu nome",
    "como e meu nome",
    "meu nome",
)
DIRECT_BEST_FRIEND_QUERIES = (
    "qual o meu melhor amigo",
    "qual o meu melhor amigo?",
    "quem e meu melhor amigo",
    "meu melhor amigo",
)
EDGE_VOICE_PRESETS = (
    {
        "id": "pt-BR-ThalitaMultilingualNeural",
        "label": "Thalita",
        "description": "Feminina, amigavel e positiva",
    },
    {
        "id": "pt-BR-FranciscaNeural",
        "label": "Francisca",
        "description": "Feminina, limpa e natural",
    },
)


def _env_flag(name: str, default: str = "0") -> bool:
    value = os.getenv(name, default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_device(value: str | None):
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.isdigit():
        return int(cleaned)
    return cleaned


def _default_input_device():
    default_device = sd.default.device
    if isinstance(default_device, (list, tuple)) and default_device:
        return default_device[0]
    if isinstance(default_device, int):
        return default_device
    return None


def _offline_face_flag() -> bool:
    if "OFFLINE_FACE_AUTH_REQUIRED" in os.environ:
        return _env_flag("OFFLINE_FACE_AUTH_REQUIRED", "0")
    return _env_flag("FACE_AUTH_REQUIRED", "0")


def _normalize_free_text(value: str) -> str:
    return " ".join(str(value).strip().split())


def _strip_trailing_memory_fillers(value: str) -> str:
    cleaned = value.strip().rstrip(".!? ")
    trailing_fillers = (
        "e memorize isso",
        "e memoriza isso",
        "e guarde isso",
        "e lembre disso",
        "memorize isso",
        "memoriza isso",
        "guarde isso",
        "lembre disso",
    )
    lowered = cleaned.lower()
    for filler in trailing_fillers:
        if lowered.endswith(filler):
            cleaned = cleaned[: -len(filler)].strip(" ,.;:-")
            lowered = cleaned.lower()
    return cleaned


class OfflineSpeaker:
    def __init__(
        self,
        enabled: bool = True,
        rate: int = 190,
        voice_name: str | None = None,
        *,
        provider: str = "edge",
        edge_voice: str = "pt-BR-ThalitaMultilingualNeural",
    ) -> None:
        self.enabled = enabled
        self.rate = rate
        self.voice_name = voice_name.strip() if voice_name else ""
        self.provider = provider.strip().lower() or "edge"
        self.edge_voice = edge_voice.strip() if edge_voice else "pt-BR-ThalitaMultilingualNeural"
        self._engine = None
        self._thread_id: int | None = None
        self._mixer_initialized = False

    def configure(
        self,
        *,
        enabled: bool | None = None,
        provider: str | None = None,
        voice_name: str | None = None,
        edge_voice: str | None = None,
    ) -> None:
        if enabled is not None:
            self.enabled = enabled
        if provider is not None:
            self.provider = provider.strip().lower() or self.provider
        if voice_name is not None:
            self.voice_name = voice_name.strip()
            self._engine = None
            self._thread_id = None
        if edge_voice is not None:
            self.edge_voice = edge_voice.strip()

    def _ensure_engine(self):
        if not self.enabled:
            return None

        current_thread_id = threading.get_ident()
        if self._engine is not None and self._thread_id == current_thread_id:
            return self._engine

        engine = pyttsx3.init()
        engine.setProperty("rate", self.rate)

        if self.voice_name:
            for voice in engine.getProperty("voices"):
                voice_name = getattr(voice, "name", "")
                voice_id = getattr(voice, "id", "")
                haystack = f"{voice_name} {voice_id}".lower()
                if self.voice_name.lower() in haystack:
                    engine.setProperty("voice", voice.id)
                    break

        self._engine = engine
        self._thread_id = current_thread_id
        return engine

    def _ensure_mixer(self) -> None:
        if self._mixer_initialized and pygame.mixer.get_init():
            return
        pygame.mixer.init()
        self._mixer_initialized = True

    async def _synthesize_edge(self, text: str, target_path: Path) -> None:
        communicate = edge_tts.Communicate(
            text,
            voice=self.edge_voice,
            rate="+0%",
            volume="+0%",
            pitch="+0Hz",
        )
        await communicate.save(str(target_path))

    def _say_edge(self, text: str) -> None:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            temp_path = Path(handle.name)

        try:
            asyncio.run(self._synthesize_edge(text, temp_path))
            self._ensure_mixer()
            pygame.mixer.music.load(str(temp_path))
            pygame.mixer.music.play()
            clock = pygame.time.Clock()
            while pygame.mixer.music.get_busy():
                clock.tick(20)
        finally:
            try:
                if pygame.mixer.get_init():
                    pygame.mixer.music.stop()
                    if hasattr(pygame.mixer.music, "unload"):
                        pygame.mixer.music.unload()
            except Exception:
                pass
            temp_path.unlink(missing_ok=True)

    def _say_local(self, text: str) -> None:
        engine = self._ensure_engine()
        if engine is None:
            return
        engine.say(text)
        engine.runAndWait()

    def say(self, text: str) -> None:
        if not self.enabled or not text.strip():
            return

        if self.provider == "edge":
            try:
                self._say_edge(text)
                return
            except Exception as exc:
                LOGGER.warning("[Offline] Edge TTS falhou, usando fallback local: %s", exc)

        self._say_local(text)

    @staticmethod
    def list_local_voices() -> list[dict[str, str]]:
        engine = pyttsx3.init()
        voices = []
        for voice in engine.getProperty("voices"):
            voices.append(
                {
                    "id": getattr(voice, "id", ""),
                    "name": getattr(voice, "name", ""),
                }
            )
        return voices

    @staticmethod
    def list_edge_voices() -> list[dict[str, str]]:
        return [dict(item) for item in EDGE_VOICE_PRESETS]

    def describe_voice(self) -> str:
        if self.provider == "edge":
            for voice in EDGE_VOICE_PRESETS:
                if voice["id"] == self.edge_voice:
                    return f"{voice['label']} via Edge TTS"
            return f"{self.edge_voice} via Edge TTS"

        for voice in self.list_local_voices():
            if self.voice_name and self.voice_name.lower() in f"{voice['name']} {voice['id']}".lower():
                return f"{voice['name']} via Windows TTS"
        return "Windows TTS local"


class LocalTranscriber:
    def __init__(self, model_name: str, device_preference: str = "auto", compute_type: str = "int8") -> None:
        self.model_name = model_name
        self.device_preference = device_preference
        self.compute_type = compute_type
        self._model = None

    def _build_model(self) -> WhisperModel:
        preferred_devices = []
        if self.device_preference == "auto":
            preferred_devices = ["cuda", "cpu"]
        else:
            preferred_devices = [self.device_preference]

        last_error: Exception | None = None
        for device_name in preferred_devices:
            try:
                LOGGER.info("[Offline] Loading Whisper model '%s' on %s.", self.model_name, device_name)
                return WhisperModel(self.model_name, device=device_name, compute_type=self.compute_type)
            except Exception as exc:
                last_error = exc
                LOGGER.warning("[Offline] Failed to load Whisper on %s: %s", device_name, exc)

        if last_error:
            raise last_error
        raise RuntimeError("Nao foi possivel inicializar o transcritor local.")

    @property
    def model(self) -> WhisperModel:
        if self._model is None:
            self._model = self._build_model()
        return self._model

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if audio.size == 0:
            return ""

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            temp_path = Path(handle.name)

        try:
            sf.write(temp_path, audio, sample_rate)
            segments, _ = self.model.transcribe(
                str(temp_path),
                language="pt",
                vad_filter=True,
                beam_size=1,
            )
            text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
            return " ".join(text.split()).strip()
        finally:
            temp_path.unlink(missing_ok=True)


class MicrophoneRecorder:
    def __init__(
        self,
        sample_rate: int,
        input_device,
        silence_threshold: float,
        silence_hold_seconds: float,
        max_record_seconds: float,
    ) -> None:
        self.sample_rate = sample_rate
        self.input_device = input_device
        self.silence_threshold = silence_threshold
        self.silence_hold_seconds = silence_hold_seconds
        self.max_record_seconds = max_record_seconds

    @staticmethod
    def list_input_devices() -> list[dict[str, Any]]:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        inputs: list[dict[str, Any]] = []
        for index, device in enumerate(devices):
            if int(device.get("max_input_channels", 0)) <= 0:
                continue
            hostapi_name = hostapis[device["hostapi"]]["name"] if device.get("hostapi") is not None else "Unknown"
            inputs.append(
                {
                    "id": index,
                    "name": device["name"],
                    "label": f"{index} - {device['name']} ({hostapi_name})",
                }
            )
        return inputs

    @classmethod
    def resolve_input_device(cls, value):
        if value is None:
            return None
        if isinstance(value, int):
            return value
        cleaned = str(value).strip()
        if not cleaned:
            return None
        if cleaned.isdigit():
            return int(cleaned)

        lowered = cleaned.lower()
        for device in cls.list_input_devices():
            haystack = f"{device['label']} {device['name']}".lower()
            if lowered in haystack:
                return device["id"]
        return None

    def set_input_device(self, value) -> None:
        self.input_device = self.resolve_input_device(value)

    def _candidate_devices(self) -> list[int | None]:
        candidates: list[int | None] = []
        seen: set[int | None] = set()

        preferred = self.resolve_input_device(self.input_device)
        if preferred not in seen:
            candidates.append(preferred)
            seen.add(preferred)

        default_input = None
        default_device = sd.default.device
        if isinstance(default_device, (list, tuple)) and default_device:
            default_input = default_device[0]
        elif isinstance(default_device, int):
            default_input = default_device

        resolved_default = self.resolve_input_device(default_input)
        if resolved_default not in seen:
            candidates.append(resolved_default)
            seen.add(resolved_default)

        for device in self.list_input_devices():
            device_id = device["id"]
            if device_id not in seen:
                candidates.append(device_id)
                seen.add(device_id)

        return candidates

    def record_until_silence(self) -> np.ndarray:
        chunk_seconds = 0.10
        blocksize = int(self.sample_rate * chunk_seconds)
        last_error: Exception | None = None

        for device_id in self._candidate_devices():
            started = False
            silent_for = 0.0
            total_seconds = 0.0
            speech_chunks: list[np.ndarray] = []
            all_chunks: list[np.ndarray] = []
            pre_roll = deque(maxlen=4)
            max_level = 0.0

            try:
                LOGGER.info("[Offline] Recording from microphone device %s...", device_id)
                with sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    blocksize=blocksize,
                    device=device_id,
                ) as stream:
                    while total_seconds < self.max_record_seconds:
                        data, overflowed = stream.read(blocksize)
                        if overflowed:
                            LOGGER.warning("[Offline] Audio input overflow detected.")

                        chunk = np.squeeze(data.copy(), axis=1)
                        level = float(np.abs(chunk).mean())
                        max_level = max(max_level, level)
                        total_seconds += chunk_seconds
                        all_chunks.append(chunk)

                        if level >= self.silence_threshold:
                            if not started and pre_roll:
                                speech_chunks.extend(list(pre_roll))
                            started = True
                            silent_for = 0.0
                            speech_chunks.append(chunk)
                            continue

                        if started:
                            speech_chunks.append(chunk)
                            silent_for += chunk_seconds
                            if silent_for >= self.silence_hold_seconds:
                                break
                        else:
                            pre_roll.append(chunk)
            except Exception as exc:
                last_error = exc
                LOGGER.warning("[Offline] Failed opening microphone %s: %s", device_id, exc)
                continue

            if started and speech_chunks:
                audio = np.concatenate(speech_chunks)
                peak = float(np.max(np.abs(audio))) if audio.size else 0.0
                if peak > 0:
                    audio = audio / peak
                self.input_device = device_id
                return audio.astype(np.float32)

            quiet_fallback_threshold = max(self.silence_threshold * 0.45, 0.0015)
            if all_chunks and max_level >= quiet_fallback_threshold:
                LOGGER.info(
                    "[Offline] Using low-volume fallback on microphone %s (peak=%.5f).",
                    device_id,
                    max_level,
                )
                audio = np.concatenate(all_chunks)
                peak = float(np.max(np.abs(audio))) if audio.size else 0.0
                if peak > 0:
                    audio = audio / peak
                self.input_device = device_id
                return audio.astype(np.float32)

        if last_error is not None:
            raise RuntimeError(f"Nao foi possivel abrir nenhum microfone util: {last_error}") from last_error
        return np.array([], dtype=np.float32)


class OfflineToolbox:
    def __init__(self, user_id: str, face_auth: FaceAuthManager | None = None) -> None:
        self.user_id = user_id
        self.face_auth = face_auth
        self.control = CortanaControl()
        self._game_mode = False

    def schemas(self) -> list[dict[str, Any]]:
        return [
            self._schema("aprender_fato", "Memoriza um fato importante sobre o usuario.", {"fato": "Fato a memorizar."}),
            self._schema(
                "pesquisar_no_passado",
                "Pesquisa fatos e conversas antigas na memoria compartilhada.",
                {"termo": "Termo para buscar nas memorias."},
            ),
            self._schema(
                "abrir_aplicativo",
                "Abre um aplicativo local ou atalho conhecido no PC.",
                {"nome_app": "Nome do aplicativo ou atalho."},
            ),
            self._schema(
                "fechar_programa",
                "Fecha um processo local pelo nome.",
                {"programa": "Nome do programa ou processo."},
            ),
            self._schema("criar_pasta", "Cria uma pasta local.", {"caminho": "Caminho da pasta."}),
            self._schema("deletar_item", "Deleta arquivo ou pasta local.", {"caminho": "Caminho a excluir."}),
            self._schema("mover_item", "Move arquivo ou pasta.", {"origem": "Origem.", "destino": "Destino."}, ["origem", "destino"]),
            self._schema("copiar_item", "Copia arquivo ou pasta.", {"origem": "Origem.", "destino": "Destino."}, ["origem", "destino"]),
            self._schema(
                "renomear_item",
                "Renomeia arquivo ou pasta.",
                {"caminho": "Caminho atual.", "novo_nome": "Novo nome."},
                ["caminho", "novo_nome"],
            ),
            self._schema("organizar_pasta", "Organiza uma pasta por tipo de arquivo.", {"caminho": "Pasta a organizar."}),
            self._schema("compactar_pasta", "Compacta uma pasta local.", {"caminho": "Pasta para compactar."}),
            self._schema("abrir_pasta", "Abre uma pasta local.", {"nome_pasta": "Nome ou caminho da pasta."}),
            self._schema(
                "buscar_e_abrir_arquivo",
                "Busca e abre um arquivo pelos locais permitidos.",
                {"nome_arquivo": "Nome completo ou parcial do arquivo."},
            ),
            self._schema("controle_volume", "Ajusta o volume do sistema.", {"nivel": "Nivel de 0 a 100."}, ["nivel"]),
            self._schema("controle_brilho", "Ajusta o brilho da tela.", {"nivel": "Nivel de 0 a 100."}, ["nivel"]),
            self._schema("energia_pc", "Executa uma acao de energia no PC.", {"acao": "Acao como desligar, suspender ou reiniciar."}),
            self._schema("wake_on_lan", "Liga um dispositivo na rede por Wake on LAN.", {"mac_address": "Endereco MAC do alvo."}),
            self._schema("status_face_id", "Consulta o status atual do Face ID local.", {}),
            self._schema("perfil_face_id", "Resume o perfil facial aprendido localmente.", {}),
            self._schema("modo_game", "Liga ou desliga o modo de economia local.", {"ativar": "true para ativar, false para desativar."}, ["ativar"]),
        ]

    def _schema(
        self,
        name: str,
        description: str,
        properties: dict[str, str],
        required: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        key: {"type": "string", "description": description_text}
                        for key, description_text in properties.items()
                    },
                    "required": required or list(properties.keys()),
                },
            },
        }

        if name in {"controle_volume", "controle_brilho"}:
            payload["function"]["parameters"]["properties"]["nivel"]["type"] = "integer"
        if name == "modo_game":
            payload["function"]["parameters"]["properties"]["ativar"]["type"] = "boolean"
        return payload

    def execute(self, name: str, arguments: dict[str, Any]) -> str:
        method = getattr(self, name, None)
        if method is None:
            return f"Ferramenta desconhecida: {name}"
        try:
            return str(method(**self._sanitize_arguments(name, arguments)))
        except TypeError as exc:
            return f"Parametros invalidos para {name}: {exc}"
        except Exception as exc:
            return f"Erro ao executar {name}: {exc}"

    def _sanitize_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            return {}

        cleaned: dict[str, Any] = {}
        for key, value in arguments.items():
            if isinstance(value, dict):
                nested_description = value.get("description")
                if isinstance(nested_description, str) and nested_description.strip():
                    cleaned[key] = _normalize_free_text(nested_description)
                    continue
            if isinstance(value, str):
                cleaned[key] = _normalize_free_text(value)
                continue
            cleaned[key] = value

        if name == "aprender_fato" and isinstance(cleaned.get("fato"), str):
            cleaned["fato"] = _strip_trailing_memory_fillers(cleaned["fato"])
        if name == "pesquisar_no_passado" and isinstance(cleaned.get("termo"), str):
            cleaned["termo"] = cleaned["termo"].strip(" .!?")
        return cleaned

    def aprender_fato(self, fato: str) -> str:
        if not fato.strip():
            return "Nada para memorizar."
        inserted = shared_memory.add_fact(self.user_id, fato, source="offline")
        return (
            f"Fato memorizado localmente: '{fato}'"
            if inserted
            else f"Fato ja existia na memoria compartilhada: '{fato}'"
        )

    def pesquisar_no_passado(self, termo: str) -> str:
        fact_matches = shared_memory.search_facts(self.user_id, termo, limit=4)
        episode_matches = shared_memory.search_episodes(self.user_id, termo, limit=4)

        sections: list[str] = []
        if fact_matches:
            sections.append("Fatos relacionados:\n" + "\n".join(f"- {match['content']}" for match in fact_matches))
        if episode_matches:
            sections.append("Historico episodico:\n" + "\n\n".join(episode_matches))

        if not sections:
            return f"Nao encontrei nada sobre '{termo}' na memoria compartilhada."
        return "Encontrei isto no meu passado:\n\n" + "\n\n".join(sections)

    def abrir_aplicativo(self, nome_app: str) -> str:
        return self.control.abrir_aplicativo(nome_app)

    def fechar_programa(self, programa: str) -> str:
        cleaned = programa.strip()
        if not cleaned:
            return "Nome de processo invalido."
        executable = cleaned if cleaned.lower().endswith(".exe") else f"{cleaned}.exe"
        result = subprocess.run(["taskkill", "/f", "/im", executable], capture_output=True, text=True)
        if result.returncode == 0:
            return f"Programa '{cleaned}' fechado com sucesso."
        return f"Nao foi possivel fechar '{cleaned}'."

    def criar_pasta(self, caminho: str) -> str:
        return self.control.cria_pasta(caminho)

    def deletar_item(self, caminho: str) -> str:
        return self.control.deletar_arquivo(caminho)

    def mover_item(self, origem: str, destino: str) -> str:
        return self.control.mover_item(origem, destino)

    def copiar_item(self, origem: str, destino: str) -> str:
        return self.control.copiar_item(origem, destino)

    def renomear_item(self, caminho: str, novo_nome: str) -> str:
        return self.control.renomear_item(caminho, novo_nome)

    def organizar_pasta(self, caminho: str) -> str:
        return self.control.organizar_pasta(caminho)

    def compactar_pasta(self, caminho: str) -> str:
        return self.control.compactar_pasta(caminho)

    def abrir_pasta(self, nome_pasta: str) -> str:
        return self.control.abrir_pasta(nome_pasta)

    def buscar_e_abrir_arquivo(self, nome_arquivo: str) -> str:
        return self.control.buscar_e_abrir_arquivo(nome_arquivo)

    def controle_volume(self, nivel: int) -> str:
        return self.control.controle_volume(int(nivel))

    def controle_brilho(self, nivel: int) -> str:
        return self.control.controle_brilho(int(nivel))

    def energia_pc(self, acao: str) -> str:
        return self.control.energia_pc(acao)

    def wake_on_lan(self, mac_address: str) -> str:
        return self.control.wake_on_lan(mac_address)

    def status_face_id(self) -> str:
        if not self.face_auth or not self.face_auth.enabled:
            return "Face ID desativado."
        snapshot = self.face_auth.snapshot()
        if not snapshot.enrolled:
            return "Face ID ainda nao cadastrado."
        if snapshot.authenticated:
            return f"Face ID autenticado para {snapshot.profile_name}."
        return f"Face ID configurado para {snapshot.profile_name}, aguardando desbloqueio de sessao."

    def perfil_face_id(self) -> str:
        if not self.face_auth or not self.face_auth.enabled:
            return "Face ID desativado."
        if not self.face_auth.is_enrolled():
            return "Face ID ainda nao cadastrado."
        profile = self.face_auth.profile_summary()
        return (
            f"Perfil facial de {profile.get('profile_name')}. "
            f"Criado em {profile.get('created_at')}. "
            f"Ultimo reconhecimento em {profile.get('last_seen_at') or 'ainda nao registrado'}. "
            f"Desbloqueios bem-sucedidos: {profile.get('successful_unlocks', 0)}. "
            f"Amostras base: {profile.get('base_sample_count', 0)}. "
            f"Amostras aprendidas: {profile.get('adaptive_sample_count', 0)}."
        )

    def modo_game(self, ativar: bool) -> str:
        self._game_mode = bool(ativar)
        return "Modo Game ativado localmente." if self._game_mode else "Modo Game desativado localmente."


class OfflineCortanaApp:
    def __init__(self, *, text_only: bool = False, tts_enabled: bool = True) -> None:
        self.user_id = os.getenv("OFFLINE_USER_ID", os.getenv("CORTANA_USER_ID", "Guilherme"))
        self.model_name = os.getenv("OFFLINE_MODEL", DEFAULT_MODEL)
        self.face_auth = self._build_face_auth_manager()
        self.toolbox = OfflineToolbox(self.user_id, self.face_auth)
        self.client = None
        self.history: list[dict[str, str]] = []
        self.last_saved_payload: str | None = None
        self.text_only = text_only or _env_flag("OFFLINE_TEXT_ONLY", "0")
        self.speaker = OfflineSpeaker(
            enabled=tts_enabled and not _env_flag("OFFLINE_DISABLE_TTS", "0"),
            rate=int(os.getenv("OFFLINE_TTS_RATE", "190")),
            voice_name=os.getenv(
                "OFFLINE_TTS_VOICE_NAME",
                r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_PT-BR_MARIA_11.0",
            ),
            provider=os.getenv("OFFLINE_TTS_PROVIDER", "edge"),
            edge_voice=os.getenv("OFFLINE_EDGE_VOICE", "pt-BR-ThalitaMultilingualNeural"),
        )
        self.recorder = MicrophoneRecorder(
            sample_rate=int(os.getenv("OFFLINE_SAMPLE_RATE", "16000")),
            input_device=MicrophoneRecorder.resolve_input_device(
                _parse_device(os.getenv("OFFLINE_INPUT_DEVICE")) or _default_input_device()
            ),
            silence_threshold=float(os.getenv("OFFLINE_SILENCE_THRESHOLD", "0.006")),
            silence_hold_seconds=float(os.getenv("OFFLINE_SILENCE_HOLD_SECONDS", "1.2")),
            max_record_seconds=float(os.getenv("OFFLINE_MAX_RECORD_SECONDS", "10")),
        )
        self.transcriber = LocalTranscriber(
            model_name=os.getenv("OFFLINE_STT_MODEL", "small"),
            device_preference=os.getenv("OFFLINE_STT_DEVICE", "auto"),
            compute_type=os.getenv("OFFLINE_STT_COMPUTE_TYPE", "int8"),
        )

    def _build_face_auth_manager(self) -> FaceAuthManager:
        return FaceAuthManager(
            enabled=_offline_face_flag(),
            profile_name=os.getenv("FACE_AUTH_PROFILE_NAME", self.user_id),
            camera_index=int(os.getenv("FACE_AUTH_CAMERA_INDEX", "0")),
            confidence_threshold=float(os.getenv("FACE_AUTH_CONFIDENCE_THRESHOLD", "60")),
            unlock_streak=int(os.getenv("FACE_AUTH_UNLOCK_STREAK", "6")),
            sample_count=int(os.getenv("FACE_AUTH_SAMPLE_COUNT", "25")),
            lock_grace_seconds=float(os.getenv("FACE_AUTH_LOCK_GRACE_SECONDS", "12")),
            frame_interval=float(os.getenv("FACE_AUTH_FRAME_INTERVAL", "0.20")),
            continuous_monitor=_env_flag("FACE_AUTH_CONTINUOUS_MONITOR", "0"),
            confidence_margin=float(os.getenv("FACE_AUTH_CONFIDENCE_MARGIN", "12")),
            adaptive_learning=_env_flag("FACE_AUTH_ADAPTIVE_LEARNING", "1"),
            adaptive_sample_limit=int(os.getenv("FACE_AUTH_ADAPTIVE_SAMPLE_LIMIT", "80")),
            adaptive_learning_cooldown_seconds=float(
                os.getenv("FACE_AUTH_ADAPTIVE_LEARNING_COOLDOWN_SECONDS", "1800")
            ),
        )

    def _ensure_face_unlock(self) -> None:
        if not self.face_auth.enabled:
            return
        if not self.face_auth.is_enrolled():
            raise RuntimeError("Face ID exigido, mas ainda nao cadastrado. Rode setup_face_auth.py primeiro.")
        LOGGER.info("[Offline] Waiting for Face ID unlock...")
        unlocked = self.face_auth.wait_for_unlock(timeout_seconds=None, show_window=True)
        if not unlocked:
            raise RuntimeError("Nao foi possivel validar o Face ID para iniciar a sessao offline.")

    def _ensure_ollama_service(self) -> None:
        try:
            response = requests.get(OLLAMA_API_URL, timeout=2)
            response.raise_for_status()
            return
        except Exception:
            pass

        ollama_path = shutil.which("ollama")
        if not ollama_path:
            raise RuntimeError("Ollama nao encontrado no sistema.")

        LOGGER.info("[Offline] Starting local Ollama service...")
        kwargs: dict[str, Any] = {
            "args": [ollama_path, "serve"],
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        subprocess.Popen(**kwargs)

        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                response = requests.get(OLLAMA_API_URL, timeout=2)
                response.raise_for_status()
                return
            except Exception:
                time.sleep(1)

        raise RuntimeError("Ollama nao respondeu a tempo em http://127.0.0.1:11434.")

    def _ensure_model_available(self) -> None:
        response = requests.get(OLLAMA_API_URL, timeout=5)
        response.raise_for_status()
        models = response.json().get("models", [])
        names = {model.get("name") for model in models if isinstance(model, dict)}
        if self.model_name not in names:
            raise RuntimeError(
                f"Modelo '{self.model_name}' nao esta instalado no Ollama. "
                f"Rode 'ollama pull {self.model_name}' para habilitar a Cortana offline."
            )

    def bootstrap(self) -> None:
        self._ensure_face_unlock()
        self._ensure_ollama_service()
        self._ensure_model_available()
        self.client = OpenAI(base_url=OLLAMA_OPENAI_URL, api_key="ollama")
        LOGGER.info("[Offline] Model '%s' ready.", self.model_name)

    def available_input_devices(self) -> list[dict[str, Any]]:
        return MicrophoneRecorder.list_input_devices()

    def current_input_device(self):
        resolved = MicrophoneRecorder.resolve_input_device(self.recorder.input_device)
        if resolved is not None:
            return resolved
        devices = self.available_input_devices()
        return devices[0]["id"] if devices else None

    def set_input_device(self, device_id) -> None:
        resolved = MicrophoneRecorder.resolve_input_device(device_id)
        self.recorder.set_input_device(resolved)
        save_offline_setting("OFFLINE_INPUT_DEVICE", "" if resolved is None else str(resolved))

    def available_voice_options(self) -> dict[str, list[dict[str, str]]]:
        return {
            "edge": self.speaker.list_edge_voices(),
            "local": self.speaker.list_local_voices(),
        }

    def set_tts_enabled(self, enabled: bool) -> None:
        self.speaker.configure(enabled=enabled)
        save_offline_setting("OFFLINE_DISABLE_TTS", "0" if enabled else "1")

    def configure_tts(self, *, provider: str, voice_id: str) -> None:
        cleaned_provider = provider.strip().lower()
        if cleaned_provider == "edge":
            self.speaker.configure(provider="edge", edge_voice=voice_id)
            save_offline_setting("OFFLINE_TTS_PROVIDER", "edge")
            save_offline_setting("OFFLINE_EDGE_VOICE", voice_id)
        else:
            self.speaker.configure(provider="local", voice_name=voice_id)
            save_offline_setting("OFFLINE_TTS_PROVIDER", "local")
            save_offline_setting("OFFLINE_TTS_VOICE_NAME", voice_id)

    def _memory_context(self) -> str:
        return shared_memory.build_context_block(self.user_id, fact_limit=15, episode_limit=3)

    def sync_online_memory(self) -> dict[str, int]:
        return asyncio.run(sync_mem0_to_shared(self.user_id))

    def _system_message(self) -> dict[str, str]:
        context = self._memory_context()
        content = OFFLINE_SYSTEM_PROMPT
        if context:
            content = content.strip() + "\n\nMemoria compartilhada atual:\n" + context
        return {"role": "system", "content": content}

    def _call_model(self, messages: list[dict[str, Any]], *, enable_tools: bool) -> Any:
        if self.client is None:
            raise RuntimeError("Cliente local do Ollama ainda nao foi inicializado.")
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": float(os.getenv("OFFLINE_TEMPERATURE", "0.4")),
        }
        if enable_tools:
            kwargs["tools"] = self.toolbox.schemas()
        return self.client.chat.completions.create(**kwargs)

    def _should_offer_tools(self, user_text: str) -> bool:
        lowered = user_text.lower()
        return any(keyword in lowered for keyword in TOOL_TRIGGER_KEYWORDS)

    def _extract_fact_to_learn(self, user_text: str) -> str | None:
        lowered = user_text.lower().strip()
        for prefix in DIRECT_LEARN_PREFIXES:
            if lowered.startswith(prefix):
                fact = user_text[len(prefix) :].strip()
                fact = _strip_trailing_memory_fillers(fact)
                return fact or None
        return None

    def _extract_memory_search(self, user_text: str) -> str | None:
        lowered = user_text.lower().strip()
        for prefix in DIRECT_SEARCH_PREFIXES:
            if lowered.startswith(prefix):
                query = user_text[len(prefix) :].strip(" .!?")
                return query or None
        return None

    def _build_identity_summary(self) -> str:
        facts = shared_memory.list_recent_facts(self.user_id, limit=12)
        if not facts:
            return "Ainda sei pouco sobre voce. Se quiser, me diga algo importante e eu memorizo na hora."

        lines = [f"- Perfil local: {self.user_id}"]
        lines.extend(f"- {fact['content']}" for fact in facts)
        return "Isto e o que eu sei sobre voce agora:\n" + "\n".join(lines)

    def _find_fact_by_keywords(self, *keywords: str) -> str | None:
        facts = shared_memory.list_recent_facts(self.user_id, limit=50)
        lowered_keywords = [keyword.lower() for keyword in keywords]
        for fact in facts:
            content = str(fact.get("content", ""))
            lowered = content.lower()
            if any(keyword in lowered for keyword in lowered_keywords):
                return content
        return None

    def _answer_name_question(self) -> str:
        name_fact = self._find_fact_by_keywords("nome:", "name:", "apelido:")
        if name_fact:
            return f"No meu perfil local, voce esta identificado como {self.user_id}. Tambem tenho salvo: {name_fact}."
        return f"No meu perfil local, voce esta identificado como {self.user_id}."

    def _answer_best_friend_question(self) -> str:
        best_friend_fact = self._find_fact_by_keywords("best friend", "melhor amigo")
        if best_friend_fact:
            if " for " in best_friend_fact and ", who is their best friend" in best_friend_fact:
                name = best_friend_fact.split(" for ", 1)[1].split(", who is their best friend", 1)[0].strip()
                return f"Pelo que eu tenho salvo, seu melhor amigo e {name}."
            return f"Pelo que eu tenho salvo, {best_friend_fact}."
        return "Ainda nao tenho nada confiavel salvo sobre o seu melhor amigo."

    def _handle_direct_intents(self, user_text: str) -> str | None:
        fact = self._extract_fact_to_learn(user_text)
        if fact:
            result = self.toolbox.aprender_fato(fact)
            return f"{result}. Vou usar isso nas proximas conversas."

        lowered = user_text.lower().strip(" .!?")
        if lowered in DIRECT_MEMORY_QUERIES:
            return self._build_identity_summary()
        if lowered in DIRECT_NAME_QUERIES:
            return self._answer_name_question()
        if lowered in DIRECT_BEST_FRIEND_QUERIES:
            return self._answer_best_friend_question()

        query = self._extract_memory_search(user_text)
        if query:
            return self.toolbox.pesquisar_no_passado(query)

        return None

    def _run_llm_turn(self, user_text: str) -> str:
        direct_reply = self._handle_direct_intents(user_text)
        if direct_reply is not None:
            return direct_reply

        window = self.history[-12:]
        messages: list[dict[str, Any]] = [self._system_message(), *window, {"role": "user", "content": user_text}]
        enable_tools = self._should_offer_tools(user_text)

        for _ in range(6):
            response = self._call_model(messages, enable_tools=enable_tools)
            message = response.choices[0].message

            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": message.content or "",
            }
            if message.tool_calls:
                assistant_message["tool_calls"] = [tool_call.model_dump() for tool_call in message.tool_calls]
            messages.append(assistant_message)

            if not message.tool_calls:
                return (message.content or "").strip()

            for tool_call in message.tool_calls:
                try:
                    arguments = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = self.toolbox.execute(tool_call.function.name, arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )
            enable_tools = True

        return "Entrei em um ciclo de ferramentas maior que o esperado. Tente reformular o pedido."

    def _record_and_transcribe(self) -> str:
        audio = self.recorder.record_until_silence()
        if audio.size == 0:
            return ""
        text = self.transcriber.transcribe(audio, self.recorder.sample_rate)
        LOGGER.info("[Offline] Transcription: %s", text)
        return text

    def _save_session_memory(self) -> None:
        session_messages = [
            message
            for message in self.history
            if message.get("role") in {"user", "assistant"} and str(message.get("content", "")).strip()
        ]
        if not session_messages:
            return

        payload = json.dumps(session_messages, ensure_ascii=False)
        if payload == self.last_saved_payload:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        shared_memory.save_episode(
            self.user_id,
            session_messages,
            source="offline",
            timestamp_label=timestamp,
            write_json_snapshot=True,
        )
        self.last_saved_payload = payload

    def run_turn(self, user_text: str) -> str:
        response_text = self._run_llm_turn(user_text)
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": response_text})
        return response_text

    def speak_text(self, text: str) -> None:
        self.speaker.say(text)

    def handle_text_input(self, user_text: str, *, speak: bool = True) -> str:
        reply = self.run_turn(user_text)
        self._save_session_memory()
        if speak:
            self.speak_text(reply)
        return reply

    def capture_voice_input(self) -> str:
        return self._record_and_transcribe()

    def handle_voice_input(self, *, speak: bool = True) -> tuple[str, str]:
        transcript = self.capture_voice_input()
        if not transcript:
            return "", ""
        reply = self.run_turn(transcript)
        self._save_session_memory()
        if speak:
            self.speak_text(reply)
        return transcript, reply

    def run_once(self, user_text: str) -> str:
        reply = self.run_turn(user_text)
        self._save_session_memory()
        return reply

    def interactive_loop(self) -> None:
        print("Cortana Offline pronta. Digite texto ou pressione Enter para falar. Use 'sair' para encerrar.")
        self.speaker.say("Cortana offline pronta, chefe.")

        try:
            while True:
                raw = input("\nVoce> ").strip()
                if raw.lower() in EXIT_COMMANDS:
                    break

                if raw:
                    user_text = raw
                else:
                    if self.text_only:
                        continue
                    print("Gravando... fale agora.")
                    user_text = self.capture_voice_input()
                    if not user_text:
                        print("Nenhuma fala detectada.")
                        continue
                    print(f"Transcricao> {user_text}")

                reply = self.handle_text_input(user_text, speak=False)
                print(f"Cortana> {reply}")
                self.speaker.say(reply)
        finally:
            self._save_session_memory()

    def shutdown(self) -> None:
        self._save_session_memory()
        try:
            if pygame.mixer.get_init():
                pygame.mixer.quit()
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Runtime offline da Cortana, separado da versao online.")
    parser.add_argument("--once", help="Executa um unico turno em modo texto e encerra.")
    parser.add_argument("--text-only", action="store_true", help="Desabilita captura de voz e usa apenas texto.")
    parser.add_argument("--no-tts", action="store_true", help="Desabilita fala sintetizada local.")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    _load_environment()
    args = parse_args()

    app = OfflineCortanaApp(text_only=args.text_only, tts_enabled=not args.no_tts)
    app.bootstrap()

    if args.once:
        reply = app.run_once(args.once)
        print(reply)
        return

    app.interactive_loop()


if __name__ == "__main__":
    main()
