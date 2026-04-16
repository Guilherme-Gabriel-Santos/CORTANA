import json
from pathlib import Path
from shared_memory import shared_memory
from obsidian_memory import obsidian_memory

print("Iniciando migracao do banco SQLite/JSON para o Obsidian Vault...")

# 1. Migrar Fatos
with shared_memory._lock, shared_memory._connect() as connection:
    facts = connection.execute("SELECT * FROM facts").fetchall()

facts_count = 0
for fact in facts:
    user_id = fact["user_id"]
    content = fact["content"]
    source = fact["source"]
    created_at = fact["created_at"]
    # Forçar adicao mesmo se duplicado, o obsidian memory resolve
    obsidian_memory.add_fact(user_id, content, source, created_at)
    facts_count += 1

print(f"Migracao concluida: {facts_count} fatos movidos para o Obsidian.")

# 2. Migrar Episodios (Sessoes passadas)
with shared_memory._lock, shared_memory._connect() as connection:
    episodes = connection.execute("SELECT * FROM episodes").fetchall()

eps_count = 0
for ep in episodes:
    user_id = ep["user_id"]
    source = ep["source"]
    created_at = ep["created_at"]
    
    # Criar um label a partir do created_at (YYYY-MM-DD HH:MM:SS para YYYY-MM-DD_HH-MM-SS)
    timestamp_label = created_at.replace(" ", "_").replace(":", "-")
    
    try:
        messages = json.loads(ep["messages_json"])
        obsidian_memory.save_episode(user_id, messages, source, timestamp_label)
        eps_count += 1
    except Exception as e:
        print(f"Erro ao migrar episodio {created_at}: {e}")

print(f"Migracao concluida: {eps_count} episodios movidos para o Obsidian.")
print("Verifique a pasta CORTANA/Obsidian_Vault!")
