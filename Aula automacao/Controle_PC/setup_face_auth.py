from __future__ import annotations

import os

from dotenv import load_dotenv

from face_auth import FaceAuthManager

load_dotenv(override=True)


def main() -> None:
    profile_name = os.getenv("FACE_AUTH_PROFILE_NAME") or os.getenv("CORTANA_USER_ID", "Guilherme")
    camera_index = int(os.getenv("FACE_AUTH_CAMERA_INDEX", "0"))
    confidence_threshold = float(os.getenv("FACE_AUTH_CONFIDENCE_THRESHOLD", "52"))
    unlock_streak = int(os.getenv("FACE_AUTH_UNLOCK_STREAK", "6"))
    sample_count = int(os.getenv("FACE_AUTH_SAMPLE_COUNT", "25"))

    manager = FaceAuthManager(
        enabled=True,
        profile_name=profile_name,
        camera_index=camera_index,
        confidence_threshold=confidence_threshold,
        unlock_streak=unlock_streak,
        sample_count=sample_count,
        continuous_monitor=os.getenv("FACE_AUTH_CONTINUOUS_MONITOR", "0") == "1",
        adaptive_learning=os.getenv("FACE_AUTH_ADAPTIVE_LEARNING", "1") == "1",
        adaptive_sample_limit=int(os.getenv("FACE_AUTH_ADAPTIVE_SAMPLE_LIMIT", "80")),
        adaptive_learning_cooldown_seconds=float(
            os.getenv("FACE_AUTH_ADAPTIVE_LEARNING_COOLDOWN_SECONDS", "1800")
        ),
    )

    print("Abrindo webcam para cadastro do Face ID da Cortana...")
    print("Fique de frente para a camera ate completar a captura.")
    manager.enroll(show_window=True)
    print("Cadastro facial concluido com sucesso.")


if __name__ == "__main__":
    main()
