from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

# numpy e opcional. Usado quando disponivel para armazenar embeddings em float32
# (cerca de 7x menos RAM que listas Python) e para computar a similaridade em
# batch (matrix multiply). Fallback puro-Python preserva a funcionalidade completa.
try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _np = None  # type: ignore
    _HAS_NUMPY = False

logger = logging.getLogger(__name__)

# Diretorio base do Cofre
VAULT_DIR = Path(__file__).resolve().parent.parent.parent / "Obsidian_Vault"

# Estrutura interna do Obsidian
FACTS_DIR = VAULT_DIR / "Fatos"
EPISODES_DIR = VAULT_DIR / "Episodios"
REPORTS_DIR = VAULT_DIR / "Relatorios_Seguranca"
INDEX_DIR = VAULT_DIR / ".index"
EMBEDDINGS_PATH = INDEX_DIR / "embeddings.jsonl"

# Configuracao do provedor de embeddings (Gemini text-embedding-004, 768 dims)
EMBEDDING_MODEL = "text-embedding-004"
EMBEDDING_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent"
)
EMBEDDING_MAX_CHARS = 7500  # ~2048 tokens de margem
EMBEDDING_TIMEOUT_SECONDS = 15
SEMANTIC_MIN_SCORE = 0.55


class ObsidianVaultManager:
    def __init__(self) -> None:
        self._init_vault()
        self._index_lock = threading.Lock()
        self._cached_index: list[dict[str, Any]] | None = None
        self._id_set: set[str] = set()  # O(1) checagem de duplicatas

    def _init_vault(self) -> None:
        VAULT_DIR.mkdir(parents=True, exist_ok=True)
        FACTS_DIR.mkdir(parents=True, exist_ok=True)
        EPISODES_DIR.mkdir(parents=True, exist_ok=True)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        INDEX_DIR.mkdir(parents=True, exist_ok=True)

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
        metadata: dict[str, Any] = {}
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
    # Camada Semantica (embeddings + retrieval vetorial)
    # -------------------------------------------------------------

    def _get_embedding_api_key(self) -> str | None:
        return os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    def _compute_embedding(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float] | None:
        """Chama o Gemini text-embedding-004. Retorna None em caso de falha/ausencia de chave."""
        text = (text or "").strip()
        if not text:
            return None
        api_key = self._get_embedding_api_key()
        if not api_key:
            return None
        truncated = text[:EMBEDDING_MAX_CHARS]
        payload = {
            "model": f"models/{EMBEDDING_MODEL}",
            "content": {"parts": [{"text": truncated}]},
            "taskType": task_type,
        }
        try:
            resp = requests.post(
                f"{EMBEDDING_ENDPOINT}?key={api_key}",
                json=payload,
                timeout=EMBEDDING_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            data = resp.json()
            values = (data.get("embedding") or {}).get("values")
            if isinstance(values, list) and values:
                return [float(x) for x in values]
        except Exception as exc:
            logger.debug("[Obsidian] Falha ao gerar embedding: %s", exc)
        return None

    def _embed_query(self, text: str) -> list[float] | None:
        return self._compute_embedding(text, task_type="RETRIEVAL_QUERY")

    def _to_storage_vec(self, values: list[float]):
        """Converte vetor Python em float32 numpy (se disponivel) para reduzir RAM."""
        if _HAS_NUMPY:
            return _np.asarray(values, dtype=_np.float32)
        return [float(x) for x in values]

    @staticmethod
    def _cosine(a, b) -> float:
        """Cosseno entre dois vetores. Aceita lista Python ou numpy array."""
        if _HAS_NUMPY and isinstance(a, _np.ndarray) and isinstance(b, _np.ndarray):
            if a.size == 0 or b.size == 0 or a.size != b.size:
                return 0.0
            na = float(_np.linalg.norm(a))
            nb = float(_np.linalg.norm(b))
            if na == 0.0 or nb == 0.0:
                return 0.0
            return float(_np.dot(a, b) / (na * nb))
        # Fallback puro-Python
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = 0.0
        na = 0.0
        nb = 0.0
        for x, y in zip(a, b):
            dot += x * y
            na += x * x
            nb += y * y
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (math.sqrt(na) * math.sqrt(nb))

    def _make_slim(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Converte uma entrada bruta do JSONL em entrada enxuta (sem 'text' em RAM)."""
        vec = raw.get("vector")
        entry_id = raw.get("id")
        if not vec or not entry_id:
            return None
        slim: dict[str, Any] = {
            "id": entry_id,
            "kind": raw.get("kind"),
            "user_id": raw.get("user_id"),
            "file": raw.get("file"),
            "created_at": raw.get("created_at", ""),
            "vector": self._to_storage_vec(vec),
        }
        if raw.get("source"):
            slim["source"] = raw["source"]
        return slim

    def _load_index(self, force: bool = False) -> list[dict[str, Any]]:
        with self._index_lock:
            if self._cached_index is not None and not force:
                return self._cached_index
            entries: list[dict[str, Any]] = []
            id_set: set[str] = set()
            if EMBEDDINGS_PATH.exists():
                try:
                    with EMBEDDINGS_PATH.open("r", encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                raw = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            slim = self._make_slim(raw)
                            if not slim:
                                continue
                            entries.append(slim)
                            id_set.add(slim["id"])
                except Exception as exc:
                    logger.warning("[Obsidian] Falha ao ler indice semantico: %s", exc)
            self._cached_index = entries
            self._id_set = id_set
            return entries

    def _append_index(self, entry: dict[str, Any]) -> None:
        """Grava no JSONL (formato portavel) e cacheia versao enxuta em RAM."""
        # Garante que o cache foi construido para poder registrar o id
        self._load_index()

        # Prepara payload para disco (vetor vira lista JSON)
        disk_payload = dict(entry)
        vec = disk_payload.get("vector")
        if _HAS_NUMPY and isinstance(vec, _np.ndarray):
            disk_payload["vector"] = vec.tolist()

        with self._index_lock:
            try:
                with EMBEDDINGS_PATH.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(disk_payload, ensure_ascii=False) + "\n")
            except Exception as exc:
                logger.warning("[Obsidian] Falha ao salvar entrada no indice: %s", exc)
                return

            # cache so a versao slim (sem 'text')
            slim = self._make_slim(entry)
            if slim is None:
                return
            if self._cached_index is None:
                self._cached_index = []
            self._cached_index.append(slim)
            self._id_set.add(slim["id"])

    def _already_indexed(self, entry_id: str) -> bool:
        self._load_index()
        return entry_id in self._id_set

    def _index_fact(self, user_id: str, fingerprint: str, file_path: Path, content: str, created_at: str) -> bool:
        entry_id = f"fact:{fingerprint}"
        if self._already_indexed(entry_id):
            return False
        vector = self._compute_embedding(content, task_type="RETRIEVAL_DOCUMENT")
        if not vector:
            return False
        self._append_index({
            "id": entry_id,
            "kind": "fact",
            "user_id": user_id,
            "file": file_path.name,
            "text": content,
            "created_at": created_at,
            "vector": vector,
        })
        return True

    def _index_episode(
        self,
        user_id: str,
        file_path: Path,
        text_for_embedding: str,
        created_at: str,
        source: str,
    ) -> bool:
        digest = hashlib.sha1(file_path.name.encode("utf-8")).hexdigest()
        entry_id = f"episode:{digest}"
        if self._already_indexed(entry_id):
            return False
        vector = self._compute_embedding(text_for_embedding, task_type="RETRIEVAL_DOCUMENT")
        if not vector:
            return False
        self._append_index({
            "id": entry_id,
            "kind": "episode",
            "user_id": user_id,
            "file": file_path.name,
            "source": source,
            "text": text_for_embedding[:2000],
            "created_at": created_at,
            "vector": vector,
        })
        return True

    def _semantic_search(
        self,
        user_id: str,
        query: str,
        kind: str,
        limit: int = 5,
        min_score: float = SEMANTIC_MIN_SCORE,
    ) -> list[tuple[float, dict[str, Any]]]:
        query_vec = self._embed_query(query)
        if not query_vec:
            return []
        index = self._load_index()

        # Filtra candidatos uma vez so
        candidates = [
            e for e in index
            if e.get("kind") == kind and e.get("user_id") == user_id and e.get("vector") is not None
        ]
        if not candidates:
            return []

        # Caminho vetorizado (numpy): calcula todas as similaridades em uma multiplicacao
        if _HAS_NUMPY:
            q = _np.asarray(query_vec, dtype=_np.float32)
            q_norm = float(_np.linalg.norm(q))
            if q_norm == 0.0:
                return []
            try:
                mat = _np.vstack([e["vector"] for e in candidates])
            except ValueError:
                # vetores de dimensoes diferentes - cai no fallback
                mat = None
            if mat is not None:
                row_norms = _np.linalg.norm(mat, axis=1)
                safe_norms = _np.where(row_norms == 0.0, 1.0, row_norms)
                scores = (mat @ q) / (safe_norms * q_norm)
                scores = _np.where(row_norms == 0.0, 0.0, scores)
                # Top-k via argpartition (O(N) em vez de O(N log N))
                k = min(limit, scores.size)
                if k <= 0:
                    return []
                top_idx = _np.argpartition(-scores, k - 1)[:k]
                top_idx = top_idx[_np.argsort(-scores[top_idx])]
                result: list[tuple[float, dict[str, Any]]] = []
                for i in top_idx:
                    s = float(scores[int(i)])
                    if s < min_score:
                        break
                    result.append((s, candidates[int(i)]))
                return result

        # Fallback puro-Python
        scored: list[tuple[float, dict[str, Any]]] = []
        for entry in candidates:
            score = self._cosine(query_vec, entry.get("vector", []))
            if score >= min_score:
                scored.append((score, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:limit]

    def reconcile_index(self, user_id: str) -> dict[str, int]:
        """Varre Fatos e Episodios e indexa o que ainda nao esta no embeddings.jsonl."""
        counts = {"facts_indexed": 0, "episodes_indexed": 0, "skipped": 0}

        # Fatos
        for fpath in FACTS_DIR.glob("Fato_*.md"):
            try:
                content = fpath.read_text(encoding="utf-8")
                meta, body = self._extract_frontmatter(content)
                if meta.get("user_id") != user_id:
                    counts["skipped"] += 1
                    continue
                fingerprint = meta.get("fingerprint", "")
                if not fingerprint:
                    counts["skipped"] += 1
                    continue
                entry_id = f"fact:{fingerprint}"
                if self._already_indexed(entry_id):
                    continue
                fact_text = body.split("\n\n", 1)[-1].split("\n---")[0].strip()
                vector = self._compute_embedding(fact_text, task_type="RETRIEVAL_DOCUMENT")
                if not vector:
                    counts["skipped"] += 1
                    continue
                self._append_index({
                    "id": entry_id,
                    "kind": "fact",
                    "user_id": user_id,
                    "file": fpath.name,
                    "text": fact_text,
                    "created_at": meta.get("created_at", ""),
                    "vector": vector,
                })
                counts["facts_indexed"] += 1
            except Exception as exc:
                logger.debug("[Obsidian] Erro reconciliando fato %s: %s", fpath.name, exc)
                counts["skipped"] += 1

        # Episodios
        for fpath in EPISODES_DIR.glob("Sessao_*.md"):
            try:
                content = fpath.read_text(encoding="utf-8")
                meta, body = self._extract_frontmatter(content)
                if meta.get("user_id") != user_id:
                    counts["skipped"] += 1
                    continue
                digest = hashlib.sha1(fpath.name.encode("utf-8")).hexdigest()
                entry_id = f"episode:{digest}"
                if self._already_indexed(entry_id):
                    continue
                text_for_embedding = body.split("<!-- RAW_MESSAGES:", 1)[0].strip()
                vector = self._compute_embedding(text_for_embedding, task_type="RETRIEVAL_DOCUMENT")
                if not vector:
                    counts["skipped"] += 1
                    continue
                self._append_index({
                    "id": entry_id,
                    "kind": "episode",
                    "user_id": user_id,
                    "file": fpath.name,
                    "source": meta.get("source", ""),
                    "text": text_for_embedding[:2000],
                    "created_at": meta.get("created_at", ""),
                    "vector": vector,
                })
                counts["episodes_indexed"] += 1
            except Exception as exc:
                logger.debug("[Obsidian] Erro reconciliando episodio %s: %s", fpath.name, exc)
                counts["skipped"] += 1

        return counts

    # -------------------------------------------------------------
    # Fatos
    # -------------------------------------------------------------

    def add_fact(self, user_id: str, content: str, source: str = "online", timestamp: str = "") -> bool:
        content = str(content).strip()
        if not content:
            return False

        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fingerprint = hashlib.sha1(f"{user_id}:{content.lower()}".encode("utf-8")).hexdigest()

        snippet = self._sanitize_filename(content)
        file_name = f"Fato_{snippet}_{fingerprint[:6]}.md"
        file_path = FACTS_DIR / file_name

        if file_path.exists():
            return False

        meta = {
            "type": "fato",
            "user_id": user_id,
            "created_at": ts,
            "source": source,
            "fingerprint": fingerprint,
            "tags": "fato_pessoal",
        }

        md_content = self._generate_frontmatter(meta)
        md_content += f"# Fato sobre {user_id}\n\n"
        md_content += content
        md_content += f"\n\n---\n*Origem: {source}*"

        file_path.write_text(md_content, encoding="utf-8")

        try:
            self._index_fact(user_id, fingerprint, file_path, content, ts)
        except Exception as exc:
            logger.debug("[Obsidian] Falha ao indexar fato: %s", exc)

        return True

    def list_recent_facts(self, user_id: str, limit: int = 8) -> list[dict[str, Any]]:
        """
        Streaming: itera arquivos sem carregar tudo em memoria. Mantem uma janela
        crescente de tamanho `limit` quando possivel. Para `limit` gigante ainda
        carrega todos, mas isso so e usado internamente em buscas (fallback keyword).
        """
        results: list[dict[str, Any]] = []
        for fpath in FACTS_DIR.glob("Fato_*.md"):
            try:
                content = fpath.read_text(encoding="utf-8")
                meta, body = self._extract_frontmatter(content)
                if meta.get("user_id") != user_id:
                    continue
                results.append({
                    "file": str(fpath),
                    "created_at": meta.get("created_at", ""),
                    "source": meta.get("source", ""),
                    "content": body.split("\n\n", 1)[-1].split("\n---")[0].strip(),
                })
            except Exception:
                continue

        results.sort(key=lambda x: x["created_at"], reverse=True)
        return results[:limit]

    def search_facts(self, user_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Busca hibrida: semantica primeiro, fallback por keyword para completar o limite."""
        query = (query or "").strip()
        if not query:
            return []
        query_lower = query.lower()

        results: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        # 1) Camada semantica — busca pelos nomes de arquivo batendo com o index
        semantic_hits = self._semantic_search(user_id, query, kind="fact", limit=limit)
        wanted_names = {h[1].get("file") for h in semantic_hits}
        hits_by_name: dict[str, float] = {h[1].get("file"): h[0] for h in semantic_hits}

        # So carrega os .md dos fatos que deram match semantico (evita ler todo o disco)
        if wanted_names:
            for fpath in FACTS_DIR.glob("Fato_*.md"):
                if fpath.name not in wanted_names:
                    continue
                try:
                    content = fpath.read_text(encoding="utf-8")
                    meta, body = self._extract_frontmatter(content)
                    if meta.get("user_id") != user_id:
                        continue
                    score = hits_by_name.get(fpath.name, 0.0)
                    results.append({
                        "file": str(fpath),
                        "created_at": meta.get("created_at", ""),
                        "source": meta.get("source", ""),
                        "content": body.split("\n\n", 1)[-1].split("\n---")[0].strip(),
                        "score": round(float(score), 3),
                        "match": "semantic",
                    })
                    seen_names.add(fpath.name)
                except Exception:
                    continue
            # Preserva ordem por score desc
            results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            if len(results) >= limit:
                return results[:limit]

        # 2) Fallback keyword — so percorre os fatos ainda nao vistos
        keyword_hits: list[dict[str, Any]] = []
        for fpath in FACTS_DIR.glob("Fato_*.md"):
            if fpath.name in seen_names:
                continue
            try:
                content = fpath.read_text(encoding="utf-8")
                meta, body = self._extract_frontmatter(content)
                if meta.get("user_id") != user_id:
                    continue
                text_body = body.split("\n\n", 1)[-1].split("\n---")[0].strip()
                if query_lower not in text_body.lower():
                    continue
                keyword_hits.append({
                    "file": str(fpath),
                    "created_at": meta.get("created_at", ""),
                    "source": meta.get("source", ""),
                    "content": text_body,
                    "match": "keyword",
                })
            except Exception:
                continue

        keyword_hits.sort(key=lambda x: x["created_at"], reverse=True)
        results.extend(keyword_hits)
        return results[:limit]

    # -------------------------------------------------------------
    # Episodios (Conversas)
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
            "tags": "conversa_diaria",
        }

        # Monta em chunks para evitar concatenacao N^2 de strings
        parts: list[str] = [self._generate_frontmatter(meta), f"# Interacao ({dt_str})\n\n"]
        text_parts: list[str] = []
        for msg in messages:
            role = str(msg.get("role", "unknown")).upper()
            txt = str(msg.get("content", "")).strip()
            parts.append(f"**[{role}]**:\n{txt}\n\n")
            text_parts.append(f"[{role}] {txt}")

        messages_json = json.dumps(messages, ensure_ascii=False)
        parts.append(f"<!-- RAW_MESSAGES: {messages_json} -->\n")

        file_path.write_text("".join(parts), encoding="utf-8")

        try:
            text_for_embedding = "\n".join(text_parts).strip()
            self._index_episode(user_id, file_path, text_for_embedding, dt_str, source)
        except Exception as exc:
            logger.debug("[Obsidian] Falha ao indexar episodio: %s", exc)

        return file_path

    def recent_episode_highlights(self, user_id: str, limit: int = 2) -> list[str]:
        """
        Otimizado: le apenas os `limit` arquivos mais recentes pelo mtime,
        em vez de carregar todos em RAM. Mantem exatamente o mesmo retorno.
        """
        episode_paths = sorted(
            EPISODES_DIR.glob("Sessao_*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        highlights: list[str] = []
        for fpath in episode_paths:
            if len(highlights) >= limit:
                break
            try:
                content = fpath.read_text(encoding="utf-8")
                meta, _ = self._extract_frontmatter(content)
                if meta.get("user_id") != user_id:
                    continue
                created_at = meta.get("created_at", "")
                source = meta.get("source", "")

                lines: list[str] = []
                for line in content.splitlines():
                    if line.startswith("**[U") or line.startswith("**[A"):
                        lines.append(line)
                    elif line and not line.startswith("---") and not line.startswith("#"):
                        if lines:
                            lines.append(line)
                    if len(lines) > 6:
                        break

                if lines:
                    highlights.append(
                        f"Em {created_at} ({source}):\n"
                        + "\n".join(lines).replace("**[", "[").replace("]**", "]")
                    )
            except Exception:
                continue

        return highlights

    def _extract_episode_snippet(self, body: str, query_lower: str, radius: int = 2) -> str | None:
        if not query_lower:
            return None
        lines = body.splitlines()
        for idx, line in enumerate(lines):
            if query_lower in line.lower():
                start = max(0, idx - radius)
                end = min(len(lines), idx + radius + 1)
                return "\n".join(lines[start:end]).replace("**[", "[").replace("]**", "]")
        return None

    def search_episodes(self, user_id: str, query: str, limit: int = 5) -> list[str]:
        """Busca hibrida em episodios: semantica primeiro, fallback por keyword."""
        query = (query or "").strip()
        if not query:
            return []
        query_lower = query.lower()

        matches: list[str] = []
        used_files: set[str] = set()

        # 1) Camada semantica — le apenas os arquivos que bateram no index
        semantic_hits = self._semantic_search(user_id, query, kind="episode", limit=limit)
        wanted = {h[1].get("file"): h[0] for h in semantic_hits}
        if wanted:
            # percorre na ordem dos scores desc
            ordered_wanted = sorted(wanted.items(), key=lambda kv: kv[1], reverse=True)
            for file_name, score in ordered_wanted:
                fpath = EPISODES_DIR / file_name
                if not fpath.exists():
                    continue
                try:
                    content = fpath.read_text(encoding="utf-8")
                    meta, body = self._extract_frontmatter(content)
                    if meta.get("user_id") != user_id:
                        continue
                    created_at = meta.get("created_at", "")
                    source = meta.get("source", "")
                    snippet = self._extract_episode_snippet(body, query_lower)
                    if not snippet:
                        snippet = (body[:400].strip() + "...") if len(body) > 400 else body.strip()
                    matches.append(f"[sem {score:.2f}] Em {created_at} ({source}):\n{snippet}")
                    used_files.add(file_name)
                    if len(matches) >= limit:
                        return matches
                except Exception:
                    continue

        # 2) Fallback keyword — itera sem acumular tudo em RAM
        episode_paths = sorted(
            EPISODES_DIR.glob("Sessao_*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for fpath in episode_paths:
            if fpath.name in used_files:
                continue
            if len(matches) >= limit:
                break
            try:
                content = fpath.read_text(encoding="utf-8")
                meta, body = self._extract_frontmatter(content)
                if meta.get("user_id") != user_id:
                    continue
                if query_lower not in body.lower():
                    continue
                snippet = self._extract_episode_snippet(body, query_lower) or body[:400].strip()
                created_at = meta.get("created_at", "")
                source = meta.get("source", "")
                matches.append(f"Em {created_at} ({source}):\n{snippet}")
                used_files.add(fpath.name)
            except Exception:
                continue

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
            sections.append(
                "Contexto Episodico Recente (Obsidian Vault):\n" + "\n\n".join(episodes)
            )
        return "\n\n".join(sections).strip()


obsidian_memory = ObsidianVaultManager()
