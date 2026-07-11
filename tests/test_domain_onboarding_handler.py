"""Tests for the domain onboarding handler and its no-tool agent flow."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from typing import Any

from channels.base import ChannelMessage
from handlers.domain_onboarding_handler import (
    handle_domain_onboarding_message,
    parse_json_object,
)
from handlers.domain_onboarding_metrics import DomainOnboardingMetrics
from tools.registry import ToolRegistry


class FakeModel:
    def __init__(
        self,
        content: str | list[str | Exception] = "",
        error: Exception | None = None,
    ):
        self.responses = content if isinstance(content, list) else [content]
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return {
            "choices": [
                {
                    "message": {
                        "content": response,
                        "tool_calls": [],
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
            },
        }


def make_message(content: Any) -> ChannelMessage:
    return ChannelMessage(
        session_id="test-session",
        channel="test",
        direction="inbound",
        mode="domain_onboarding",
        content=content,
    )


def make_app_state(
    model: FakeModel,
    metrics: DomainOnboardingMetrics | None = None,
) -> SimpleNamespace:
    agent = SimpleNamespace(
        profile=SimpleNamespace(
            system_prompt="Return a JSON object.",
            tools=[],
        )
    )
    return SimpleNamespace(
        domain_onboarding_agent=agent,
        model=model,
        tool_registry=ToolRegistry(),
        domain_onboarding_metrics=metrics or DomainOnboardingMetrics(),
    )


def make_complete_payload(domain: str = "多模态大模型") -> dict[str, Any]:
    return {
        "domain": domain,
        "text": (
            f"{domain}研究如何建立可靠的领域表示、推理与应用体系，"
            "入门时应依次掌握基础理论、核心方法、代表论文和开放问题。"
        ),
        "prerequisites": [
            {
                "name": f"前置知识 {index}",
                "why_needed": "用于理解模型原理和实验方法。",
                "key_points": ["核心概念", "基础方法"],
            }
            for index in range(1, 4)
        ],
        "development_stages": [
            {
                "name": f"发展阶段 {index}",
                "summary": "该阶段形成了具有代表性的研究范式。",
                "motivation": "解决上一阶段在能力和适用范围上的限制。",
                "representative_papers": [
                    {
                        "title": f"代表论文 {index}",
                        "authors": [],
                        "year": 2020 + index,
                        "contribution": "提出该阶段的代表性方法。",
                    }
                ],
                "core_concepts": ["核心概念"],
                "main_techniques": ["关键技术"],
                "open_problems": ["开放问题"],
            }
            for index in range(1, 4)
        ],
        "current_landscape": {
            "problems": ["问题一", "问题二", "问题三"],
            "subdirections": ["方向一", "方向二", "方向三"],
        },
        "learning_path": [
            {
                "step": f"学习阶段 {index}",
                "goal": "建立本阶段的知识框架。",
                "topics": ["主题一", "主题二"],
                "papers": [
                    {
                        "title": f"学习论文 {index}",
                        "authors": [],
                        "year": 2020 + index,
                        "contribution": "帮助理解本阶段核心内容。",
                    }
                ],
                "expected_outcome": "能够解释核心概念并复现基础方法。",
            }
            for index in range(1, 4)
        ],
    }


class ParseJsonObjectTests(unittest.TestCase):
    def test_parses_plain_json_object(self) -> None:
        self.assertEqual(parse_json_object('{"domain": "多模态"}'), {"domain": "多模态"})

    def test_extracts_json_from_markdown_code_block(self) -> None:
        raw_text = '```json\n{"domain": "多模态", "learning_path": []}\n```'

        self.assertEqual(
            parse_json_object(raw_text),
            {"domain": "多模态", "learning_path": []},
        )

    def test_extracts_json_from_surrounding_text(self) -> None:
        raw_text = '以下是结果：\n{"domain": "强化学习"}\n请按此路径学习。'

        self.assertEqual(parse_json_object(raw_text), {"domain": "强化学习"})

    def test_skips_invalid_braces_before_json_object(self) -> None:
        raw_text = '说明中的 {占位符} 不是 JSON，结果是 {"domain": "计算机视觉"}。'

        self.assertEqual(parse_json_object(raw_text), {"domain": "计算机视觉"})

    def test_rejects_non_object_or_malformed_content(self) -> None:
        self.assertIsNone(parse_json_object('["domain", "多模态"]'))
        self.assertIsNone(parse_json_object('{"domain": }'))
        self.assertIsNone(parse_json_object(""))


class DomainOnboardingHandlerTests(unittest.TestCase):
    def test_returns_normalized_result_from_fake_model(self) -> None:
        model = FakeModel(
            content=(
                '{"domain":"多模态大模型","text":"入门方案",'
                '"prerequisites":"线性代数","development_stages":[], '
                '"current_landscape":{"problems":"幻觉"},'
                '"learning_path":["基础","论文"]}'
            )
        )

        result = handle_domain_onboarding_message(
            make_message(" 我想入门多模态大模型 "),
            make_app_state(model),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["query"], "我想入门多模态大模型")
        self.assertEqual(result["domain"], "多模态大模型")
        self.assertEqual(
            result["prerequisites"],
            [
                {
                    "name": "线性代数",
                    "why_needed": "",
                    "key_points": [],
                }
            ],
        )
        self.assertEqual(result["current_landscape"]["problems"], ["幻觉"])
        self.assertEqual(result["current_landscape"]["subdirections"], [])
        self.assertEqual(
            result["learning_path"],
            [
                {
                    "step": "基础",
                    "goal": "",
                    "topics": [],
                    "papers": [],
                    "expected_outcome": "",
                },
                {
                    "step": "论文",
                    "goal": "",
                    "topics": [],
                    "papers": [],
                    "expected_outcome": "",
                },
            ],
        )

    def test_repairs_common_deep_structure_variations(self) -> None:
        model = FakeModel(
            content=json.dumps(
                {
                    "domain": "多模态大模型",
                    "prerequisites": {
                        "name": "机器学习",
                        "key_points": "反向传播",
                    },
                    "development_stages": {
                        "name": "视觉语言预训练",
                        "representative_papers": [
                            "CLIP",
                            {
                                "name": "BLIP",
                                "authors": "Li",
                                "year": "2022年",
                                "summary": "统一视觉语言理解与生成",
                            },
                        ],
                        "core_concepts": "跨模态对齐",
                    },
                    "current_landscape": {
                        "problems": "多模态幻觉",
                        "subdirections": None,
                    },
                    "learning_path": {
                        "step": 1,
                        "papers": "CLIP",
                    },
                },
                ensure_ascii=False,
            )
        )

        result = handle_domain_onboarding_message(
            make_message("多模态大模型"),
            make_app_state(model),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["prerequisites"][0]["key_points"], ["反向传播"])
        stage = result["development_stages"][0]
        self.assertEqual(stage["core_concepts"], ["跨模态对齐"])
        self.assertEqual(stage["representative_papers"][0]["title"], "CLIP")
        self.assertEqual(stage["representative_papers"][1]["title"], "BLIP")
        self.assertEqual(stage["representative_papers"][1]["authors"], ["Li"])
        self.assertEqual(stage["representative_papers"][1]["year"], 2022)
        self.assertEqual(
            stage["representative_papers"][1]["contribution"],
            "统一视觉语言理解与生成",
        )
        self.assertEqual(result["learning_path"][0]["step"], "1")
        self.assertEqual(result["learning_path"][0]["papers"][0]["title"], "CLIP")

    def test_irreparable_nested_field_returns_validation_failed(self) -> None:
        model = FakeModel(
            content=json.dumps(
                {
                    "domain": "图神经网络",
                    "prerequisites": [
                        {
                            "name": {
                                "unexpected": "object",
                            }
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )

        result = handle_domain_onboarding_message(
            make_message("图神经网络"),
            make_app_state(model),
        )

        self.assertEqual(result["status"], "validation_failed")
        self.assertEqual(
            result["validation_errors"][0]["loc"],
            ("prerequisites", 0, "name"),
        )

    def test_accepts_markdown_wrapped_model_output(self) -> None:
        model = FakeModel(content='结果如下：\n```json\n{"domain":"具身智能"}\n```')

        result = handle_domain_onboarding_message(
            make_message("具身智能"),
            make_app_state(model),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["domain"], "具身智能")

    def test_high_quality_result_does_not_retry(self) -> None:
        model = FakeModel(content=json.dumps(make_complete_payload(), ensure_ascii=False))

        result = handle_domain_onboarding_message(
            make_message("多模态大模型"),
            make_app_state(model),
        )

        self.assertEqual(len(model.calls), 1)
        self.assertGreaterEqual(
            result["quality"]["score"],
            result["quality"]["threshold"],
        )
        self.assertEqual(result["quality"]["attempts"], 1)
        self.assertEqual(result["quality"]["selected_attempt"], 1)
        self.assertEqual(result["quality"]["retry_status"], "not_needed")

    def test_low_quality_result_retries_once_and_selects_improvement(self) -> None:
        first = json.dumps({"domain": "多模态大模型"}, ensure_ascii=False)
        improved = json.dumps(make_complete_payload(), ensure_ascii=False)
        model = FakeModel(content=[first, improved])

        result = handle_domain_onboarding_message(
            make_message("多模态大模型"),
            make_app_state(model),
        )

        self.assertEqual(len(model.calls), 2)
        self.assertEqual(result["quality"]["attempts"], 2)
        self.assertEqual(result["quality"]["selected_attempt"], 2)
        self.assertEqual(result["quality"]["retry_status"], "improved")
        self.assertGreaterEqual(
            result["quality"]["score"],
            result["quality"]["threshold"],
        )
        self.assertIn("唯一一次修正机会", model.calls[1]["messages"][1]["content"])

    def test_metrics_track_retry_improvement_latency_and_extra_cost(self) -> None:
        first = json.dumps({"domain": "多模态大模型"}, ensure_ascii=False)
        improved = json.dumps(make_complete_payload(), ensure_ascii=False)
        model = FakeModel(content=[first, improved])
        metrics = DomainOnboardingMetrics(
            input_cost_per_million_tokens=2.0,
            output_cost_per_million_tokens=4.0,
        )

        handle_domain_onboarding_message(
            make_message("多模态大模型"),
            make_app_state(model, metrics),
        )
        snapshot = metrics.snapshot()

        self.assertEqual(snapshot["requests_total"], 1)
        self.assertEqual(snapshot["statuses"], {"ok": 1})
        self.assertEqual(snapshot["retry"]["requests"], 1)
        self.assertEqual(snapshot["retry"]["rate"], 1.0)
        self.assertEqual(snapshot["retry"]["improved"], 1)
        self.assertEqual(snapshot["retry"]["improvement_rate"], 1.0)
        self.assertEqual(snapshot["latency"]["request"]["count"], 1)
        self.assertEqual(snapshot["latency"]["first_call"]["count"], 1)
        self.assertEqual(snapshot["latency"]["retry_call"]["count"], 1)
        self.assertEqual(snapshot["extra_call_cost"]["model_calls"], 1)
        self.assertEqual(snapshot["extra_call_cost"]["prompt_tokens"], 120)
        self.assertEqual(snapshot["extra_call_cost"]["completion_tokens"], 80)
        self.assertEqual(snapshot["extra_call_cost"]["total_tokens"], 200)
        self.assertTrue(snapshot["extra_call_cost"]["usage_reported"])
        self.assertTrue(snapshot["extra_call_cost"]["pricing_configured"])
        self.assertEqual(snapshot["extra_call_cost"]["estimated_cost"], 0.00056)

    def test_retry_without_improvement_keeps_first_result(self) -> None:
        first = json.dumps({"domain": "第一次结果"}, ensure_ascii=False)
        second = json.dumps({"domain": "第二次结果"}, ensure_ascii=False)
        model = FakeModel(content=[first, second])

        result = handle_domain_onboarding_message(
            make_message("测试领域"),
            make_app_state(model),
        )

        self.assertEqual(len(model.calls), 2)
        self.assertEqual(result["domain"], "第一次结果")
        self.assertEqual(result["quality"]["selected_attempt"], 1)
        self.assertEqual(result["quality"]["retry_status"], "not_improved")

    def test_invalid_retry_keeps_first_valid_result(self) -> None:
        first = json.dumps({"domain": "第一次结果"}, ensure_ascii=False)
        model = FakeModel(content=[first, "重试结果不是 JSON"])

        result = handle_domain_onboarding_message(
            make_message("测试领域"),
            make_app_state(model),
        )

        self.assertEqual(len(model.calls), 2)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["domain"], "第一次结果")
        self.assertEqual(result["quality"]["selected_attempt"], 1)
        self.assertEqual(result["quality"]["retry_status"], "invalid_response")

    def test_retry_llm_failure_keeps_first_valid_result(self) -> None:
        first = json.dumps({"domain": "第一次结果"}, ensure_ascii=False)
        model = FakeModel(
            content=[
                first,
                RuntimeError("retry service unavailable"),
            ]
        )

        result = handle_domain_onboarding_message(
            make_message("测试领域"),
            make_app_state(model),
        )

        self.assertEqual(len(model.calls), 2)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["domain"], "第一次结果")
        self.assertEqual(result["quality"]["selected_attempt"], 1)
        self.assertEqual(result["quality"]["retry_status"], "llm_failed")

    def test_empty_input_does_not_call_model(self) -> None:
        model = FakeModel(content='{"domain":"unused"}')

        result = handle_domain_onboarding_message(make_message("  "), make_app_state(model))

        self.assertEqual(result["status"], "invalid_input")
        self.assertEqual(model.calls, [])

    def test_unparseable_output_returns_parse_failed(self) -> None:
        model = FakeModel(content="这不是 JSON")

        result = handle_domain_onboarding_message(make_message("量子计算"), make_app_state(model))

        self.assertEqual(result["status"], "parse_failed")
        self.assertEqual(result["raw_text"], "这不是 JSON")

    def test_model_error_returns_llm_failed(self) -> None:
        model = FakeModel(error=RuntimeError("service unavailable"))

        result = handle_domain_onboarding_message(make_message("图神经网络"), make_app_state(model))

        self.assertEqual(result["status"], "llm_failed")
        self.assertIn("service unavailable", result["text"])

    def test_toolless_agent_does_not_send_tool_options(self) -> None:
        model = FakeModel(
            content=json.dumps(
                make_complete_payload("自然语言处理"),
                ensure_ascii=False,
            )
        )

        handle_domain_onboarding_message(make_message("NLP"), make_app_state(model))

        self.assertEqual(len(model.calls), 1)
        self.assertNotIn("tools", model.calls[0])
        self.assertNotIn("tool_choice", model.calls[0])


if __name__ == "__main__":
    unittest.main()
