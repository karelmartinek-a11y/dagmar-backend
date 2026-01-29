from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter(tags=["webhook-whatsapp"])


def _env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


async def _call_openai(prompt: str) -> str:
    api_key = _env("OPENAI_API_KEY")
    model = _env("OPENAI_MODEL", "gpt-4.1-mini")
    if not api_key:
        return ""

    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "Jsi stručný a zdvořilý asistent hotelu. Odpovídej česky do 80 slov.",
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 120,
        "temperature": 0.6,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception:
            return ""


async def _send_whatsapp(text: str, to: str) -> None:
    token = _env("WHATSAPP_TOKEN")
    phone_id = _env("WHATSAPP_PHONE_NUMBER_ID")
    if not token or not phone_id:
        raise RuntimeError("Chybí WHATSAPP_TOKEN nebo WHATSAPP_PHONE_NUMBER_ID")

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()


@router.get("/webhook/whatsapp", include_in_schema=False)
async def whatsapp_verify(mode: str | None = None, hub_mode: str | None = None, hub_challenge: str | None = None, hub_verify_token: str | None = None):
    # Meta sends hub.* params; FastAPI lowercases/underscores them.
    token = _env("WHATSAPP_VERIFY_TOKEN")
    if (mode == "subscribe" or hub_mode == "subscribe") and token and hub_verify_token == token:
        return int(hub_challenge or 0)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="verify failed")


@router.post("/webhook/whatsapp", include_in_schema=False)
async def whatsapp_webhook(request: Request) -> dict[str, Any]:
    body = await request.json()
    entries = body.get("entry", [])
    replies = []
    for entry in entries:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            for msg in messages:
                from_id = msg.get("from")
                text = msg.get("text", {}).get("body", "")
                if not from_id or not text:
                    continue
                # Vygeneruj odpověď
                reply = ""
                try:
                    reply = await _call_openai(text)
                except Exception:
                    reply = ""
                if not reply:
                    reply = "Děkujeme za zprávu, ozveme se co nejdříve."
                try:
                    await _send_whatsapp(reply, from_id)
                    replies.append({"to": from_id, "status": "sent"})
                except Exception as e:
                    replies.append({"to": from_id, "status": f"error: {e}"})
    return {"ok": True, "handled": len(replies), "replies": replies}
