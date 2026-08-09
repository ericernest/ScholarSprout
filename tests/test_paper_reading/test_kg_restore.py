"""论文详情加载时不再向前端恢复图谱快照的回归测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from handlers.paper_reading.handler import _handle_get_paper_detail
from handlers.paper_reading.harness.storage import PaperReadingStorage
from handlers.paper_reading.schemas.request import PaperReadingRequest


class PaperDetailWithoutGraphTests(unittest.TestCase):
    def test_paper_detail_does_not_return_saved_graph_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            storage = PaperReadingStorage(Path(directory))
            storage.save_paper("paper-1", {
                "paper_id": "paper-1",
                "title": "A Paper",
                "authors": [],
                "sections": [{
                    "section_id": "sec:abstract",
                    "title": "Abstract",
                    "level": 1,
                    "content": "Summary",
                    "paragraphs": ["Summary"],
                }],
                "full_text": "",
            })
            storage.save_kg("paper-1", {
                "nodes": [{
                    "node_id": "node-1",
                    "paper_id": "paper-1",
                    "node_type": "Problem",
                    "label": "Memory fragmentation",
                    "properties": {"read_stage": "abstract"},
                }],
                "edges": [],
            })
            state = SimpleNamespace(
                paper_storage=storage,
            )

            response = _handle_get_paper_detail(
                PaperReadingRequest(action="get_paper_detail", paper_id="paper-1"),
                state,
            )

        self.assertEqual(response["status"], "ok")
        self.assertNotIn("initial_kg", response["data"])


if __name__ == "__main__":
    unittest.main()
