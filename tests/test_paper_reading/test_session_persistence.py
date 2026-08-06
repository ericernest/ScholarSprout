"""论文精读进度自动持久化测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from handlers.paper_reading.harness.session import SessionManager
from handlers.paper_reading.harness.storage import PaperReadingStorage


class SessionPersistenceTests(unittest.TestCase):
    def test_progress_update_is_available_after_manager_restart(self) -> None:
        with TemporaryDirectory() as directory:
            storage = PaperReadingStorage(Path(directory))
            manager = SessionManager(storage)
            session = manager.create_session(paper_id="paper-1")
            manager.set_total_sections(session.session_id, 4)
            manager.update_progress(
                session.session_id,
                section_id="sec:introduction",
                completed=True,
            )

            restored = SessionManager(storage).get_session(session.session_id)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.progress["current_position"]["section_id"], "sec:introduction")
        self.assertEqual(restored.progress["completed_sections"], ["sec:introduction"])
        self.assertEqual(restored.progress["percentage"], 25.0)


if __name__ == "__main__":
    unittest.main()
