# CORTANA Workspace

## Estrutura atual

Projeto ativo:
`Aula automacao/Controle_PC`

Frontend de apoio:
`Layout Cortana/agent-starter-react-main`

Historico / prototipos:
`Cortana Mem0`
`Cortana- Aula 01`
`kira-v3`
`backup_wpp_v1_legacy`

## O que mudou nesta limpeza

- Segredos e arquivos gerados agora ficam fora do Git.
- O backend principal foi consolidado em uma unica versao do `agent.py`.
- A automacao local ganhou travas para operacoes destrutivas.
- Scripts de camera e RTSP deixaram de usar credenciais hardcoded.

## Ambiente

Use os arquivos de exemplo para configurar seu ambiente local:

- `Aula automacao/Controle_PC/.env.example`
- `Cortana- Aula 01/.env.example`
- `Layout Cortana/agent-starter-react-main/.env.example`

## Observacoes

- `Aula automacao/Controle_PC` deve ser tratada como a base principal.
- As outras pastas foram mantidas sem mover nada para nao quebrar caminhos locais, historico ou automacoes ja existentes.
- O frontend Next.js continua como base de interface LiveKit customizada para a Cortana, mas o nucleo funcional hoje esta no backend Python.

## Face ID Local

- O backend principal agora suporta desbloqueio facial local pela webcam em `Aula automacao/Controle_PC`.
- Cadastro inicial: execute `python setup_face_auth.py` dentro de `Aula automacao/Controle_PC`.
- Depois do cadastro, a Cortana so inicia e executa ferramentas sensiveis quando reconhecer o rosto autorizado.
- O modo padrao atual usa o rosto apenas para desbloquear o inicio da sessao; sair da frente da camera nao encerra mais a conversa.
- O perfil facial aprende novas amostras ao longo do uso e as salva em `memory/face_auth/adaptive_samples`.

## Versao Offline

- A Cortana online atual continua intacta em `Aula automacao/Controle_PC/agent.py`.
- A nova versao offline roda separada em `Aula automacao/Controle_PC/offline_runtime.py`.
- As duas compartilham a mesma memoria unificada em `Obsidian_Vault/` (Fatos e Episodios em Markdown, com indice semantico em `.index/embeddings.jsonl`).
- Conversas continuam gerando snapshots episodicos em `Obsidian_Vault/Episodios/`.

### Como usar

- Configuracao local da versao offline: `Aula automacao/Controle_PC/.env.offline.example`
- Runner PowerShell da versao offline: `Aula automacao/Controle_PC/run_cortana_offline.ps1`
- App desktop da versao offline: `Aula automacao/Controle_PC/offline_desktop_app.py`
- Runner PowerShell do app desktop: `Aula automacao/Controle_PC/run_cortana_offline_desktop.ps1`
- Build do executavel desktop: `Aula automacao/Controle_PC/build_cortana_offline_desktop.ps1`
- Dependencias dedicadas da versao offline: `Aula automacao/Controle_PC/requirements-offline.txt`

### Stack da versao offline

- LLM local: Ollama
- STT local: faster-whisper
- TTS principal: Edge TTS com fallback local do Windows
- Memoria unificada: Obsidian Vault (Markdown) com indice semantico via embeddings
- Ferramentas: reaproveita a automacao Python ja existente sem depender de navegador

### Compartilhamento de memoria

- Online e offline escrevem e leem do mesmo `Obsidian_Vault/`, entao qualquer fato ou episodio novo ja fica disponivel para as duas instancias.
- O indice semantico (`Obsidian_Vault/.index/embeddings.jsonl`) e recompilado automaticamente conforme Fatos/Episodios sao criados, e pode ser reconstruido a qualquer momento pelo botao do app desktop offline.

### Observacao sobre voz

- A voz `Aoede` do Gemini continua exclusiva da versao online.
- A versao offline agora usa `Edge TTS` por padrao com voz feminina PT-BR para ficar bem mais natural.
- Isso melhora muito a fala, mas reintroduz dependencia de internet apenas para a sintese de voz.
