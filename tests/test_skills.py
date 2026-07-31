"""测试 Skill 加载、注册、选择和 Runtime 兼容路径。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agents.agent import Agent, AgentProfile
from agents.profiles import Profiles
from runtime.agent_runner import run_agent
from skills.loader import SkillLoadError, load_skill_document, load_skill_metadata
from skills.models import CapabilitySelection
from skills.registry import SkillRegistry, SkillRegistryError
from skills.selector import (
    CapabilitySelectionError,
    CapabilitySelector,
    validate_capability_selection,
)
from tools.base import BaseTool, ToolSpec
from tools.registry import ToolRegistry


# 创建测试用 SKILL.md。
def write_skill(
    root: Path,
    relative_dir: str,
    *,
    skill_id: str,
    category: str,
    instructions: str = "Follow this test workflow.",
) -> Path:
    skill_dir = root / relative_dir
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        (
            "---\n"
            f"id: {skill_id}\n"
            f"name: {skill_id}\n"
            f"category: {category}\n"
            f"description: Description for {skill_id}.\n"
            "when_to_use:\n"
            "  - The task matches this skill.\n"
            "when_not_to_use: []\n"
            "---\n\n"
            f"{instructions}\n"
        ),
        encoding="utf-8",
    )
    return skill_path


# 创建 OpenAI-compatible 测试响应。
def make_response(
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                    "tool_calls": tool_calls or [],
                }
            }
        ]
    }


class FakeModel:
    """记录 Runtime 或 Selector 发出的模型请求。"""

    # 初始化顺序返回的测试响应。
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    # 返回下一条测试响应。
    def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


class NamedTool(BaseTool):
    """提供可区分名称的测试 Tool。"""

    # 创建测试 Tool schema。
    def __init__(self, name: str) -> None:
        self.spec = ToolSpec(
            name=name,
            description=f"Description for {name}.",
            parameters={"type": "object", "properties": {}},
        )
        self.calls: list[dict[str, Any]] = []

    # 记录 Tool 调用参数。
    def run(self, arguments: dict[str, Any]) -> dict[str, str]:
        self.calls.append(arguments)
        return {"result": self.name}


class FakeSelector:
    """返回固定能力选择或固定错误。"""

    # 保存测试选择结果。
    def __init__(
        self,
        selection: CapabilitySelection | None = None,
        error: Exception | None = None,
    ) -> None:
        self.selection = selection or CapabilitySelection()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    # 返回预设结果。
    def select(self, **kwargs: Any) -> CapabilitySelection:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.selection


# 创建包含两个测试 Tool 的 Registry。
def make_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(NamedTool("tool_a"))
    registry.register(NamedTool("tool_b"))
    return registry


# 创建测试 Agent。
def make_agent(
    model: FakeModel,
    *,
    default_skill: str = "",
    skills: list[str],
    tools: list[str],
) -> SimpleNamespace:
    return SimpleNamespace(
        llm=model,
        profile=AgentProfile(
            name="test_agent",
            type="test",
            role="test",
            system_prompt="Base agent prompt.",
            tools=tools,
            default_skill=default_skill,
            skills=skills,
        ),
    )


class SkillLoaderRegistryTests(unittest.TestCase):
    # 验证 Front Matter 与正文可以分别加载。
    def test_parses_skill_metadata_and_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_path = write_skill(
                Path(temp_dir),
                "domain/sample",
                skill_id="domain.sample",
                category="domain",
                instructions="Sample instructions.",
            )

            metadata = load_skill_metadata(skill_path)
            document = load_skill_document(skill_path, source="builtin")

        self.assertEqual(metadata.id, "domain.sample")
        self.assertEqual(metadata.category, "domain")
        self.assertEqual(document.instructions, "Sample instructions.")

    # 验证四类内置目录都会被递归扫描。
    def test_recursively_scans_four_builtin_categories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            builtin_root = root / "builtin"
            user_root = root / "user"
            expected_ids = []
            for category in ("domain", "reading", "chat", "custom"):
                skill_id = f"{category}.sample"
                write_skill(
                    builtin_root,
                    f"{category}/nested/sample",
                    skill_id=skill_id,
                    category=category,
                )
                expected_ids.append(skill_id)

            registry = SkillRegistry(builtin_root=builtin_root, user_root=user_root)

        self.assertEqual(
            [summary.id for summary in registry.list_summaries()],
            expected_ids,
        )

    # 验证用户 Skill 从注入的临时目录加载。
    def test_loads_user_skill_from_temporary_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_skill(
                root / "user",
                "my-skill",
                skill_id="custom.my_skill",
                category="custom",
            )
            registry = SkillRegistry(
                builtin_root=root / "builtin",
                user_root=root / "user",
            )

        self.assertEqual(registry.get_metadata("custom.my_skill").source, "user")

    # 验证非法 category 给出明确加载错误。
    def test_rejects_invalid_category(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_path = write_skill(
                Path(temp_dir),
                "invalid",
                skill_id="invalid.sample",
                category="invalid",
            )

            with self.assertRaisesRegex(SkillLoadError, "Invalid Skill metadata"):
                load_skill_metadata(skill_path)

    # 验证 Skill 元数据不能声明 Tool。
    def test_rejects_tool_field_in_skill_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_path = write_skill(
                Path(temp_dir),
                "chat/invalid-tool",
                skill_id="chat.invalid_tool",
                category="chat",
            )
            content = skill_path.read_text(encoding="utf-8")
            skill_path.write_text(
                content.replace("when_not_to_use: []", "when_not_to_use: []\ntools: []"),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SkillLoadError, "Invalid Skill metadata"):
                load_skill_metadata(skill_path)

    # 验证重复 ID 不会被用户 Skill 静默覆盖。
    def test_rejects_duplicate_skill_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_skill(
                root / "builtin",
                "domain/first",
                skill_id="domain.duplicate",
                category="domain",
            )
            write_skill(
                root / "user",
                "second",
                skill_id="domain.duplicate",
                category="domain",
            )

            with self.assertRaisesRegex(SkillRegistryError, "Duplicate Skill id"):
                SkillRegistry(
                    builtin_root=root / "builtin",
                    user_root=root / "user",
                )

    # 验证精确 ID、通配解析和结果去重。
    def test_resolves_exact_id_and_category_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_skill(
                root / "builtin",
                "domain/a",
                skill_id="domain.a",
                category="domain",
            )
            write_skill(
                root / "builtin",
                "domain/b",
                skill_id="domain.b",
                category="domain",
            )
            registry = SkillRegistry(
                builtin_root=root / "builtin",
                user_root=root / "user",
            )

            resolved = registry.resolve_skill_ids(["domain.b", "domain.*"])

        self.assertEqual(resolved, ["domain.b", "domain.a"])

    # 验证正文在扫描完成后才读取。
    def test_instructions_are_loaded_lazily(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            skill_path = write_skill(
                root / "builtin",
                "domain/lazy",
                skill_id="domain.lazy",
                category="domain",
                instructions="Initial instructions.",
            )
            registry = SkillRegistry(
                builtin_root=root / "builtin",
                user_root=root / "user",
            )
            content = skill_path.read_text(encoding="utf-8")
            skill_path.write_text(
                content.replace("Initial instructions.", "Updated instructions."),
                encoding="utf-8",
            )

            instructions = registry.get_instructions("domain.lazy")

        self.assertEqual(instructions, "Updated instructions.")

    # 验证旧 Profile 缺少 skills 时仍得到默认空列表。
    def test_old_agent_profile_without_skills_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "profiles.json"
            profile_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "legacy",
                            "type": "legacy",
                            "role": "chat",
                            "system_prompt": "Legacy prompt.",
                            "tools": [],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            agent = Agent(
                agent_type="legacy",
                llm=SimpleNamespace(),
                profiles=Profiles(profile_path),
            )

        self.assertEqual(agent.profile.skills, [])
        self.assertEqual(agent.profile.default_skill, "")

    # 验证 chat 与论文精读 Profile 声明的 Skill 都已注册。
    def test_agent_profiles_register_default_and_special_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = SkillRegistry(user_root=Path(temp_dir) / "user")
            chat_profile = Profiles().get("chat")
            chat_skill_ids = registry.resolve_skill_ids(list(chat_profile["skills"]))
            paper_profile = Profiles().get("paper_reading")
            paper_skill_ids = registry.resolve_skill_ids(list(paper_profile["skills"]))

        self.assertEqual(chat_profile["default_skill"], "chat.default")
        self.assertEqual(chat_skill_ids, ["chat.research_discussion"])
        self.assertEqual(paper_profile["default_skill"], "reading.method_analyst")
        self.assertCountEqual(
            paper_skill_ids,
            [
                "reading.method_analyst",
                "reading.critique_agent",
                "reading.math_verifier",
                "reading.code_reviewer",
                "reading.domain_expert",
                "reading.writing_coach",
                "reading.idea_generator",
                "reading.cross_paper_linker",
            ],
        )
        self.assertCountEqual(
            [summary.id for summary in registry.list_summaries()],
            [
                "chat.default",
                "chat.research_discussion",
                "reading.method_analyst",
                "reading.critique_agent",
                "reading.math_verifier",
                "reading.code_reviewer",
                "reading.domain_expert",
                "reading.writing_coach",
                "reading.idea_generator",
                "reading.cross_paper_linker",
                "reading.novice_map_builder",
            ],
        )


class CapabilitySelectorTests(unittest.TestCase):
    # 验证 Selector 可以不选择专项 Skill。
    def test_selector_allows_empty_special_skill(self) -> None:
        model = FakeModel([make_response('{"skill": null, "reason": "none"}')])
        selector = CapabilitySelector()

        selection = selector.select(
            model=model,
            role="chat",
            user_task="hello",
            skill_summaries=[],
        )

        self.assertIsNone(selection.skill)
        selector_payload = json.loads(model.calls[0]["messages"][1]["content"])
        self.assertNotIn("allowed_tools", selector_payload)

    # 验证越权专项 Skill 会被 Runtime 校验拒绝。
    def test_rejects_unauthorized_selection(self) -> None:
        unauthorized_skill = CapabilitySelection(skill="domain.not_allowed")

        with self.assertRaisesRegex(CapabilitySelectionError, "unauthorized Skill"):
            validate_capability_selection(
                unauthorized_skill,
                allowed_skill_ids=["domain.allowed"],
            )


class SkillRuntimeTests(unittest.TestCase):
    # 创建包含一个默认 Skill 和两个专项 Skill 的临时 Registry。
    def make_skill_registry(self, root: Path) -> SkillRegistry:
        write_skill(
            root / "builtin",
            "domain/default",
            skill_id="domain.default",
            category="domain",
            instructions="Default instructions.",
        )
        write_skill(
            root / "builtin",
            "domain/a",
            skill_id="domain.a",
            category="domain",
            instructions="Instructions A.",
        )
        write_skill(
            root / "builtin",
            "domain/b",
            skill_id="domain.b",
            category="domain",
            instructions="Instructions B.",
        )
        return SkillRegistry(
            builtin_root=root / "builtin",
            user_root=root / "user",
        )

    # 验证 Runtime 注入默认与选中专项 Skill，并提供全部授权 Tool。
    def test_runtime_uses_default_and_selected_skill_with_all_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = self.make_skill_registry(Path(temp_dir))
            model = FakeModel([make_response("done")])
            agent = make_agent(
                model,
                default_skill="domain.default",
                skills=["domain.a", "domain.b"],
                tools=["tool_a", "tool_b"],
            )
            selector = FakeSelector(CapabilitySelection(skill="domain.a"))

            result = run_agent(
                agent=agent,
                user_content="test task",
                tool_registry=make_tool_registry(),
                skill_registry=registry,
                capability_selector=selector,
            )

        system_prompt = model.calls[0]["messages"][0]["content"]
        tool_names = [
            item["function"]["name"] for item in model.calls[0]["tools"]
        ]
        self.assertEqual(result, "done")
        self.assertIn("[Agent Role]", system_prompt)
        self.assertIn("Default instructions.", system_prompt)
        self.assertIn("Instructions A.", system_prompt)
        self.assertNotIn("Instructions B.", system_prompt)
        self.assertNotIn("[Default Skill]", system_prompt)
        self.assertNotIn("[Selected Skill]", system_prompt)
        self.assertEqual(tool_names, ["tool_a", "tool_b"])
        self.assertNotIn("tool_summaries", selector.calls[0])

    # 验证未选中专项 Skill 时仍加载默认 Skill 和全部授权 Tool。
    def test_runtime_allows_empty_special_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = self.make_skill_registry(Path(temp_dir))
            model = FakeModel([make_response("done")])
            agent = make_agent(
                model,
                default_skill="domain.default",
                skills=["domain.a", "domain.b"],
                tools=["tool_a"],
            )

            run_agent(
                agent=agent,
                user_content="test task",
                tool_registry=make_tool_registry(),
                skill_registry=registry,
                capability_selector=FakeSelector(CapabilitySelection()),
            )

        system_prompt = model.calls[0]["messages"][0]["content"]
        tool_names = [
            item["function"]["name"] for item in model.calls[0]["tools"]
        ]
        self.assertIn("Default instructions.", system_prompt)
        self.assertNotIn("Instructions A.", system_prompt)
        self.assertNotIn("Instructions B.", system_prompt)
        self.assertEqual(tool_names, ["tool_a"])

    # 验证只有默认 Skill 时不调用 Selector。
    def test_runtime_skips_selector_when_only_default_skill_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = self.make_skill_registry(Path(temp_dir))
            model = FakeModel([make_response("done")])
            agent = make_agent(
                model,
                default_skill="domain.default",
                skills=[],
                tools=["tool_a"],
            )
            selector = FakeSelector(error=AssertionError("selector must not be called"))

            run_agent(
                agent=agent,
                user_content="test task",
                tool_registry=make_tool_registry(),
                skill_registry=registry,
                capability_selector=selector,
            )

        self.assertEqual(selector.calls, [])
        self.assertIn(
            "Default instructions.",
            model.calls[0]["messages"][0]["content"],
        )

    # 验证 Selector 失败后保留默认 Skill 和全部授权 Tool。
    def test_selector_failure_keeps_default_skill_and_all_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = self.make_skill_registry(Path(temp_dir))
            model = FakeModel([make_response("done")])
            agent = make_agent(
                model,
                default_skill="domain.default",
                skills=["domain.a", "domain.b"],
                tools=["tool_a", "tool_b"],
            )
            selector = FakeSelector(error=RuntimeError("selector unavailable"))

            with self.assertLogs("runtime.agent_runner", level="WARNING"):
                run_agent(
                    agent=agent,
                    user_content="test task",
                    tool_registry=make_tool_registry(),
                    skill_registry=registry,
                    capability_selector=selector,
                )

        tool_names = [
            item["function"]["name"] for item in model.calls[0]["tools"]
        ]
        self.assertEqual(tool_names, ["tool_a", "tool_b"])
        self.assertIn(
            "Default instructions.",
            model.calls[0]["messages"][0]["content"],
        )

    # 验证没有注册 Skill 时不调用 Selector，并继续使用 agent.llm。
    def test_legacy_runtime_skips_selector_and_uses_agent_llm(self) -> None:
        model = FakeModel([make_response("legacy")])
        agent = make_agent(model, skills=[], tools=["tool_a"])
        selector = FakeSelector(error=AssertionError("selector must not be called"))

        result = run_agent(
            agent=agent,
            user_content="legacy task",
            tool_registry=make_tool_registry(),
            capability_selector=selector,
        )

        self.assertEqual(result, "legacy")
        self.assertEqual(selector.calls, [])
        self.assertEqual(len(model.calls), 1)

    # 验证主模型可以执行任意 Profile 已授权 Tool。
    def test_runtime_executes_any_profile_allowed_tool(self) -> None:
        tool_call = {
            "id": "call-1",
            "type": "function",
            "function": {"name": "tool_b", "arguments": "{}"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = self.make_skill_registry(Path(temp_dir))
            model = FakeModel(
                [
                    make_response(tool_calls=[tool_call]),
                    make_response("final"),
                ]
            )
            agent = make_agent(
                model,
                default_skill="domain.default",
                skills=["domain.a", "domain.b"],
                tools=["tool_a", "tool_b"],
            )

            result = run_agent(
                agent=agent,
                user_content="test task",
                tool_registry=make_tool_registry(),
                skill_registry=registry,
                capability_selector=FakeSelector(CapabilitySelection()),
            )

        tool_message = model.calls[1]["messages"][-1]
        self.assertEqual(result, "final")
        self.assertIn('"result": "tool_b"', tool_message["content"])

    # 验证 Runtime 仍拒绝 Profile 未授权的 Tool。
    def test_runtime_rejects_profile_unauthorized_tool_call(self) -> None:
        tool_call = {
            "id": "call-1",
            "type": "function",
            "function": {"name": "tool_b", "arguments": "{}"},
        }
        model = FakeModel(
            [
                make_response(tool_calls=[tool_call]),
                make_response("final"),
            ]
        )
        agent = make_agent(model, skills=[], tools=["tool_a"])

        result = run_agent(
            agent=agent,
            user_content="test task",
            tool_registry=make_tool_registry(),
        )

        tool_message = model.calls[1]["messages"][-1]
        self.assertEqual(result, "final")
        self.assertIn("not active", tool_message["content"])


if __name__ == "__main__":
    unittest.main()
