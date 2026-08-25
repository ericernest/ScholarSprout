from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from handlers.paper_reading.harness.fork_merge import ForkMergeManager
from handlers.paper_reading.harness.session import SessionManager
from memory.service import ConversationMemoryService
from runtime.agent_runner import run_agent_detailed
from storage.local_store import LocalResearchStore


class FakeModel:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("compression unavailable")
        visible = json.loads(kwargs["messages"][1]["content"].split("Historical data:\n", 1)[1])
        contents = [item["content"] for item in visible["visible_messages"]]
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "current_goal": "测试滚动记忆",
                                "summary": " | ".join(contents),
                                "confirmed_decisions": ["保留最近 8 条"],
                                "open_questions": [],
                                "facts_to_add": [],
                                "fact_ids_to_supersede": [],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }


class FactModel(FakeModel):
    def chat(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(
            kwargs["messages"][1]["content"].split("Historical data:\n", 1)[1]
        )
        facts = []
        for message in payload["visible_messages"]:
            if message["role"] == "user" and "烧烤" in message["content"]:
                facts.append({
                    "text": "用户在烧烤聚餐中支付了130元",
                    "source_message_ids": [message["message_id"]],
                })
        return {"choices": [{"message": {"content": json.dumps({
            "current_goal": "继续对话",
            "summary": "",
            "confirmed_decisions": [],
            "open_questions": [],
            "facts_to_add": facts,
            "fact_ids_to_supersede": [],
        }, ensure_ascii=False)}}]}


class AnswerModel:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}


class EmptyTools:
    def to_openai_tools(self, _names):
        return []


class FlakySessionStorage:
    def __init__(self) -> None:
        self.saved: dict[str, dict] = {}
        self.fail_once_session_id: str | None = None
        self.failed = False

    def save_session(self, session_id: str, data: dict) -> None:
        if session_id == self.fail_once_session_id and not self.failed:
            self.failed = True
            raise OSError("session storage unavailable")
        self.saved[session_id] = copy.deepcopy(data)

    def load_session(self, session_id: str):
        data = self.saved.get(session_id)
        return copy.deepcopy(data) if data is not None else None


class FailOnceLinkResearchStore:
    def __init__(self, store: LocalResearchStore) -> None:
        self.store = store
        self.failed = False

    def __getattr__(self, name: str):
        return getattr(self.store, name)

    def link_fork_memory(self, *args, **kwargs):
        if not self.failed:
            self.failed = True
            raise OSError("link unavailable")
        return self.store.link_fork_memory(*args, **kwargs)


class ConversationMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.store = LocalResearchStore(Path(self.directory.name) / "research.sqlite3")
        self.store.initialize()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def conversation(self, name: str = "conversation") -> str:
        self.store.ensure_conversation(name, title=name)
        return name

    def append(self, conversation_id: str, count: int) -> list[str]:
        return [
            self.store.append_message(
                conversation_id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message-{index + 1}",
            )
            for index in range(count)
        ]

    def test_eight_messages_need_no_compression_and_nine_compacts_one(self) -> None:
        conversation_id = self.conversation()
        ids = self.append(conversation_id, 8)
        model = FakeModel()
        service = ConversationMemoryService(self.store, model)

        context = service.prepare_context(conversation_id)
        self.assertEqual(len(context.context_messages), 8)
        self.assertEqual(model.calls, [])
        self.assertIsNone(self.store.get_latest_memory(conversation_id))

        ids.extend(self.append(conversation_id, 1))
        context = service.prepare_context(conversation_id)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(len(context.context_messages), 8)
        self.assertEqual(
            self.store.get_latest_memory(conversation_id)["through_message_id"], ids[0]
        )
        with self.store._connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)
                ).fetchone()[0],
                9,
            )

    def test_atomic_window_never_returns_more_than_recent_limit(self) -> None:
        conversation_id = self.conversation()
        self.append(conversation_id, 20)

        window = self.store.read_conversation_memory_window(
            conversation_id, recent_limit=8
        )

        self.assertEqual(len(window["messages_to_compress"]), 12)
        self.assertEqual(len(window["recent_messages"]), 8)
        self.assertEqual(
            [item["sequence_number"] for item in window["recent_messages"]],
            list(range(13, 21)),
        )

    def test_incremental_watermark_only_compacts_new_overflow(self) -> None:
        conversation_id = self.conversation()
        self.append(conversation_id, 9)
        model = FakeModel()
        service = ConversationMemoryService(self.store, model)
        service.prepare_context(conversation_id)
        self.append(conversation_id, 2)
        service.prepare_context(conversation_id)

        second_payload = json.loads(
            model.calls[1]["messages"][1]["content"].split("Historical data:\n", 1)[1]
        )
        self.assertEqual(
            [item["content"] for item in second_payload["visible_messages"]],
            ["message-2", "message-3"],
        )

    def test_incremental_summary_merges_old_and_new_content(self) -> None:
        conversation_id = self.conversation()
        self.append(conversation_id, 9)
        model = FakeModel()
        service = ConversationMemoryService(self.store, model)
        service.prepare_context(conversation_id)
        self.append(conversation_id, 2)
        service.prepare_context(conversation_id)

        summary = self.store.get_latest_memory(conversation_id)["summary"]
        self.assertIn("message-1", summary)
        self.assertIn("message-2", summary)
        self.assertIn("message-3", summary)

    def test_bbq_fact_survives_compression_with_user_message_provenance(self) -> None:
        conversation_id = self.conversation("bbq")
        source_id = self.store.append_message(
            conversation_id, role="user", content="烧烤那次是我付的130元"
        )
        for index in range(8):
            self.store.append_message(
                conversation_id,
                role="assistant" if index % 2 == 0 else "user",
                content=f"后续闲聊-{index}",
            )
        service = ConversationMemoryService(self.store, FactModel())

        context = service.prepare_context(conversation_id)
        facts = self.store.list_active_memory_facts(conversation_id)

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["source_message_ids"], [source_id])
        self.assertIn("烧烤聚餐中支付了130元", context.memory_text)

    def test_v1_snapshot_is_retained_for_audit_but_ignored_for_v2_rebuild(self) -> None:
        conversation_id = self.conversation("legacy")
        source_id = self.store.append_message(
            conversation_id, role="user", content="烧烤那次是我付的130元"
        )
        for index in range(8):
            self.store.append_message(
                conversation_id, role="assistant", content=f"旧对话-{index}"
            )
        with self.store._connection() as connection:
            connection.execute(
                """INSERT INTO conversation_memory_snapshots(
                       memory_snapshot_id, conversation_id, through_message_id,
                       schema_version, summary, created_at)
                   VALUES ('legacy-memory', ?, ?, 'conversation-memory-v1', '错误旧摘要', '2025-01-01')""",
                (conversation_id, source_id),
            )

        context = ConversationMemoryService(self.store, FactModel()).prepare_context(
            conversation_id
        )
        with self.store._connection() as connection:
            versions = [row[0] for row in connection.execute(
                "SELECT schema_version FROM conversation_memory_snapshots WHERE conversation_id = ? ORDER BY created_at",
                (conversation_id,),
            ).fetchall()]

        self.assertEqual(versions, ["conversation-memory-v1", "conversation-memory-v2"])
        self.assertIn("烧烤聚餐中支付了130元", context.memory_text)

    def test_failure_does_not_advance_watermark_and_keeps_full_fallback(self) -> None:
        conversation_id = self.conversation()
        self.append(conversation_id, 9)
        service = ConversationMemoryService(self.store, FakeModel(fail=True))

        context = service.prepare_context(conversation_id)
        self.assertTrue(context.compression_failed)
        self.assertEqual(len(context.context_messages), 9)
        self.assertIsNone(self.store.get_latest_memory(conversation_id))

    def test_long_term_memory_redacts_secrets_from_every_field(self) -> None:
        memory = ConversationMemoryService._sanitize(
            {
                "current_goal": "调用 Bearer abcdefghijklmnop 完成验证",
                "summary": '配置为 {"api_key": "secret-summary-value"}',
                "confirmed_decisions": ["password=hunter2-value"],
                "open_questions": ["是否继续使用 sk-abcdefghijklmnop"],
            }
        )

        serialized = json.dumps(memory, ensure_ascii=False)
        self.assertNotIn("abcdefghijklmnop", serialized)
        self.assertNotIn("secret-summary-value", serialized)
        self.assertNotIn("hunter2-value", serialized)
        self.assertGreaterEqual(serialized.count("[已隐藏敏感信息]"), 4)

    def test_legacy_snapshot_secret_is_redacted_before_prompt_rendering(self) -> None:
        conversation_id = self.conversation()
        message_id = self.store.append_message(
            conversation_id, role="user", content="普通历史消息"
        )
        self.store.save_memory_snapshot(
            conversation_id,
            through_message_id=message_id,
            current_goal="继续已有任务",
            summary="旧快照包含 sk-legacysecret123456",
            confirmed_decisions=["api_key=legacy-key-value"],
            open_questions=["password: legacy-password-value"],
        )
        model = FakeModel()

        context = ConversationMemoryService(self.store, model).prepare_context(
            conversation_id
        )

        self.assertEqual(model.calls, [])
        self.assertNotIn("sk-legacysecret123456", context.memory_text)
        self.assertNotIn("legacy-key-value", context.memory_text)
        self.assertNotIn("legacy-password-value", context.memory_text)
        self.assertIn("[已隐藏敏感信息]", context.memory_text)

    def test_memory_is_conversation_scoped(self) -> None:
        first = self.conversation("first")
        second = self.conversation("second")
        self.append(first, 9)
        self.append(second, 8)
        model = FakeModel()
        service = ConversationMemoryService(self.store, model)
        service.prepare_context(first)

        self.assertIsNotNone(self.store.get_latest_memory(first))
        self.assertIsNone(self.store.get_latest_memory(second))

    def test_current_user_is_not_repeated_in_agent_messages(self) -> None:
        conversation_id = self.conversation()
        ids = self.append(conversation_id, 3)
        context = ConversationMemoryService(self.store, FakeModel()).prepare_context(
            conversation_id, exclude_message_id=ids[-1]
        )
        model = AnswerModel()
        profile = SimpleNamespace(
            system_prompt="system", tools=[], default_skill="", skills=[], role="assistant"
        )
        agent = SimpleNamespace(llm=model, profile=profile)
        run_agent_detailed(
            agent,
            "message-3",
            EmptyTools(),
            memory_text=context.memory_text,
            context_messages=context.context_messages,
        )

        messages = model.calls[0]["messages"]
        self.assertEqual([item["content"] for item in messages].count("message-3"), 1)
        self.assertEqual(messages[-1], {"role": "user", "content": "message-3"})

    def test_persisted_tool_history_is_replayed_without_bare_tool_role(self) -> None:
        conversation_id = self.conversation()
        tool_content = "检索到 3 篇论文。\n第二篇与当前问题最相关。"
        self.store.append_message(
            conversation_id, role="tool", content=tool_content
        )
        context = ConversationMemoryService(self.store, FakeModel()).prepare_context(
            conversation_id
        )
        model = AnswerModel()
        profile = SimpleNamespace(
            system_prompt="system", tools=[], default_skill="", skills=[], role="assistant"
        )

        run_agent_detailed(
            SimpleNamespace(llm=model, profile=profile),
            "继续分析",
            EmptyTools(),
            context_messages=context.context_messages,
        )

        messages = model.calls[0]["messages"]
        self.assertNotIn("tool", [message["role"] for message in messages])
        self.assertIn(
            {"role": "assistant", "content": f"[工具结果]\n{tool_content}"},
            messages,
        )

    def test_concurrent_append_does_not_drop_messages(self) -> None:
        conversation_id = self.conversation()

        def write(index: int) -> str:
            return self.store.append_message(
                conversation_id, role="user", content=f"concurrent-{index}"
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            ids = list(executor.map(write, range(30)))
        with self.store._connection() as connection:
            rows = connection.execute(
                "SELECT message_id, sequence_number FROM messages WHERE conversation_id = ? ORDER BY sequence_number",
                (conversation_id,),
            ).fetchall()
        self.assertEqual(len(rows), 30)
        self.assertEqual(len(set(ids)), 30)
        self.assertEqual([row["sequence_number"] for row in rows], list(range(1, 31)))

    def test_fork_finalize_link_and_repeat_merge_are_idempotent(self) -> None:
        manager = SessionManager()
        parent = manager.create_session(session_id="parent", paper_id="paper")
        fork = manager.create_session(
            session_id="fork", paper_id="paper", parent_session_id=parent.session_id,
            fork_context="公式解释",
        )
        parent.fork_sessions.append(fork.session_id)
        fork.active_skills = ["math_verifier"]
        self.store.ensure_conversation(parent.session_id, title="parent")
        self.store.ensure_conversation(
            fork.session_id, title="fork", parent_conversation_id=parent.session_id
        )
        self.store.append_message(fork.session_id, role="user", content="为什么这样推导？")
        self.store.append_message(fork.session_id, role="assistant", content="因为使用了链式法则。")
        model = FakeModel()
        service = ConversationMemoryService(self.store, model)
        merge_manager = ForkMergeManager(
            manager, memory_service=service, research_store=self.store
        )

        result = merge_manager.merge_fork(
            fork.session_id, expected_parent_session_id=parent.session_id
        )
        repeated = merge_manager.merge_fork(
            fork.session_id, expected_parent_session_id=parent.session_id
        )

        self.assertTrue(result.success)
        self.assertTrue(repeated.success)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(fork.state, "completed")
        self.assertIn("math_verifier", parent.active_skills)
        self.assertIsNotNone(self.store.get_fork_memory_link(parent.session_id, fork.session_id))
        parent_context = service.prepare_context(parent.session_id)
        self.assertIn("[Merged Fork 1: fork]", parent_context.memory_text)
        self.assertIn("公式解释", result.key_findings[0])

    def test_finalize_twice_reuses_sanitized_snapshot_metadata(self) -> None:
        fork_id = self.conversation("fork")
        self.store.append_message(fork_id, role="user", content="需要最终结论")
        model = FakeModel()
        service = ConversationMemoryService(self.store, model)

        first = service.finalize_conversation(fork_id)
        second = service.finalize_conversation(fork_id)

        self.assertEqual(len(model.calls), 1)
        self.assertEqual(second["memory_snapshot_id"], first["memory_snapshot_id"])
        self.assertEqual(second["conversation_id"], fork_id)
        self.assertEqual(second["through_message_id"], first["through_message_id"])

    def test_fork_link_failure_retries_with_existing_snapshot(self) -> None:
        manager = SessionManager()
        parent = manager.create_session(session_id="parent", paper_id="paper")
        fork = manager.create_session(
            session_id="fork", paper_id="paper", parent_session_id=parent.session_id
        )
        parent.fork_sessions.append(fork.session_id)
        fork.active_skills = ["math_verifier"]
        self.store.ensure_conversation(parent.session_id, title="parent")
        self.store.ensure_conversation(
            fork.session_id, title="fork", parent_conversation_id=parent.session_id
        )
        self.store.append_message(fork.session_id, role="user", content="分析公式")
        model = FakeModel()
        service = ConversationMemoryService(self.store, model)
        flaky_store = FailOnceLinkResearchStore(self.store)
        merge_manager = ForkMergeManager(
            manager, memory_service=service, research_store=flaky_store
        )

        first = merge_manager.merge_fork(
            fork.session_id, expected_parent_session_id=parent.session_id
        )
        second = merge_manager.merge_fork(
            fork.session_id, expected_parent_session_id=parent.session_id
        )

        self.assertFalse(first.success)
        self.assertTrue(second.success)
        self.assertEqual(len(model.calls), 1)
        self.assertIsNotNone(
            self.store.get_fork_memory_link(parent.session_id, fork.session_id)
        )
        self.assertEqual(fork.state, "completed")
        self.assertIn("math_verifier", parent.active_skills)

    def test_linked_fork_snapshot_is_frozen_while_child_memory_keeps_rolling(self) -> None:
        parent_id = self.conversation("parent")
        fork_id = "fork"
        self.store.ensure_conversation(
            fork_id, title="fork", parent_conversation_id=parent_id
        )
        self.store.append_message(fork_id, role="user", content="合并时问题")
        self.store.append_message(fork_id, role="assistant", content="合并时结论")
        model = FakeModel()
        service = ConversationMemoryService(self.store, model)
        merged_memory = service.finalize_conversation(fork_id)
        self.store.link_fork_memory(
            parent_id, fork_id, merged_memory["memory_snapshot_id"]
        )

        self.append(fork_id, 9)
        child_context = service.prepare_context(fork_id)

        self.assertFalse(child_context.compression_failed)
        latest = self.store.get_latest_memory(fork_id)
        self.assertNotEqual(
            latest["memory_snapshot_id"], merged_memory["memory_snapshot_id"]
        )
        frozen = self.store.get_fork_memory_link(parent_id, fork_id)
        self.assertEqual(
            frozen["memory_snapshot_id"], merged_memory["memory_snapshot_id"]
        )
        self.assertEqual(frozen["summary"], merged_memory["summary"])
        parent_context = service.prepare_context(parent_id)
        self.assertIn(merged_memory["summary"], parent_context.memory_text)
        with self.store._connection() as connection:
            snapshot_count = connection.execute(
                "SELECT COUNT(*) FROM conversation_memory_snapshots WHERE conversation_id = ?",
                (fork_id,),
            ).fetchone()[0]
        self.assertEqual(snapshot_count, 2)

    def test_fork_merge_persistence_failure_retries_without_recompression(self) -> None:
        session_storage = FlakySessionStorage()
        manager = SessionManager(storage=session_storage)
        parent = manager.create_session(session_id="parent", paper_id="paper")
        fork = manager.create_session(
            session_id="fork",
            paper_id="paper",
            parent_session_id=parent.session_id,
            fork_context="验证持久化重试",
        )
        parent.fork_sessions.append(fork.session_id)
        fork.active_skills = ["math_verifier"]
        manager._persist(parent)
        manager._persist(fork)
        self.store.ensure_conversation(parent.session_id, title="parent")
        self.store.ensure_conversation(
            fork.session_id,
            title="fork",
            parent_conversation_id=parent.session_id,
        )
        self.store.append_message(fork.session_id, role="user", content="分析公式")
        self.store.append_message(fork.session_id, role="assistant", content="公式结论")
        model = FakeModel()
        merge_manager = ForkMergeManager(
            manager,
            memory_service=ConversationMemoryService(self.store, model),
            research_store=self.store,
        )
        session_storage.fail_once_session_id = parent.session_id

        first = merge_manager.merge_fork(
            fork.session_id, expected_parent_session_id=parent.session_id
        )

        self.assertFalse(first.success)
        self.assertEqual(fork.state, "active")
        self.assertEqual(parent.active_skills, [])
        self.assertEqual(session_storage.saved[fork.session_id]["state"], "completed")
        self.assertIsNotNone(
            self.store.get_fork_memory_link(parent.session_id, fork.session_id)
        )

        second = merge_manager.merge_fork(
            fork.session_id, expected_parent_session_id=parent.session_id
        )

        self.assertTrue(second.success)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(fork.state, "completed")
        self.assertIn("math_verifier", parent.active_skills)
        self.assertEqual(session_storage.saved[fork.session_id]["state"], "completed")
        self.assertIn(
            "math_verifier", session_storage.saved[parent.session_id]["active_skills"]
        )

    def test_empty_fork_merge_keeps_original_merge_operations(self) -> None:
        manager = SessionManager()
        parent = manager.create_session(session_id="parent", paper_id="paper")
        fork = manager.create_session(
            session_id="fork",
            paper_id="paper",
            parent_session_id=parent.session_id,
            fork_context="只合并分支上下文",
        )
        parent.fork_sessions.append(fork.session_id)
        fork.active_skills = ["method_analyst"]
        self.store.ensure_conversation(parent.session_id, title="parent")
        self.store.ensure_conversation(
            fork.session_id,
            title="fork",
            parent_conversation_id=parent.session_id,
        )
        model = FakeModel()
        merge_manager = ForkMergeManager(
            manager,
            memory_service=ConversationMemoryService(self.store, model),
            research_store=self.store,
        )

        result = merge_manager.merge_fork(
            fork.session_id, expected_parent_session_id=parent.session_id
        )

        self.assertTrue(result.success)
        self.assertEqual(model.calls, [])
        self.assertEqual(fork.state, "completed")
        self.assertIn("method_analyst", parent.active_skills)
        self.assertIn("只合并分支上下文", result.key_findings[0])
        self.assertIsNone(
            self.store.get_fork_memory_link(parent.session_id, fork.session_id)
        )

    def test_fork_parent_mismatch_has_no_side_effect(self) -> None:
        manager = SessionManager()
        parent = manager.create_session(session_id="parent", paper_id="paper")
        fork = manager.create_session(
            session_id="fork", paper_id="paper", parent_session_id=parent.session_id
        )
        fork.active_skills = ["math_verifier"]
        model = FakeModel()
        service = ConversationMemoryService(self.store, model)
        merge_manager = ForkMergeManager(
            manager, memory_service=service, research_store=self.store
        )

        result = merge_manager.merge_fork(
            fork.session_id, expected_parent_session_id="another-parent"
        )
        self.assertFalse(result.success)
        self.assertEqual(model.calls, [])
        self.assertEqual(fork.state, "active")
        self.assertEqual(parent.active_skills, [])


if __name__ == "__main__":
    unittest.main()
