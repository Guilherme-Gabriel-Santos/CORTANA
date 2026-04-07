# 🤖 Kira v3 — Assistente WhatsApp

## Instalação (1 vez só)

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Executar

```bash
python main.py
```

## Como funciona

1. Clique em **Conectar**
2. Na **primeira vez**: aparece QR Code — escaneie pelo celular
3. Da **segunda vez em diante**: entra direto (sessão salva em `kira_session/`)
4. O navegador fica **completamente invisível** — sem janela, sem popup
5. Kira monitora mensagens e notifica na tela

## Configurar seu nome

Abra `main.py` linha 16:
```python
SENDER_NAME = "Alan"  ← seu nome aqui
```

## Modo headless (usado pelo kira_app.py)
Para rodar sem janela gráfica:
    python main.py --headless
Ou via variável de ambiente:
    set KIRA_WPP_HEADLESS=1 && python main.py
