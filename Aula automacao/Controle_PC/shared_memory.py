from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

MEMORY_DIR = Path("memory")
SHARED_MEMORY_DIR = MEMORY_DIR / "shared"
EPISODIC_MEMORY_DIR = MEMORY_DIR / "episodic"
DB_PATH = SHARED_MEMORY_DIR / "cortana_memory.sqlite3"


class SharedMemoryStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        SHARED_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        EPISODIC_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    created_ts REAL NOT NULL,
                    last_used_ts REAL,
                    UNIQUE(user_id, fingerprint)
                );

                CREATE INDEX IF NOT EXISTS idx_facts_user_created
                ON facts(user_id, created_ts DESC);

                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    file_path TEXT,
                    created_at TEXT NOT NULL,
                    created_ts REAL NOT NULL,
                    messages_json TEXT NOT NULL,
                    content_text TEXT NOT NULL,
                    UNIQUE(user_id, source, content_hash)
                );

                CREATE INDEX IF NOT EXISTS idx_episodes_user_created
                ON episodes(user_id, created_ts DESC);
                """
            )

    def _now(self) -> tuple[str, float]:
        created_ts = time.time()
        created_at = datetime.fromtimestamp(created_ts).strftime("%Y-%m-%d %H:%M:%S")
        return created_at, created_ts

    def _normalize_fact_text(self, content: str) -> str:
        normalized = " ".join(str(content).split()).strip()
        if not normalized:
            return ""

        if normalized.startswith("{") and normalized.endswith("}"):
            try:
                parsed = json.loads(normalized)
            except json.JSONDecodeError:
                return normalized
            if isinstance(parsed, dict):
                pairs = [f"{key}: {value}" for key, value in parsed.items()]
                return "; ".join(pairs).strip()
        return normalized

    def add_fact(self, user_id: str, content: str, source: str = "unknown") -> bool:
        normalized = self._normalize_fact_text(content)
        if not normalized:
            return False

        fingerprint = hashlib.sha1(f"{user_id}:{normalized.lower()}".encode("utf-8")).hexdigest()
        created_at, created_ts = self._now()

        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO facts (
                    user_id, content, source, fingerprint, created_at, created_ts, last_used_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, normalized, source, fingerprint, created_at, created_ts, created_ts),
            )
            inserted = cursor.rowcount > 0
            if not inserted:
                connection.execute(
                    """
                    UPDATE facts
                    SET last_used_ts = ?, source = ?
                    WHERE user_id = ? AND fingerprint = ?
                    """,
                    (created_ts, source, user_id, fingerprint),
                )
            return inserted

    def list_recent_facts(self, user_id: str, limit: int = 8) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT content, source, created_at, created_ts
                FROM facts
                WHERE user_id = ?
                ORDER BY created_ts DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_facts(self, user_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        normalized = f"%{' '.join(query.split()).strip().lower()}%"
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT content, source, created_at, created_ts
                FROM facts
                WHERE user_id = ? AND lower(content) LIKE ?
                ORDER BY created_ts DESC
                LIMIT ?
                """,
                (user_id, normalized, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_episode(
        self,
        user_id: str,
        messages: list[dict[str, Any]],
        *,
        source: str,
        timestamp_label: str | None = None,
        write_json_snapshot: bool = True,
    ) -> Path | None:
        if not messages:
            return None

        timestamp_label = timestamp_label or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        created_dt = datetime.strptime(timestamp_label, "%Y-%m-%d_%H-%M-%S")
        created_at = created_dt.strftime("%Y-%m-%d %H:%M:%S")
        created_ts = created_dt.timestamp()
        messages_json = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        content_hash = hashlib.sha1(messages_json.encode("utf-8")).hexdigest()
        content_text = "\n".join(
            f"[{message.get('role', 'unknown')}]: {message.get('content', '')}" for message in messages
        )

        file_path = EPISODIC_MEMORY_DIR / f"session_{timestamp_label}.json"
        if write_json_snapshot and not file_path.exists():
            payload = {
                "timestamp": timestamp_label,
                "user_id": user_id,
                "source": source,
                "messages": messages,
            }
            with file_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)

        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO episodes (
                    user_id, source, content_hash, file_path, created_at, created_ts, messages_json, content_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    source,
                    content_hash,
                    str(file_path) if write_json_snapshot else None,
                    created_at,
                    created_ts,
                    messages_json,
                    content_text,
                ),
            )

        return file_path if write_json_snapshot else None

    def search_episodes(self, user_id: str, query: str, limit: int = 5) -> list[str]:
        normalized = query.strip().lower()
        if not normalized:
            return []

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT created_at, source, messages_json
                FROM episodes
                WHERE user_id = ? AND lower(content_text) LIKE ?
                ORDER BY created_ts DESC
                LIMIT ?
                """,
                (user_id, f"%{normalized}%", max(limit * 2, limit)),
            ).fetchall()

        matches: list[str] = []
        for row in rows:
            messages = json.loads(row["messages_json"])
            for index, message in enumerate(messages):
                content = str(message.get("content", ""))
                if normalized not in content.lower():
                    continue
                context = messages[max(0, index - 1) : min(len(messages), index + 2)]
                snippet = "\n".join(
                    f"[{item.get('role', 'unknown')}]: {item.get('content', '')}" for item in context
                )
                matches.append(f"Em {row['created_at']} ({row['source']}):\n{snippet}")
                break
            if len(matches) >= limit:
                break
        return matches

    def recent_episode_highlights(self, user_id: str, limit: int = 2) -> list[str]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT created_at, source, messages_json
                FROM episodes
                WHERE user_id = ?
                ORDER BY created_ts DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

        highlights: list[str] = []
        for row in rows:
            messages = json.loads(row["messages_json"])
            excerpt = []
            for message in messages[:4]:
                content = str(message.get("content", "")).strip()
                if content:
                    excerpt.append(f"[{message.get('role', 'unknown')}]: {content}")
            if excerpt:
                highlights.append(f"Em {row['created_at']} ({row['source']}):\n" + "\n".join(excerpt))
        return highlights

    def build_context_block(self, user_id: str, fact_limit: int = 6, episode_limit: int = 2) -> str:
        facts = self.list_recent_facts(user_id, limit=fact_limit)
        episodes = self.recent_episode_highlights(user_id, limit=episode_limit)

        sections: list[str] = []
        if facts:
            fact_lines = [f"- {fact['content']}" for fact in facts]
            sections.append("Fatos compartilhados:\n" + "\n".join(fact_lines))
        if episodes:
            sections.append("Contexto episodico recente:\n" + "\n\n".join(episodes))
        return "\n\n".join(sections).strip()


shared_memory = SharedMemoryStore()
