from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

if os.name == "nt":
    import msvcrt
else:
    import fcntl

logger = logging.getLogger(__name__)

FACE_AUTH_DIR = Path("memory") / "face_auth"
MODEL_PATH = FACE_AUTH_DIR / "lbph_model.yml"
METADATA_PATH = FACE_AUTH_DIR / "metadata.json"
SAMPLES_DIR = FACE_AUTH_DIR / "samples"
ADAPTIVE_SAMPLES_DIR = FACE_AUTH_DIR / "adaptive_samples"
LOCK_PATH = FACE_AUTH_DIR / "camera.lock"
STATE_PATH = FACE_AUTH_DIR / "state.json"
CAMERA_LOCK_POLL_SECONDS = 0.25
CAMERA_LOCK_WAIT_SECONDS = 20.0
SHARED_AUTH_MIN_TTL_SECONDS = 5.0
DEFAULT_PRESENCE_GRACE_SECONDS = 45.0
DEFAULT_UNAUTHORIZED_GRACE_SECONDS = 18.0
DEFAULT_CONFIDENCE_MARGIN = 12.0
DEFAULT_ADAPTIVE_SAMPLE_LIMIT = 80
DEFAULT_ADAPTIVE_LEARNING_COOLDOWN_SECONDS = 1800.0


@dataclass
class FaceAuthState:
    enabled: bool
    enrolled: bool
    authenticated: bool
    profile_name: str | None = None
    reason: str | None = None
    confidence: float | None = None
    last_seen_at: float | None = None


class FaceAuthManager:
    def __init__(
        self,
        *,
        enabled: bool = False,
        profile_name: str = "Guilherme",
        camera_index: int = 0,
        confidence_threshold: float = 52.0,
        unlock_streak: int = 6,
        sample_count: int = 25,
        lock_grace_seconds: float = 8.0,
        presence_grace_seconds: float | None = None,
        unauthorized_grace_seconds: float | None = None,
        frame_interval: float = 0.20,
        continuous_monitor: bool = True,
        confidence_margin: float = DEFAULT_CONFIDENCE_MARGIN,
        adaptive_learning: bool = True,
        adaptive_sample_limit: int = DEFAULT_ADAPTIVE_SAMPLE_LIMIT,
        adaptive_learning_cooldown_seconds: float = DEFAULT_ADAPTIVE_LEARNING_COOLDOWN_SECONDS,
        camera_backend: int | None = None,
    ) -> None:
        self.enabled = enabled
        self.profile_name = profile_name
        self.camera_index = camera_index
        self.confidence_threshold = confidence_threshold
        self.unlock_streak = unlock_streak
        self.sample_count = sample_count
        self.lock_grace_seconds = lock_grace_seconds
        self.presence_grace_seconds = (
            max(lock_grace_seconds, DEFAULT_PRESENCE_GRACE_SECONDS)
            if presence_grace_seconds is None
            else max(lock_grace_seconds, presence_grace_seconds)
        )
        self.unauthorized_grace_seconds = (
            max(lock_grace_seconds, DEFAULT_UNAUTHORIZED_GRACE_SECONDS)
            if unauthorized_grace_seconds is None
            else max(lock_grace_seconds, unauthorized_grace_seconds)
        )
        self.frame_interval = frame_interval
        self.continuous_monitor = continuous_monitor
        self.confidence_margin = max(0.0, confidence_margin)
        self.adaptive_learning = adaptive_learning
        self.adaptive_sample_limit = max(0, adaptive_sample_limit)
        self.adaptive_learning_cooldown_seconds = max(0.0, adaptive_learning_cooldown_seconds)
        self.camera_backend = camera_backend

        self._cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        self._recognizer = cv2.face.LBPHFaceRecognizer_create()
        self._model_loaded = False
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._camera_lock_handle = None
        self._session_unlocked = False
        self._last_authorized_at: float | None = None
        self._last_confidence: float | None = None
        self._last_reason: str | None = None
        self._last_shared_state_write_at = 0.0

        self._ensure_dirs()
        if not hasattr(cv2, "face"):
            raise RuntimeError(
                "OpenCV foi instalado sem o modulo de reconhecimento facial. "
                "Instale opencv-contrib-python para usar o Face ID da Cortana."
            )
        if self.is_enrolled():
            self._load_model()
            self._ensure_profile_metadata()

    def _ensure_dirs(self) -> None:
        FACE_AUTH_DIR.mkdir(parents=True, exist_ok=True)
        SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
        ADAPTIVE_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)

    def _default_metadata(self) -> dict:
        return {
            "profile_name": self.profile_name,
            "camera_index": self.camera_index,
            "confidence_threshold": self.confidence_threshold,
            "unlock_streak": self.unlock_streak,
            "sample_count": len(list(SAMPLES_DIR.glob("sample_*.png"))),
            "adaptive_sample_count": len(list(ADAPTIVE_SAMPLES_DIR.glob("learned_*.png"))),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "created_ts": time.time(),
            "last_seen_at": None,
            "last_seen_ts": None,
            "last_learned_at": None,
            "last_learned_ts": None,
            "successful_unlocks": 0,
            "recognition_events": 0,
            "last_confidence": None,
            "average_confidence": None,
            "best_confidence": None,
            "continuous_monitor": self.continuous_monitor,
            "adaptive_learning": self.adaptive_learning,
        }

    def _ensure_profile_metadata(self) -> dict:
        metadata = self._default_metadata()
        metadata.update(self._read_metadata())
        metadata["profile_name"] = metadata.get("profile_name") or self.profile_name
        metadata["camera_index"] = self.camera_index
        metadata["confidence_threshold"] = self.confidence_threshold
        metadata["unlock_streak"] = self.unlock_streak
        metadata["sample_count"] = len(list(SAMPLES_DIR.glob("sample_*.png")))
        metadata["adaptive_sample_count"] = len(list(ADAPTIVE_SAMPLES_DIR.glob("learned_*.png")))
        metadata["continuous_monitor"] = self.continuous_monitor
        metadata["adaptive_learning"] = self.adaptive_learning
        self._save_metadata(metadata)
        return metadata

    def _load_training_samples(self) -> list[np.ndarray]:
        samples: list[np.ndarray] = []
        sample_paths = sorted(SAMPLES_DIR.glob("sample_*.png")) + sorted(ADAPTIVE_SAMPLES_DIR.glob("learned_*.png"))
        for sample_path in sample_paths:
            image = cv2.imread(str(sample_path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            samples.append(cv2.resize(image, (220, 220)))
        return samples

    def _retrain_model_from_disk(self) -> bool:
        samples = self._load_training_samples()
        if not samples:
            return False
        labels = np.ones(len(samples), dtype=np.int32)
        self._recognizer.train(samples, labels)
        self._recognizer.write(str(MODEL_PATH))
        self._model_loaded = True
        return True

    def _open_camera(self):
        backend_candidates: list[tuple[str, int | None]] = []
        if self.camera_backend is not None:
            backend_candidates.append(("CUSTOM", self.camera_backend))
        elif os.name == "nt":
            backend_candidates.extend(
                [
                    ("DSHOW", getattr(cv2, "CAP_DSHOW", None)),
                    ("MSMF", getattr(cv2, "CAP_MSMF", None)),
                    ("DEFAULT", None),
                ]
            )
        else:
            backend_candidates.append(("DEFAULT", None))

        for backend_name, backend in backend_candidates:
            if backend_name != "DEFAULT" and backend is None:
                continue

            capture = cv2.VideoCapture(self.camera_index, backend) if backend is not None else cv2.VideoCapture(
                self.camera_index
            )
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            if not capture.isOpened():
                capture.release()
                continue

            read_ok = False
            for _ in range(3):
                read_ok, _ = capture.read()
                if read_ok:
                    logger.info("[FaceAuth] Webcam aberta com backend %s.", backend_name)
                    return capture
                time.sleep(0.05)

            capture.release()

        return None

    def _read_state(self) -> dict:
        if not STATE_PATH.exists():
            return {}
        try:
            with STATE_PATH.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_state(self, state: dict) -> None:
        try:
            with STATE_PATH.open("w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
        except OSError as exc:
            logger.warning("[FaceAuth] Nao consegui atualizar o estado compartilhado: %s", exc)

    def _shared_auth_ttl_seconds(self) -> float:
        return max(self.lock_grace_seconds, SHARED_AUTH_MIN_TTL_SECONDS)

    def _grace_seconds_for_reason(self, reason: str | None) -> float:
        if reason == "rosto_nao_autorizado":
            return self.unauthorized_grace_seconds
        return self.presence_grace_seconds

    def _shared_snapshot(self) -> tuple[float | None, float | None, str | None, str | None]:
        state = self._read_state()
        last_authorized_at = state.get("last_authorized_at")
        if isinstance(last_authorized_at, (int, float)):
            return (
                float(last_authorized_at),
                float(state["confidence"]) if isinstance(state.get("confidence"), (int, float)) else None,
                state.get("reason"),
                state.get("profile_name"),
            )
        return None, None, state.get("reason"), state.get("profile_name")

    def _shared_auth_is_recent(self, last_authorized_at: float | None) -> bool:
        if last_authorized_at is None:
            return False
        return (time.time() - last_authorized_at) <= self._shared_auth_ttl_seconds()

    def _is_timestamp_authenticated(self, last_authorized_at: float | None, reason: str | None = None) -> bool:
        if not self.enabled:
            return True
        if last_authorized_at is None:
            return False
        if not self.continuous_monitor:
            return True
        grace_seconds = max(self._shared_auth_ttl_seconds(), self._grace_seconds_for_reason(reason))
        return (time.time() - last_authorized_at) <= grace_seconds

    def _acquire_camera_lock(self, timeout_seconds: float | None = CAMERA_LOCK_WAIT_SECONDS) -> bool:
        if self._camera_lock_handle is not None:
            return True

        deadline = time.time() + timeout_seconds if timeout_seconds is not None else None
        while True:
            handle = LOCK_PATH.open("a+b")
            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)

                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                if deadline is not None and time.time() >= deadline:
                    return False
                time.sleep(CAMERA_LOCK_POLL_SECONDS)
                continue

            self._camera_lock_handle = handle
            return True

    def _release_camera_lock(self) -> None:
        handle = self._camera_lock_handle
        if handle is None:
            return

        try:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()
            self._camera_lock_handle = None

    def _preprocess_face(self, frame: np.ndarray):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(
            gray,
            scaleFactor=1.2,
            minNeighbors=5,
            minSize=(90, 90),
        )
        if len(faces) == 0:
            return None, None

        x, y, w, h = max(faces, key=lambda face: face[2] * face[3])
        face = gray[y : y + h, x : x + w]
        face = cv2.equalizeHist(face)
        face = cv2.resize(face, (220, 220))
        return face, (x, y, w, h)

    def is_enrolled(self) -> bool:
        return MODEL_PATH.exists() and METADATA_PATH.exists()

    def _load_model(self) -> None:
        if self._model_loaded:
            return
        if not self.is_enrolled():
            raise RuntimeError("Perfil facial ainda nao cadastrado.")
        self._recognizer.read(str(MODEL_PATH))
        self._model_loaded = True

    def _save_metadata(self, metadata: dict) -> None:
        with METADATA_PATH.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False, indent=2)

    def _read_metadata(self) -> dict:
        if not METADATA_PATH.exists():
            return {}
        with METADATA_PATH.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def enroll(self, show_window: bool = True) -> bool:
        self._ensure_dirs()
        for sample_path in list(SAMPLES_DIR.glob("sample_*.png")) + list(ADAPTIVE_SAMPLES_DIR.glob("learned_*.png")):
            try:
                sample_path.unlink()
            except OSError:
                logger.warning("[FaceAuth] Nao consegui limpar amostra antiga: %s", sample_path)
        if not self._acquire_camera_lock(timeout_seconds=CAMERA_LOCK_WAIT_SECONDS):
            raise RuntimeError("A webcam esta em uso. Feche outros apps antes de cadastrar seu rosto.")

        capture = self._open_camera()
        if capture is None:
            self._release_camera_lock()
            raise RuntimeError("Nao consegui acessar a webcam para cadastrar seu rosto.")

        samples: list[np.ndarray] = []
        last_capture_at = 0.0
        window_name = "Cortana Face ID - Cadastro"

        try:
            while len(samples) < self.sample_count:
                ok, frame = capture.read()
                if not ok:
                    continue

                face, box = self._preprocess_face(frame)
                now = time.time()
                if face is not None and box is not None:
                    x, y, w, h = box
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (40, 220, 40), 2)
                    if now - last_capture_at >= 0.35:
                        samples.append(face)
                        last_capture_at = now
                        sample_path = SAMPLES_DIR / f"sample_{len(samples):02d}.png"
                        cv2.imwrite(str(sample_path), face)

                cv2.putText(
                    frame,
                    f"Capturando rosto: {len(samples)}/{self.sample_count}",
                    (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 255, 0),
                    2,
                )
                cv2.putText(
                    frame,
                    "Olhe para a camera. Pressione Q para cancelar.",
                    (20, 62),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (255, 255, 255),
                    2,
                )

                if show_window:
                    cv2.imshow(window_name, frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        raise RuntimeError("Cadastro facial cancelado pelo usuario.")
                else:
                    time.sleep(0.03)

            labels = np.ones(len(samples), dtype=np.int32)
            self._recognizer.train(samples, labels)
            self._recognizer.write(str(MODEL_PATH))
            self._save_metadata(
                {
                    **self._default_metadata(),
                    "profile_name": self.profile_name,
                    "camera_index": self.camera_index,
                    "confidence_threshold": self.confidence_threshold,
                    "unlock_streak": self.unlock_streak,
                    "sample_count": len(samples),
                }
            )
            self._model_loaded = True
            self._session_unlocked = False
            logger.info("[FaceAuth] Perfil facial cadastrado com %s amostras.", len(samples))
            return True
        finally:
            capture.release()
            self._release_camera_lock()
            cv2.destroyAllWindows()

    def _predict_face(self, frame: np.ndarray):
        if not self.is_enrolled():
            return False, None, "perfil_nao_cadastrado", None

        self._load_model()
        face, box = self._preprocess_face(frame)
        if face is None:
            return False, None, "nenhum_rosto_detectado", None

        label, confidence = self._recognizer.predict(face)
        if label == 1 and confidence <= self.confidence_threshold:
            return True, confidence, "rosto_autorizado", face
        if label == 1 and confidence <= (self.confidence_threshold + self.confidence_margin):
            return False, confidence, "rosto_inconclusivo", face
        return False, confidence, "rosto_nao_autorizado", face

    def _record_successful_unlock(self, confidence: float | None, face: np.ndarray | None = None) -> None:
        metadata = self._ensure_profile_metadata()
        now = time.time()

        successful_unlocks = int(metadata.get("successful_unlocks") or 0) + 1
        previous_events = int(metadata.get("recognition_events") or 0)
        previous_average = metadata.get("average_confidence")
        previous_best = metadata.get("best_confidence")

        metadata["successful_unlocks"] = successful_unlocks
        metadata["recognition_events"] = previous_events + 1
        metadata["last_seen_ts"] = now
        metadata["last_seen_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        metadata["last_confidence"] = confidence

        if confidence is not None:
            if isinstance(previous_average, (int, float)) and previous_events > 0:
                metadata["average_confidence"] = (
                    (float(previous_average) * previous_events) + confidence
                ) / metadata["recognition_events"]
            else:
                metadata["average_confidence"] = confidence

            if isinstance(previous_best, (int, float)):
                metadata["best_confidence"] = min(float(previous_best), confidence)
            else:
                metadata["best_confidence"] = confidence

        learned = self._learn_from_face(face, confidence, metadata)
        if learned:
            metadata["sample_count"] = len(list(SAMPLES_DIR.glob("sample_*.png")))
            metadata["adaptive_sample_count"] = len(list(ADAPTIVE_SAMPLES_DIR.glob("learned_*.png")))

        self._save_metadata(metadata)

    def _learn_from_face(self, face: np.ndarray | None, confidence: float | None, metadata: dict | None = None) -> bool:
        if not self.adaptive_learning or face is None or self.adaptive_sample_limit <= 0:
            return False

        metadata = metadata or self._ensure_profile_metadata()
        last_learned_ts = metadata.get("last_learned_ts")
        if isinstance(last_learned_ts, (int, float)):
            if (time.time() - float(last_learned_ts)) < self.adaptive_learning_cooldown_seconds:
                return False

        sample_name = time.strftime("learned_%Y%m%d_%H%M%S.png")
        sample_path = ADAPTIVE_SAMPLES_DIR / sample_name
        cv2.imwrite(str(sample_path), face)

        adaptive_samples = sorted(ADAPTIVE_SAMPLES_DIR.glob("learned_*.png"))
        while len(adaptive_samples) > self.adaptive_sample_limit:
            oldest = adaptive_samples.pop(0)
            try:
                oldest.unlink()
            except OSError:
                logger.warning("[FaceAuth] Nao consegui remover amostra adaptativa antiga: %s", oldest)
                break

        if self._retrain_model_from_disk():
            logger.info("[FaceAuth] Perfil facial atualizado com nova amostra adaptativa.")

        now = time.time()
        metadata["last_learned_ts"] = now
        metadata["last_learned_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        metadata["adaptive_sample_count"] = len(list(ADAPTIVE_SAMPLES_DIR.glob("learned_*.png")))
        metadata["sample_count"] = len(list(SAMPLES_DIR.glob("sample_*.png")))
        if confidence is not None:
            metadata["last_confidence"] = confidence
        return True

    def wait_for_unlock(self, timeout_seconds: float | None = None, show_window: bool = True) -> bool:
        if not self.enabled:
            return True
        if not self.is_enrolled():
            raise RuntimeError("Face ID ainda nao configurado. Rode setup_face_auth.py primeiro.")
        if self._session_unlocked:
            logger.info("[FaceAuth] Sessao ja foi autenticada neste processo.")
            return True

        shared_last_authorized_at, shared_confidence, shared_reason, _ = self._shared_snapshot()
        if self._shared_auth_is_recent(shared_last_authorized_at):
            with self._lock:
                self._session_unlocked = True
                self._last_authorized_at = shared_last_authorized_at
                self._last_confidence = shared_confidence
                self._last_reason = shared_reason or "rosto_autorizado"
            logger.info("[FaceAuth] Sessao reutilizando autenticacao facial recente.")
            return True

        deadline = time.time() + timeout_seconds if timeout_seconds else None
        lock_deadline = time.time() + min(timeout_seconds, CAMERA_LOCK_WAIT_SECONDS) if timeout_seconds else (
            time.time() + CAMERA_LOCK_WAIT_SECONDS
        )
        while True:
            shared_last_authorized_at, shared_confidence, shared_reason, _ = self._shared_snapshot()
            if self._shared_auth_is_recent(shared_last_authorized_at):
                with self._lock:
                    self._session_unlocked = True
                    self._last_authorized_at = shared_last_authorized_at
                    self._last_confidence = shared_confidence
                    self._last_reason = shared_reason or "rosto_autorizado"
                logger.info("[FaceAuth] Sessao reutilizando autenticacao facial recente.")
                return True

            remaining = None if deadline is None else max(0.0, deadline - time.time())
            wait_time = CAMERA_LOCK_POLL_SECONDS if remaining is None else min(CAMERA_LOCK_POLL_SECONDS, remaining)
            if self._acquire_camera_lock(timeout_seconds=wait_time):
                break
            if deadline is not None and time.time() >= deadline:
                return False
            if time.time() >= lock_deadline:
                raise RuntimeError(
                    "Nao consegui acessar a webcam para validar o Face ID. "
                    "Ela parece estar em uso por outra sessao da Cortana ou por outro aplicativo."
                )

        capture = self._open_camera()
        if capture is None:
            self._release_camera_lock()
            raise RuntimeError(
                "Nao consegui acessar a webcam para validar o Face ID. "
                "Ela pode estar sendo usada por outro aplicativo."
            )

        window_name = "Cortana Face ID - Desbloqueio"
        streak = 0

        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    continue

                authorized, confidence, reason, face = self._predict_face(frame)
                if authorized:
                    streak += 1
                    self._mark_authorized(confidence)
                else:
                    streak = 0
                    self._mark_unauthorized(reason, confidence)

                status_text = (
                    f"Reconhecido ({streak}/{self.unlock_streak})"
                    if authorized
                    else "Aguardando rosto autorizado"
                )
                color = (30, 220, 30) if authorized else (20, 20, 220)
                cv2.putText(
                    frame,
                    status_text,
                    (20, 32),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    color,
                    2,
                )
                cv2.putText(
                    frame,
                    "Fique em frente a webcam. Pressione Q para cancelar.",
                    (20, 62),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.58,
                    (255, 255, 255),
                    2,
                )
                if confidence is not None:
                    cv2.putText(
                        frame,
                        f"Confianca LBPH: {confidence:.2f}",
                        (20, 92),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.58,
                        (255, 255, 255),
                        2,
                    )

                if show_window:
                    cv2.imshow(window_name, frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        raise RuntimeError("Desbloqueio facial cancelado pelo usuario.")
                else:
                    time.sleep(0.03)

                if streak >= self.unlock_streak:
                    self._record_successful_unlock(confidence, face)
                    return True
                if deadline and time.time() >= deadline:
                    return False
        finally:
            capture.release()
            self._release_camera_lock()
            cv2.destroyAllWindows()

    def _mark_authorized(self, confidence: float | None) -> None:
        now = time.time()
        with self._lock:
            self._session_unlocked = True
            self._last_authorized_at = now
            self._last_confidence = confidence
            self._last_reason = "rosto_autorizado"

        if now - self._last_shared_state_write_at >= 0.75:
            self._write_state(
                {
                    "profile_name": self.profile_name,
                    "last_authorized_at": now,
                    "confidence": confidence,
                    "reason": "rosto_autorizado",
                }
            )
            self._last_shared_state_write_at = now

    def _mark_unauthorized(self, reason: str, confidence: float | None = None) -> None:
        with self._lock:
            self._last_reason = reason
            self._last_confidence = confidence

    def is_authenticated(self) -> bool:
        return self.snapshot().authenticated

    def snapshot(self) -> FaceAuthState:
        metadata = self._read_metadata()
        shared_last_authorized_at, shared_confidence, shared_reason, shared_profile_name = self._shared_snapshot()
        with self._lock:
            session_unlocked = self._session_unlocked
            last_authorized_at = self._last_authorized_at
            confidence = self._last_confidence
            reason = self._last_reason

        if shared_last_authorized_at is not None and (
            last_authorized_at is None or shared_last_authorized_at > last_authorized_at
        ):
            last_authorized_at = shared_last_authorized_at
            confidence = shared_confidence
            reason = shared_reason or reason

        if self.continuous_monitor:
            authenticated = self._is_timestamp_authenticated(last_authorized_at, reason)
        else:
            authenticated = session_unlocked or self._shared_auth_is_recent(shared_last_authorized_at)
        return FaceAuthState(
            enabled=self.enabled,
            enrolled=self.is_enrolled(),
            authenticated=authenticated,
            profile_name=metadata.get("profile_name") or shared_profile_name or self.profile_name,
            reason=reason,
            confidence=confidence,
            last_seen_at=last_authorized_at,
        )

    def profile_summary(self) -> dict:
        metadata = self._ensure_profile_metadata()
        snapshot = self.snapshot()
        return {
            "profile_name": metadata.get("profile_name") or self.profile_name,
            "created_at": metadata.get("created_at"),
            "last_seen_at": metadata.get("last_seen_at"),
            "successful_unlocks": int(metadata.get("successful_unlocks") or 0),
            "recognition_events": int(metadata.get("recognition_events") or 0),
            "base_sample_count": int(metadata.get("sample_count") or 0),
            "adaptive_sample_count": int(metadata.get("adaptive_sample_count") or 0),
            "average_confidence": metadata.get("average_confidence"),
            "best_confidence": metadata.get("best_confidence"),
            "last_confidence": metadata.get("last_confidence"),
            "adaptive_learning": bool(metadata.get("adaptive_learning", self.adaptive_learning)),
            "continuous_monitor": bool(metadata.get("continuous_monitor", self.continuous_monitor)),
            "authenticated": snapshot.authenticated,
        }

    def _monitor_loop(self) -> None:
        if not self._acquire_camera_lock(timeout_seconds=2.0):
            logger.info("[FaceAuth] Webcam ja esta em uso por outra sessao autenticada.")
            return

        capture = self._open_camera()
        if capture is None:
            self._mark_unauthorized("webcam_indisponivel")
            logger.warning("[FaceAuth] Webcam indisponivel para monitoramento continuo.")
            self._release_camera_lock()
            return

        try:
            while not self._stop_event.is_set():
                ok, frame = capture.read()
                if ok:
                    authorized, confidence, reason, _ = self._predict_face(frame)
                    if authorized:
                        self._mark_authorized(confidence)
                    else:
                        self._mark_unauthorized(reason, confidence)
                time.sleep(self.frame_interval)
        finally:
            capture.release()
            self._release_camera_lock()

    def start_monitor(self) -> None:
        if not self.enabled or not self.continuous_monitor or not self.is_enrolled():
            return
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._stop_event.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="cortana-face-auth-monitor",
            daemon=True,
        )
        self._monitor_thread.start()
        logger.info("[FaceAuth] Monitoramento continuo iniciado.")

    def stop_monitor(self) -> None:
        self._stop_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)
        self._monitor_thread = None
