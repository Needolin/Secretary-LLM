from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from Application.Database import database


DATABASE_DIR = Path(__file__).resolve().parent
CHATS_FILE = DATABASE_DIR / "chats.json"
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _ensure_file() -> None:
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    if not CHATS_FILE.exists():
        CHATS_FILE.write_text("[]\n", encoding="utf-8")


def _load_unlocked() -> list[dict[str, Any]]:
    _ensure_file()
    try:
        chats = json.loads(CHATS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Файл chats.json содержит некорректный JSON.") from exc

    if not isinstance(chats, list) or any(not isinstance(chat, dict) for chat in chats):
        raise RuntimeError("В chats.json должен находиться список чатов.")

    changed = False
    for chat in chats:
        chat.setdefault("pinned", False)
        chat.setdefault("messages", [])
        chat.setdefault("created_at", _now())
        chat.setdefault("updated_at", chat["created_at"])
        chat.setdefault("title", "Новый чат")
        if not isinstance(chat["messages"], list):
            chat["messages"] = []
            changed = True
        if not isinstance(chat["pinned"], bool):
            chat["pinned"] = False
            changed = True

    if changed:
        _save_unlocked(chats)
    return chats


def _save_unlocked(chats: list[dict[str, Any]]) -> None:
    _ensure_file()
    temp = CHATS_FILE.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(chats, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(CHATS_FILE)


def _normalise_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("ID чата должен быть положительным целым числом.")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("ID чата должен быть положительным целым числом.") from exc
    if result <= 0:
        raise ValueError("ID чата должен быть положительным целым числом.")
    return result


def _find_chat(chats: list[dict[str, Any]], chat_id: Any) -> dict[str, Any]:
    wanted = _normalise_id(chat_id)
    for chat in chats:
        if int(chat.get("id", 0)) == wanted:
            return chat
    raise ValueError(f"Чат с ID {wanted} не найден.")


def load_chats(schedule_id: int | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        chats = _load_unlocked()
        if schedule_id is not None:
            wanted = _normalise_id(schedule_id)
            chats = [chat for chat in chats if int(chat.get("schedule_id", 0)) == wanted]
        return sorted(
            (dict(chat) for chat in chats),
            key=lambda chat: (bool(chat.get("pinned")), chat.get("updated_at", "")),
            reverse=True,
        )


def get_chat(chat_id: int) -> dict[str, Any]:
    with _LOCK:
        return dict(_find_chat(_load_unlocked(), chat_id))


def create_chat(schedule_id: int, title: str = "Новый чат") -> dict[str, Any]:
    schedule_id = _normalise_id(schedule_id)
    # Проверяем, что чат привязан к реально существующему расписанию.
    database.get_schedule(schedule_id)
    clean_title = str(title or "Новый чат").strip()[:80] or "Новый чат"

    with _LOCK:
        chats = _load_unlocked()
        next_id = max((int(chat.get("id", 0)) for chat in chats), default=0) + 1
        timestamp = _now()
        chat = {
            "id": next_id,
            "schedule_id": schedule_id,
            "title": clean_title,
            "pinned": False,
            "created_at": timestamp,
            "updated_at": timestamp,
            "messages": [],
        }
        chats.append(chat)
        _save_unlocked(chats)
        return dict(chat)


def delete_chat(chat_id: int) -> None:
    with _LOCK:
        chats = _load_unlocked()
        wanted = _normalise_id(chat_id)
        filtered = [chat for chat in chats if int(chat.get("id", 0)) != wanted]
        if len(filtered) == len(chats):
            raise ValueError(f"Чат с ID {wanted} не найден.")
        _save_unlocked(filtered)


def toggle_chat_pin(chat_id: int) -> dict[str, Any]:
    with _LOCK:
        chats = _load_unlocked()
        chat = _find_chat(chats, chat_id)
        chat["pinned"] = not bool(chat.get("pinned"))
        chat["updated_at"] = _now()
        _save_unlocked(chats)
        return dict(chat)


def add_chat_message(
    chat_id: int,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if role not in {"user", "assistant", "system"}:
        raise ValueError("Роль сообщения должна быть user, assistant или system.")
    text = str(content or "").strip()
    if not text:
        raise ValueError("Сообщение не должно быть пустым.")
    if len(text) > 8000:
        raise ValueError("Сообщение слишком длинное (максимум 8000 символов).")

    with _LOCK:
        chats = _load_unlocked()
        chat = _find_chat(chats, chat_id)
        messages = chat.setdefault("messages", [])
        message = {
            "id": max((int(item.get("id", 0)) for item in messages), default=0) + 1,
            "role": role,
            "content": text,
            "created_at": _now(),
        }
        if metadata:
            safe_metadata = {
                key: metadata[key]
                for key in ("status", "intent", "agents_called")
                if key in metadata
            }
            message["metadata"] = safe_metadata

        messages.append(message)
        if role == "user" and chat.get("title") == "Новый чат":
            chat["title"] = text[:48] + ("…" if len(text) > 48 else "")
        chat["updated_at"] = message["created_at"]
        _save_unlocked(chats)
        return dict(message)


def delete_chats_for_schedule(schedule_id: int) -> None:
    """Удаляет все диалоги, относящиеся к удаляемому расписанию."""
    wanted = _normalise_id(schedule_id)
    with _LOCK:
        chats = _load_unlocked()
        filtered = [chat for chat in chats if int(chat.get("schedule_id", 0)) != wanted]
        if len(filtered) != len(chats):
            _save_unlocked(filtered)
