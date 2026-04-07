import logging
import os
import time
import urllib.parse

import av
import cv2
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [SENTRY] - %(message)s")
logger = logging.getLogger(__name__)

IP = os.getenv("YOOUSEE_IP")
USER = urllib.parse.quote(os.getenv("YOOUSEE_USER", "admin"))
PASSWORD = urllib.parse.quote(os.getenv("YOOUSEE_PASSWORD", ""))
ALERT_CONTACT = os.getenv("CORTANA_ALERT_CONTACT", "Guilherme")
RTSP_PATH = os.getenv("YOOUSEE_RTSP_PATH", "/onvif1")
RTSP_PORT = os.getenv("YOOUSEE_RTSP_PORT", "554")
BRIDGE_URL = os.getenv("CORTANA_BRIDGE_SEND_URL", "http://localhost:5050/send")
RESIDENTS_DIR = "residents"

STATIONARY_THRESHOLD = 10
DETECTION_INTERVAL = 0.5
ALERT_COOLDOWN = 60

if not IP or not PASSWORD:
    raise SystemExit(
        "Defina YOOUSEE_IP e YOOUSEE_PASSWORD no ambiente antes de iniciar o Vision Sentry."
    )

RTSP_URL = f"rtsp://{USER}:{PASSWORD}@{IP}:{RTSP_PORT}{RTSP_PATH}"


class VisionSentryAV:
    def __init__(self):
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self.face_cascade = cv2.CascadeClassifier(cascade_path)
        self.unknown_start_time = None
        self.last_alert_time = 0
        os.makedirs(RESIDENTS_DIR, exist_ok=True)

    def send_alert(self, message: str) -> None:
        try:
            payload = {
                "contact": ALERT_CONTACT,
                "message": f"CORTANA MONITORAMENTO: {message}",
            }
            requests.post(BRIDGE_URL, json=payload, timeout=5)
            logger.info("Alerta enviado: %s", message)
        except Exception as exc:
            logger.error("Erro ao conectar com o bridge: %s", exc)

    def run(self) -> None:
        logger.info("Iniciando monitoramento via PyAV: %s", RTSP_URL)
        last_check_time = 0.0

        while True:
            container = None
            try:
                container = av.open(RTSP_URL, options={"rtsp_transport": "tcp"})
                for frame in container.decode(video=0):
                    now = time.time()
                    if now - last_check_time < DETECTION_INTERVAL:
                        continue
                    last_check_time = now

                    image = frame.to_ndarray(format="bgr24")
                    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    small_image = cv2.resize(gray, (0, 0), fx=0.5, fy=0.5)
                    faces = self.face_cascade.detectMultiScale(
                        small_image,
                        scaleFactor=1.1,
                        minNeighbors=5,
                        minSize=(30, 30),
                    )

                    if len(faces) > 0:
                        if self.unknown_start_time is None:
                            self.unknown_start_time = now
                            logger.info("Pessoa detectada na garagem.")

                        duration = now - self.unknown_start_time
                        if duration > STATIONARY_THRESHOLD and (now - self.last_alert_time > ALERT_COOLDOWN):
                            self.send_alert(f"Individuo detectado ha {int(duration)} segundos!")
                            self.last_alert_time = now
                    else:
                        self.unknown_start_time = None
            except KeyboardInterrupt:
                logger.info("Sentinela encerrada pelo usuario.")
                break
            except Exception as exc:
                logger.error("Erro no stream: %s. Tentando reconectar em 5 segundos...", exc)
                time.sleep(5)
            finally:
                if container:
                    container.close()


if __name__ == "__main__":
    VisionSentryAV().run()
