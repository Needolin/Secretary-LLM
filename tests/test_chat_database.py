from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Application.Database import chat_database


class ChatDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.chat_file_patch = patch.object(
            chat_database, "CHATS_FILE", Path(self.temp_dir.name) / "chats.json"
        )
        self.chat_file_patch.start()
        self.schedule_patch = patch.object(
            chat_database.database,
            "get_schedule",
            return_value={"id": 7, "name": "Учёба", "period": "Week"},
        )
        self.schedule_patch.start()

    def tearDown(self) -> None:
        self.schedule_patch.stop()
        self.chat_file_patch.stop()
        self.temp_dir.cleanup()

    def test_create_chat_and_auto_title_from_first_message(self) -> None:
        chat = chat_database.create_chat(7)
        self.assertEqual(chat["title"], "Новый чат")
        self.assertEqual(chat["schedule_id"], 7)

        chat_database.add_chat_message(chat["id"], "user", "Подготовь мне день")
        updated = chat_database.get_chat(chat["id"])
        self.assertEqual(updated["title"], "Подготовь мне день")
        self.assertEqual(len(updated["messages"]), 1)

    def test_pin_orders_chat_before_unpinned(self) -> None:
        first = chat_database.create_chat(7, "Первый")
        second = chat_database.create_chat(7, "Второй")
        chat_database.toggle_chat_pin(first["id"])

        chats = chat_database.load_chats(7)
        self.assertEqual(chats[0]["id"], first["id"])
        self.assertTrue(chats[0]["pinned"])
        self.assertFalse(chats[1]["pinned"])

        chat_database.toggle_chat_pin(first["id"])
        self.assertFalse(chat_database.get_chat(first["id"])["pinned"])

    def test_delete_chat_removes_messages(self) -> None:
        chat = chat_database.create_chat(7)
        chat_database.add_chat_message(chat["id"], "user", "Привет")
        chat_database.delete_chat(chat["id"])
        self.assertEqual(chat_database.load_chats(), [])

    def test_reject_empty_message(self) -> None:
        chat = chat_database.create_chat(7)
        with self.assertRaises(ValueError):
            chat_database.add_chat_message(chat["id"], "user", "   ")


if __name__ == "__main__":
    unittest.main()
