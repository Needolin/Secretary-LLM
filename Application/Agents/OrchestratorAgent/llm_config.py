"""Настройки подключения к Cloud.ru Foundation Models."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BASE_URL = "https://foundation-models.api.cloud.ru/v1"
DEFAULT_MODEL = "openai/gpt-5-mini"


def load_cloud_settings() -> tuple[str, str, str]:
    """Загружает .env и возвращает (api_key, base_url, model)."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    api_key = os.getenv("CLOUD_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Не найден CLOUD_API_KEY. Создайте API-ключ Cloud.ru Foundation Models "
            "и добавьте его в файл .env в корне проекта. См. .env.example и README.md."
        )

    model = os.getenv("CLOUD_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return api_key, BASE_URL, model


def create_chat_model():
    """Создаёт ChatOpenAI-клиент для OpenAI-совместимого API Cloud.ru."""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise RuntimeError(
            "Не установлен langchain-openai. Выполните: python -m pip install -r requirements.txt"
        ) from exc

    api_key, base_url, model = load_cloud_settings()
    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=0,
        max_tokens=1200,
        timeout=60,
        max_retries=1,
        use_responses_api=False,
    )
