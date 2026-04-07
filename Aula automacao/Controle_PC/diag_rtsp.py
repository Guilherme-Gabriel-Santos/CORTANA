import os

import cv2
from dotenv import load_dotenv

load_dotenv(override=True)

IP = os.getenv("YOOUSEE_IP")
USER = os.getenv("YOOUSEE_USER", "admin")
PASSWORD = os.getenv("YOOUSEE_PASSWORD")
PATHS = ["/onvif1", "/live/ch0", "/11", "/12"]
PORTS = [554, 5000, 8000, 8899]
TRANSPORTS = ["tcp", "udp"]

if not IP or not PASSWORD:
    raise SystemExit(
        "Defina YOOUSEE_IP e YOOUSEE_PASSWORD no ambiente antes de rodar o diagnostico RTSP."
    )

print(f"--- DIAGNOSTICO RTSP ({IP}) ---")

for transport in TRANSPORTS:
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{transport}"
    print(f"\nTestando transporte: {transport.upper()}")

    for port in PORTS:
        for path in PATHS:
            url = f"rtsp://{USER}:{PASSWORD}@{IP}:{port}{path}"
            print(f"  Tentando URL: {url} ...", end=" ", flush=True)

            capture = cv2.VideoCapture(url)
            if capture.isOpened():
                success, _frame = capture.read()
                if success:
                    print("SUCESSO!")
                    capture.release()
                    raise SystemExit(0)
                print("ABERTO, mas sem imagem.")
            else:
                print("FALHA.")
            capture.release()

print("\nNenhuma das combinacoes padrao funcionou.")
