"""Модуль интеграции с моделями ИИ (локальные + облачные)."""
from __future__ import annotations

import json
import httpx
from typing import Any

from . import config
from .db import db

SYSTEM_PROMPT = """Ты — эксперт по проверке проектной документации инженерных систем зданий.
Анализируй предоставленные данные строго по фактам.

Правила:
1. Если не хватает исходных данных — не выдумывай. Сообщи, каких данных не хватает.
2. Если какие-либо проверки не проводились — сообщи об этом с указанием причины.
3. Не придумывай — давай ответ как есть.
"""


def _get_active_models() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM ai_models WHERE is_enabled = 1 ORDER BY kind, is_default DESC, id"
        ).fetchall()
        return [dict(r) for r in rows]


async def analyze_text(text: str, mode: str = "local", model_name: str = "") -> dict[str, Any]:
    """Отправляет текст на анализ в модель ИИ в зависимости от режима."""
    if mode == "local":
        return await _ollama_query(text, model_name)
    elif mode == "cloud":
        # Берём первую включённую облачную модель
        models = _get_active_models()
        cloud = [m for m in models if m["kind"] == "cloud"]
        if cloud:
            return await _api_query(text, cloud[0])
        return {"error": "Нет активных облачных моделей. Добавьте модель через настройки."}
    elif mode == "hybrid":
        # Пробуем локальную, при ошибке — облачную
        result = await _ollama_query(text, model_name)
        if "error" not in result:
            return result
        models = _get_active_models()
        cloud = [m for m in models if m["kind"] == "cloud"]
        if cloud:
            return await _api_query(text, cloud[0])
        return result
    return {"error": "Неизвестный режим"}


async def _ollama_query(text: str, model_name: str = "") -> dict[str, Any]:
    """Запрос к локальному Ollama."""
    base_url = config.OLLAMA_BASE_URL
    if not base_url:
        return {"error": "OLLAMA_BASE_URL не задан. Укажите его в docker-compose.yml"}
    model = model_name or "qwen2.5:7b-instruct"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return {"model": model, "response": data.get("message", {}).get("content", ""), "provider": "ollama"}
    except Exception as e:
        return {"error": f"Ошибка Ollama ({model}): {e}"}


async def _api_query(text: str, model_cfg: dict) -> dict[str, Any]:
    """Запрос к облачной модели через OpenAI-совместимый API."""
    base_url = model_cfg.get("base_url", "").rstrip("/")
    if not base_url:
        return {"error": "Не указан base_url для облачной модели"}
    api_key = model_cfg.get("api_key", "")
    model_id = model_cfg.get("model_id", "")
    if not api_key:
        return {"error": f"Не указан API-ключ для модели {model_cfg.get('name', '—')}"}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_id,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "model": model_id,
                "response": data.get("choices", [{}])[0].get("message", {}).get("content", ""),
                "provider": model_cfg.get("name", "cloud"),
            }
    except Exception as e:
        return {"error": f"Ошибка API ({model_id}): {e}"}