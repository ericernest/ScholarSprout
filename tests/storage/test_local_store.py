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
            child_message_id = store.append_message(
                child, role="user", content="先确认分支问题"
            )
            store.save_memory_snapshot(
                child,
                through_message_id=child_message_id,
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

    def test_v7_cross_conversation_memory_watermark_is_cleared_idempotently(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "research.sqlite3"
            store = LocalResearchStore(database)
            store.initialize()
            parent = store.create_conversation("旧主会话")
            child = store.create_conversation("旧分支", parent_conversation_id=parent)
            parent_message = store.append_message(
                parent, role="user", content="旧库中的父会话消息"
            )
            with store._connection() as connection:
                connection.execute("DELETE FROM schema_versions WHERE version = 8")
                connection.execute(
                    "INSERT OR IGNORE INTO schema_versions(version, applied_at) VALUES (7, ?)",
                    ("2026-01-01T00:00:00+00:00",),
                )
                connection.execute(
                    """INSERT INTO conversation_memory_snapshots(
                           memory_snapshot_id, conversation_id, through_message_id,
                           current_goal, confirmed_decisions_json,
                           open_questions_json, summary, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "legacy-memory",
                        child,
                        parent_message,
                        "保留旧目标",
                        '["保留旧决定"]',
                        '["保留旧问题"]',
                        "保留旧摘要",
                        "2026-01-01T00:00:00+00:00",
                    ),
                )

            store.initialize()
            store.initialize()

            memory = store.get_latest_memory(child)
            self.assertIsNone(memory["through_message_id"])
            self.assertEqual(memory["current_goal"], "保留旧目标")
            self.assertEqual(memory["summary"], "保留旧摘要")
            self.assertEqual(memory["confirmed_decisions"], ["保留旧决定"])
            self.assertEqual(memory["open_questions"], ["保留旧问题"])
            self.assertEqual(
                store.read_conversation_memory_window(child)["recent_messages"], []
            )
            with store._connection() as connection:
                connection.execute(
                    "DELETE FROM messages WHERE message_id = ?", (parent_message,)
                )
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            self.assertEqual(violations, [])

    def test_paper_document_stores_author_names_instead_of_dict_strings(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "research.sqlite3"
            store = LocalResearchStore(database)
            store.initialize()
            store.save_paper_document(
                "paper-authors",
                {
                    "title": "Clean Paper Metadata",
                    "authors": [
                        {"name": "Alice Example", "affiliation": "Example Lab"},
                        "{'name': 'Bob Example', 'affiliation': 'Another Lab'}",
                    ],
                    "abstract": "A useful abstract.",
                },
            )

            with closing(sqlite3.connect(database)) as connection:
                authors_json = connection.execute(
                    "SELECT authors_json FROM papers WHERE paper_id = ?",
                    ("paper-authors",),
                ).fetchone()[0]

            self.assertEqual(json.loads(authors_json), ["Alice Example", "Bob Example"])


if __name__ == "__main__":
    unittest.main()
