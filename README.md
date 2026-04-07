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
