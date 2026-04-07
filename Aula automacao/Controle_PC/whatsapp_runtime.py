"""
whatsapp_runtime.py
Runtime helpers for sending WhatsApp messages and forwarding bridge events to the agent.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

BRIDGE_URL = "http://127.0.0.1:5050"


def build_whatsapp_notifier(agent, speech_coordinator):
    async def notify(contact: str, text: str):
        if hasattr(agent, "handle_whatsapp_notif"):
            await agent.handle_whatsapp_notif(contact, text)
        elif hasattr(agent, "remember_whatsapp_message"):
            agent.remember_whatsapp_message(contact, text)

    return notify


async def send_whatsapp_message(contact: str, message: str):
    try:
        async with httpx.AsyncClient(timeout=35.0) as client:
            payload = {"contact": contact, "message": message}
            response = await client.post(f"{BRIDGE_URL}/send", json=payload)
            if response.status_code == 200:
                return response.json()
            return {"success": False, "message": f"Erro HTTP {response.status_code}"}
    except Exception as exc:
        logger.error("[WPP Runtime] Failed to send message: %s", exc)
        return {"success": False, "message": str(exc)}


async def get_whatsapp_status():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{BRIDGE_URL}/status")
            if response.status_code == 200:
                return response.json()
            return {"connected": False, "controller_active": False}
    except Exception:
        return {"connected": False, "controller_active": False}
