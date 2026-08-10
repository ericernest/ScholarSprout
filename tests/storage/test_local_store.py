from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from storage import LocalResearchStore


class LocalResearchStoreTests(unittest.TestCase):
    def test_schema_covers_papers_modes_conversations_and_memory(self) -> None:
        with TemporaryDirectory() as directory:
            store = LocalResearchStore(Path(directory) / "research.sqlite3")
            store.initialize()

            self.assertTrue(
                {
                    "papers",
                    "library_items",
                    "paper_notes",
                    "work_artifacts",
                    "domain_onboardings",
                    "domain_recommendations",
                    "paper_reading_sessions",
                    "conversations",
                    "messages",
                    "conversation_memory_snapshots",
                }.issubset(store.list_table_names())
            )

    def test_recommendation_can_be_read_before_it_is_added_to_library(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "research.sqlite3"
            store = LocalResearchStore(database)
            store.initialize()
            conversation_id = store.create_conversation("量子控制讨论")
            paper_id = store.upsert_paper(
                title="Reliable Quantum Control",
                authors=["Ada Lovelace"],
                doi="10.1000/example",
                source_url="https://example.test/paper",
            )
            onboarding_id = store.create_domain_onboarding(
                title="量子控制领域入门",
                query="量子控制入门",
                conversation_id=conversation_id,
            )
            store.recommend_paper(
                onboarding_id,
                paper_id,
                recommendation_rank=1,
                paper_role="foundational",
                reading_priority="core",
                is_canonical=True,
                reason="解释控制误差的基本模型。",
                reading_focus=["误差模型"],
            )
            reading_id = store.create_reading_session(
                title="Reliable Quantum Control 精读",
                paper_id=paper_id,
                conversation_id=conversation_id,
            )
            store.save_reading_state(
                reading_id,
                state="paused",
                current_section_id="sec:method",
                current_paragraph_index=2,
                total_sections=5,
                active_skills=["method_analyst"],
                completed_sections=["sec:introduction"],
                section_statuses={"sec:introduction": "completed", "sec:method": "reading"},
            )
            store.save_reading_block(
                reading_id,
                block_type="method_analysis",
                content_schema_version="paper-reading-block-v1",
                content={"method": "robust control"},
                rendered_text="方法：鲁棒控制",
            )

            with closing(sqlite3.connect(database)) as connection:
                library_count = connection.execute("SELECT COUNT(*) FROM library_items").fetchone()[0]
                state = connection.execute(
                    "SELECT state, current_section_id, active_skills_json FROM paper_reading_sessions WHERE reading_session_id = ?",
                    (reading_id,),
                ).fetchone()
                block = connection.execute(
                    "SELECT content_json FROM paper_reading_blocks WHERE reading_session_id = ?",
                    (reading_id,),
                ).fetchone()

            self.assertEqual(library_count, 0)
            self.assertEqual(state[0], "paused")
            self.assertEqual(state[1], "sec:method")
            self.assertEqual(json.loads(state[2]), ["method_analyst"])
            self.assertEqual(json.loads(block[0])["method"], "robust control")

    def test_conversation_memory_is_scoped_to_one_conversation(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "research.sqlite3"
            store = LocalResearchStore(database)
            store.initialize()
            parent = store.create_conversation("主会话")
            message_id = store.append_message(parent, role="user", content="先理解方法部分")
            child = store.create_conversation(
                "公式推导分支",
                parent_conversation_id=parent,
                forked_from_message_id=message_id,
            )
            store.save_memory_snapshot(
                child,
                through_message_id=message_id,
                current_goal="推导公式",
                confirmed_decisions=["先检查符号定义"],
                open_questions=["损失函数如何构造？"],
                summary="该分支只讨论方法中的公式。",
            )

            with closing(sqlite3.connect(database)) as connection:
                parent_count = connection.execute(
                    "SELECT COUNT(*) FROM conversation_memory_snapshots WHERE conversation_id = ?", (parent,)
                ).fetchone()[0]
                child_count = connection.execute(
                    "SELECT COUNT(*) FROM conversation_memory_snapshots WHERE conversation_id = ?", (child,)
                ).fetchone()[0]

            self.assertEqual(parent_count, 0)
            self.assertEqual(child_count, 1)


if __name__ == "__main__":
    unittest.main()
