"""论文详情加载时恢复 KG 快照的回归测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from handlers.paper_reading.handler import _handle_get_paper_detail
from handlers.paper_reading.harness.storage import PaperReadingStorage
from handlers.paper_reading.kg.builder import ProgressiveKGBuilder
from handlers.paper_reading.kg.engine import KnowledgeGraphEngine
from handlers.paper_reading.schemas.request import PaperReadingRequest


class KnowledgeGraphRestoreTests(unittest.TestCase):
    def test_paper_detail_restores_saved_graph_into_fresh_engine(self) -> None:
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
            engine = KnowledgeGraphEngine()
            state = SimpleNamespace(
                paper_storage=storage,
                kg_engine=engine,
                kg_builder=ProgressiveKGBuilder(engine),
            )

            response = _handle_get_paper_detail(
                PaperReadingRequest(action="get_paper_detail", paper_id="paper-1"),
                state,
            )

        initial_kg = response["data"]["initial_kg"]
        self.assertEqual(initial_kg["node_count"], 1)
        self.assertEqual(initial_kg["cytoscape_elements"][0]["data"]["id"], "node-1")
        self.assertEqual(len(engine.list_nodes_by_paper("paper-1")), 1)


if __name__ == "__main__":
    unittest.main()
