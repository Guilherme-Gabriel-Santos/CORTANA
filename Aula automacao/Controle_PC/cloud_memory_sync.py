from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from shared_memory import shared_memory

MEM0_API_URL = "https://api.mem0.ai/v2/memories/"


def _extract_memories(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        results = payload.get("results", [])
    elif isinstance(payload, list):
        results = payload
    else:
        results = []

    memories: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        text = item.get("memory") or item.get("text") or item.get("content")
        if not text:
            continue
        cleaned = " ".join(str(text).split()).strip()
        if cleaned:
            memories.append(cleaned)
    return memories


def _build_mem0_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Token {api_key}",
        "Mem0-User-ID": hashlib.md5(api_key.encode()).hexdigest(),
        "Content-Type": "application/json",
    }


def _ensure_mem0_env_loaded() -> None:
    load_dotenv(Path.cwd() / ".env", override=False)
    load_dotenv(Path.cwd() / ".env.offline", override=False)


def _fetch_mem0_payload_sync(user_id: str) -> Any:
    _ensure_mem0_env_loaded()
    api_key = os.getenv("MEM0_API_KEY")
    if not api_key:
        raise RuntimeError("MEM0_API_KEY nao encontrada no ambiente.")

    payload: dict[str, Any] = {"filters": {"user_id": user_id}}
    org_id = os.getenv("MEM0_ORG_ID")
    project_id = os.getenv("MEM0_PROJECT_ID")
    if org_id and project_id:
        payload["org_id"] = org_id
        payload["project_id"] = project_id

    response = requests.post(
        MEM0_API_URL,
        headers=_build_mem0_headers(api_key),
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


async def _fetch_mem0_payload(user_id: str, client: Any | None) -> Any:
    if client is not None and hasattr(client, "get_all"):
        return await client.get_all(filters={"user_id": user_id})
    return await asyncio.to_thread(_fetch_mem0_payload_sync, user_id)


async def sync_mem0_to_shared(
    user_id: str,
    *,
    client: Any | None = None,
    source: str = "online-cloud",
) -> dict[str, int]:
    payload = await _fetch_mem0_payload(user_id, client)
    memories = _extract_memories(payload)
    inserted = 0
    updated = 0

    for memory_text in reversed(memories):
        was_inserted = shared_memory.add_fact(user_id, memory_text, source=source)
        if was_inserted:
            inserted += 1
        else:
            updated += 1

    return {
        "fetched": len(memories),
        "inserted": inserted,
        "updated": updated,
    }
