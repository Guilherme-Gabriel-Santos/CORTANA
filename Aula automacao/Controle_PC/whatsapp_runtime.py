"""
whatsapp_runtime.py
Módulo de integração: envia mensagens, notifica agente por voz, batching.
Interface entre o Agente LiveKit e o Bridge HTTP (Kira v3).
"""

import httpx
import logging
import asyncio

logger = logging.getLogger(__name__)

BRIDGE_URL = "http://127.0.0.1:5050"

def build_whatsapp_notifier(agent, speech_coordinator):
    """
    Cria a função de callback que será chamada para cada nova mensagem recebida pelo bridge.
    
    O bridge consome /messages/new/agent e para cada mensagem chama este notifier.
    """
    async def notify(contact: str, text: str):
        # Delega toda a lógica de decisão e registro para o agente
        if hasattr(agent, 'handle_whatsapp_notif'):
            await agent.handle_whatsapp_notif(contact, text)
        if hasattr(agent, 'remember_whatsapp_message'):
            agent.remember_whatsapp_message(contact, text)
            
    return notify

async def send_whatsapp_message(contact: str, message: str):
    """
    Envia uma mensagem de texto para o bridge HTTP na porta 5050.
    """
    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            payload = {"contact": contact, "message": message}
            resp = await client.post(f"{BRIDGE_URL}/send", json=payload)
            if resp.status_code == 200:
                return resp.json()
            else:
                return {"success": False, "message": f"Erro HTTP {resp.status_code}"}
    except Exception as e:
        logger.error(f"[WPP RUNTIME] Falha ao enviar: {e}")
        return {"success": False, "message": str(e)}

async def get_whatsapp_status():
    """
    Consulta o status atual da conexão no bridge.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{BRIDGE_URL}/status")
            if resp.status_code == 200:
                return resp.json()
            return {"connected": False, "controller_active": False}
    except Exception:
        return {"connected": False, "controller_active": False}
