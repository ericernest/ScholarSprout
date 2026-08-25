from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from storage.catalog import ResearchCatalog
from storage.local_store import LocalResearchStore
from tools.builtin.research_context_tools import (
    GetDomainOnboardingResultTool,
    GetPaperReadingContextTool,
    SearchPaperReadingDialogueTool,
)


class ResearchContextToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.store = LocalResearchStore(Path(self.directory.name) / "research.sqlite3")
        self.store.initialize()
        self.store.ensure_conversation("main", title="Main")
        self.store.upsert_paper(paper_id="paper-1", title="Paper One", authors=["Ada"])
        self.store.save_reading_session_snapshot("reading-1", {
            "conversation_id": "main",
            "paper_id": "paper-1",
            "paper_title": "Paper One",
            "state": "active",
            "progress": {"current_position": {"section_id": "method"}},
        })

    def tearDown(self) -> None:
        self.directory.cleanup()

    def arguments(self, **values):
        return {
            **values,
            "_runtime_context": {
                "conversation_id": "main",
                "active_context": {
                    "kind": "paper_reading",
                    "id": "reading-1",
                    "title": "Paper One",
                },
            },
        }

    def test_paper_dialogue_is_separate_and_read_only_on_demand(self) -> None:
        dialogue_id = self.store.get_reading_dialogue_conversation_id("reading-1")
        self.store.append_message(
            dialogue_id, role="user", content="方法假设是什么？", mode="paper_reading"
        )
        self.store.append_message(
            dialogue_id, role="assistant", content="核心假设是局部平稳。", mode="paper_reading"
        )

        result = SearchPaperReadingDialogueTool(self.store).run(self.arguments(query="假设"))
        context = GetPaperReadingContextTool(self.store).run(self.arguments())
        main = ResearchCatalog(self.store).get_conversation("main")

        self.assertEqual(len(result["messages"]), 2)
        self.assertEqual(context["paper"]["title"], "Paper One")
        self.assertEqual(main["messages"], [])

    def test_domain_result_requires_link_to_current_conversation(self) -> None:
        artifact_id = self.store.persist_domain_onboarding_result(
            query="图神经网络",
            conversation_id="main",
            response={
                "status": "ok",
                "domain": "图神经网络",
                "text": "领域概览",
                "research_plan": {"goal": "入门"},
                "learning_path": [{"stage": "基础"}],
            },
        )
        result = GetDomainOnboardingResultTool(self.store).run({
            "id": artifact_id,
            "_runtime_context": {"conversation_id": "main", "active_context": {}},
        })
        denied = GetDomainOnboardingResultTool(self.store).run({
            "id": artifact_id,
            "_runtime_context": {"conversation_id": "other", "active_context": {}},
        })

        self.assertEqual(result["query"], "图神经网络")
        self.assertIn("不属于当前主会话", denied["error"])

    def test_legacy_paper_messages_migrate_to_hidden_dialogue(self) -> None:
        message_id = self.store.append_message(
            "main", role="user", content="旧右栏问题", mode="paper_reading"
        )
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE paper_reading_sessions SET dialogue_conversation_id = NULL WHERE reading_session_id = ?",
                ("reading-1",),
            )

        self.store.initialize()
        record = self.store.get_reading_session_record("reading-1")
        with self.store._connection() as connection:
            row = connection.execute(
                "SELECT conversation_id FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()

        self.assertEqual(row["conversation_id"], record["dialogue_conversation_id"])
        self.assertEqual(ResearchCatalog(self.store).get_conversation("main")["messages"], [])


if __name__ == "__main__":
    unittest.main()
