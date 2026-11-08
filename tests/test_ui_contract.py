from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "Application" / "Site" / "Frontend" / "index.html"
STYLES = ROOT / "Application" / "Site" / "Frontend" / "styles.css"


class UIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = TEMPLATE.read_text(encoding="utf-8")
        cls.css = STYLES.read_text(encoding="utf-8")

    def test_schedule_period_picker_only_appears_in_creation_dialog(self) -> None:
        self.assertIn('id="schedule-dialog"', self.html)
        self.assertIn('name="period"', self.html)
        self.assertIn("фиксированный формат", self.html)
        self.assertNotIn("data-period-switch", self.html)
        self.assertNotIn("view-switch", self.html)

    def test_schedule_context_menu_supports_pin_and_delete(self) -> None:
        self.assertIn('id="context-pin"', self.html)
        self.assertIn('id="context-delete"', self.html)
        self.assertIn('addEventListener("contextmenu"', self.html)
        self.assertIn("/api/schedules/${contextScheduleId}/pin", self.html)
        self.assertIn("/api/schedules/${contextScheduleId}", self.html)

    def test_chat_is_a_separate_right_dock_and_recent_chats_section_is_absent(self) -> None:
        self.assertIn('class="chat-dock"', self.html)
        self.assertIn("grid-template-columns: minmax(0, 1fr) 360px", self.css)
        self.assertNotIn("Недавние чаты", self.html)
        self.assertNotIn("assistant-mark", self.html)

    def test_only_event_and_deadline_creation_controls_are_visible(self) -> None:
        self.assertIn('id="event-dialog"', self.html)
        self.assertIn('id="deadline-dialog"', self.html)
        self.assertIn('name="color"', self.html)
        self.assertIn("deadline-cross", self.html)
        self.assertIn("neon-line-preview", self.html)

    def test_schedule_creation_button_is_above_the_schedule_list(self) -> None:
        button_pos = self.html.index('class="new-schedule-button"')
        list_pos = self.html.index('class="schedule-list"')
        self.assertLess(button_pos, list_pos)


if __name__ == "__main__":
    unittest.main()
