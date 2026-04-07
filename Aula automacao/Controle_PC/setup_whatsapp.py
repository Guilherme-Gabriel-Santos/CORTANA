"""
setup_whatsapp.py
Execute UMA VEZ antes de usar o WhatsApp pela primeira vez:
    python setup_whatsapp.py
"""
import subprocess
import sys

print("=" * 50)
print("  Setup WhatsApp Bridge — Kira v3")
print("=" * 50)

print("\n[1/2] Instalando dependências Python...")
subprocess.run(
    [sys.executable, "-m", "pip", "install",
     "playwright>=1.40.0",
     "fastapi>=0.110.0",
     "uvicorn[standard]>=0.29.0",
     "httpx>=0.27.0",
     "pywin32>=306" if sys.platform == "win32" else "httpx",
    ],
    check=True,
)

print("\n[2/2] Instalando navegador Chromium para Playwright...")
subprocess.run(
    [sys.executable, "-m", "playwright", "install", "chromium"],
    check=True,
)

print("\n" + "=" * 50)
print("✅ Setup concluído!")
print("   Agora execute: python agent.py")
print("   E diga: 'conecta no meu WhatsApp'")
print("=" * 50)
