from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from cloud_memory_sync import sync_mem0_to_shared


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    load_dotenv(override=True)
    user_id = os.getenv("CORTANA_USER_ID", "Guilherme")
    stats = asyncio.run(sync_mem0_to_shared(user_id))
    print(
        "Sincronizacao concluida: "
        f"{stats['fetched']} memorias lidas, "
        f"{stats['inserted']} novas, "
        f"{stats['updated']} ja existentes."
    )


if __name__ == "__main__":
    main()
