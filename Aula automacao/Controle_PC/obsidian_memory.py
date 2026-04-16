from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Diretório base do Cofre
VAULT_DIR = Path(__file__).resolve().parent.parent.parent / "Obsidian_Vault"

# Estrutura interna do Obsidian
FACTS_DIR = VAULT_DIR / "Fatos"
EPISODES_DIR = VAULT_DIR / "Episodios"
REPORTS_DIR = VAULT_DIR / "Relatorios_Seguranca"

class ObsidianVaultManager:
    def __init__(self) -> None:
        self._init_vault()

    def _init_vault(self) -> None:
        VAULT_DIR.mkdir(parents=True, exist_ok=True)
        FACTS_DIR.mkdir(parents=True, exist_ok=True)
        EPISODES_DIR.mkdir(parents=True, exist_ok=True)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    def _sanitize_filename(self, text: str) -> str:
        safe_chars = "".join(c if c.isalnum() else "_" for c in text[:30])
        return safe_chars.strip("_")

    def _generate_frontmatter(self, metadata: dict[str, Any]) -> str:
        lines = ["---"]
        for key, val in metadata.items():
            if isinstance(val, list):
                lines.append(f"{key}: [{', '.join(str(v) for v in val)}]")
            else:
                lines.append(f"{key}: {val}")
        lines.append("---\n")
        return "\n".join(lines)

    def _extract_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        metadata = {}
        body = content
        if content.startswith("---\n"):
            parts = content.split("---\n", 2)
            if len(parts) >= 3:
                fm_text = parts[1]
                body = parts[2]
                for line in fm_text.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        metadata[k.strip()] = v.strip()
        return metadata, body.strip()

    # -------------------------------------------------------------
    # Fatos
    # -------------------------------------------------------------

    def add_fact(self, user_id: str, content: str, source: str = "online", timestamp: str = "") -> bool:
        content = str(content).strip()
        if not content:
            return False

        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fingerprint = hashlib.sha1(f"{user_id}:{content.lower()}".encode("utf-8")).hexdigest()
        
        # O titulo do arquivo será um trecho do conteudo + hash
        snippet = self._sanitize_filename(content)
        file_name = f"Fato_{snippet}_{fingerprint[:6]}.md"
        file_path = FACTS_DIR / file_name

        if file_path.exists():
            # Atualizar a data de ultimo acesso (opcional no MD)
            return False

        meta = {
            "type": "fato",
            "user_id": user_id,
            "created_at": ts,
            "source": source,
            "fingerprint": fingerprint,
            "tags": "fato_pessoal"
        }

        md_content = self._generate_frontmatter(meta)
        md_content += f"# Fato sobre {user_id}\n\n"
        md_content += content
        md_content += f"\n\n---\n*Origem: {source}*"

        file_path.write_text(md_content, encoding="utf-8")
        return True

    def list_recent_facts(self, user_id: str, limit: int = 8) -> list[dict[str, Any]]:
        facts_files = list(FACTS_DIR.glob("Fato_*.md"))
        results = []

        for fpath in facts_files:
            try:
                content = fpath.read_text(encoding="utf-8")
                meta, body = self._extract_frontmatter(content)
                if meta.get("user_id") == user_id:
                    results.append({
                        "file": str(fpath),
                        "created_at": meta.get("created_at", ""),
                        "source": meta.get("source", ""),
                        "content": body.split("\n\n", 1)[-1].split("\n---")[0].strip() # Pega apenas o conteudo textual
                    })
            except Exception:
                continue

        # Ordena por created_at desc (lexicográfico funciona para nromas YYYY-MM-DD)
        results.sort(key=lambda x: x["created_at"], reverse=True)
        return results[:limit]

    def search_facts(self, user_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        query_lower = query.strip().lower()
        all_facts = self.list_recent_facts(user_id, limit=9999)
        matches = [f for f in all_facts if query_lower in f["content"].lower()]
        return matches[:limit]

    # -------------------------------------------------------------
    # Episódios (Conversas)
    # -------------------------------------------------------------

    def save_episode(self, user_id: str, messages: list[dict[str, Any]], source: str, timestamp_label: str = "") -> Path | None:
        if not messages:
            return None

        timestamp = timestamp_label or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        dt_str = datetime.strptime(timestamp, "%Y-%m-%d_%H-%M-%S").strftime("%Y-%m-%d %H:%M:%S")

        file_name = f"Sessao_{timestamp}.md"
        file_path = EPISODES_DIR / file_name

        meta = {
            "type": "episodio",
            "user_id": user_id,
            "created_at": dt_str,
            "source": source,
            "tags": "conversa_diaria"
        }

        md_content = self._generate_frontmatter(meta)
        md_content += f"# Interação ({dt_str})\n\n"

        for idx, msg in enumerate(messages):
            role = str(msg.get("role", "unknown")).upper()
            txt = str(msg.get("content", "")).strip()
            md_content += f"**[{role}]**:\n{txt}\n\n"

        # Guarda tbm o JSON invisivel para fins de programacao
        messages_json = json.dumps(messages, ensure_ascii=False)
        md_content += f"<!-- RAW_MESSAGES: {messages_json} -->\n"

        file_path.write_text(md_content, encoding="utf-8")
        return file_path

    def recent_episode_highlights(self, user_id: str, limit: int = 2) -> list[str]:
        episodes_files = list(EPISODES_DIR.glob("Sessao_*.md"))
        results = []

        for fpath in episodes_files:
            try:
                content = fpath.read_text(encoding="utf-8")
                meta, _ = self._extract_frontmatter(content)
                if meta.get("user_id") == user_id:
                    results.append((meta.get("created_at", ""), meta.get("source", ""), content))
            except Exception:
                continue

        results.sort(key=lambda x: x[0], reverse=True)
        
        highlights = []
        for created_at, source, full_md in results[:limit]:
            # Extrai do body textual os primeiros papos
            lines = []
            for line in full_md.splitlines():
                if line.startswith("**[U") or line.startswith("**[A"):
                    lines.append(line)
                elif line and not line.startswith("---") and not line.startswith("#"):
                    if lines:
                        lines.append(line)
                if len(lines) > 6:
                    break
            
            if lines:
                highlights.append(f"Em {created_at} ({source}):\n" + "\n".join(lines).replace("**[", "[").replace("]**", "]"))
        
        return highlights

    def search_episodes(self, user_id: str, query: str, limit: int = 5) -> list[str]:
        query_lower = query.strip().lower()
        if not query_lower:
            return []

        episodes_files = list(EPISODES_DIR.glob("Sessao_*.md"))
        results = []

        for fpath in episodes_files:
            try:
                content = fpath.read_text(encoding="utf-8")
                meta, body = self._extract_frontmatter(content)
                if meta.get("user_id") == user_id:
                    results.append((meta.get("created_at", ""), meta.get("source", ""), body))
            except Exception:
                continue

        results.sort(key=lambda x: x[0], reverse=True)
        
        matches = []
        for created_at, source, body in results:
            if query_lower in body.lower():
                # Encontrou, tentar extrair a janela do chat
                lines = body.splitlines()
                for idx, line in enumerate(lines):
                    if query_lower in line.lower():
                        # Pega 1 antes e 1 depois
                        start = max(0, idx - 2)
                        end = min(len(lines), idx + 3)
                        snippet = "\n".join(lines[start:end]).replace("**[", "[").replace("]**", "]")
                        matches.append(f"Em {created_at} ({source}):\n{snippet}")
                        break
            if len(matches) >= limit:
                break

        return matches

    # -------------------------------------------------------------
    # Contexto Inicial
    # -------------------------------------------------------------

    def build_context_block(self, user_id: str, fact_limit: int = 6, episode_limit: int = 2) -> str:
        facts = self.list_recent_facts(user_id, limit=fact_limit)
        episodes = self.recent_episode_highlights(user_id, limit=episode_limit)

        sections: list[str] = []
        if facts:
            fact_lines = [f"- {fact['content']}" for fact in facts]
            sections.append("Fatos Pessoais (Obsidian Vault):\n" + "\n".join(fact_lines))
        if episodes:
            sections.append("Contexto Episódico Recente (Obsidian Vault):\n" + "\n\n".join(episodes))
        return "\n\n".join(sections).strip()

obsidian_memory = ObsidianVaultManager()
