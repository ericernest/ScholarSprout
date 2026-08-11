from __future__ import annotations

import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from handlers.paper_reading.handler import (
    RESEARCH_GUIDE_MAX_WORKERS,
    SURVEY_CARD_MAX_WORKERS,
    SURVEY_CARD_MAX_TOKENS,
    SURVEY_MAP_TASK_LIMIT,
    SURVEY_PLAN_MAX_TOKENS,
    SURVEY_SECTION_GUIDE_TASK_LIMIT,
    _build_llm_reading_map,
    _build_survey_plan_card_reading_map,
    _ensure_survey_prerequisite_task,
    _normalize_survey_card_plan,
    _reading_map_response_json,
    _research_reading_sections,
    resume_pending_reading_map_generations,
)
from handlers.paper_reading.postprocessors.common import extract_json_object, repair_json_object


class _ConcurrentJsonModel:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls: list[dict] = []

    def chat(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
        prompt = kwargs["messages"][-1]["content"]
        if "planning a novice-oriented reading map" in prompt:
            content = json.dumps({
                "map_tasks": [
                    {
                        "task_id": f"map:{index}",
                        "group_key": "technical_routes",
                        "title": f"路线 {index}",
                        "priority": "high",
                        "section_ids": ["sec:intro"],
                    }
                    for index in range(4)
                ],
                "section_guide_tasks": [
                    {
                        "task_id": f"guide:{index}",
                        "section_id": "sec:intro",
                        "title": f"章节 {index}",
                        "priority": "medium",
                        "section_ids": ["sec:intro"],
                    }
                    for index in range(4)
                ],
            }, ensure_ascii=False)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.08)
            if "Target group: prerequisite_card" in prompt:
                payload = {
                    "prerequisite_card": {
                        "concepts": [{"name": "概念", "why_needed": "理解论文", "learn_first": [], "difficulty": "easy", "evidence": "引言证据", "source_sections": []}],
                        "field_questions": [],
                        "reading_order": [],
                        "anchor_works": [],
                        "common_confusions": [],
                    }
                }
            elif "Target group: section_guides" in prompt:
                payload = {
                    "section_id": "sec:intro",
                    "title": "引言",
                    "section_role": "introduction",
                    "read_priority": "high",
                    "novice_summary": "理解研究背景与技术路线。",
                    "cards": [],
                }
            else:
                payload = {
                    "items": [{
                        "route_name": "并行路线",
                        "core_mechanism": "并行生成卡片",
                        "typical_flow": "规划后并发执行",
                        "strengths": "降低总等待时间",
                        "limitations": "受并发限制",
                        "representative_methods": [],
                        "evidence": "引言描述了技术路线。",
                        "source_sections": [{"section_id": "sec:intro", "title": "Introduction", "page": 1}],
                    }]
                }
            content = json.dumps(payload, ensure_ascii=False)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        finally:
            with self.lock:
                self.active -= 1


class ReadingMapRuntimeTests(unittest.TestCase):
    def test_research_section_guides_cover_more_than_old_28_section_limit(self) -> None:
        sections = [{
            "section_id": f"sec:{index}",
            "title": f"Section {index}",
            "content": "Substantive section text.",
        } for index in range(31)]
        sections.extend([
            {"section_id": "sec:references", "title": "References", "content": "[1] Citation"},
            {"section_id": "sec:empty", "title": "Empty heading", "content": ""},
        ])

        selected = _research_reading_sections(
            {"sections": sections},
            {"paper_type": "research"},
        )

        self.assertEqual(len(selected), 31)
        self.assertEqual(selected[-1]["section_id"], "sec:30")

    def test_research_overview_and_grouped_guides_run_in_bounded_parallel(self) -> None:
        class ResearchModel:
            def __init__(self) -> None:
                self.lock = threading.Lock()
                self.active = 0
                self.max_active = 0
                self.calls: list[dict] = []

            def chat(self, **kwargs):
                with self.lock:
                    self.calls.append(kwargs)
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                try:
                    time.sleep(0.06)
                    system = kwargs["messages"][0]["content"]
                    if "research section guides" in system:
                        prompt = kwargs["messages"][-1]["content"]
                        section_ids = list(dict.fromkeys(
                            __import__("re").findall(r'"section_id": "(sec:\d+)"', prompt)
                        ))
                        payload = {
                            "section_guides": [{
                                "section_id": section_id,
                                "title": section_id,
                                "novice_summary": "章节主线",
                                "cards": [{"title": "阅读重点", "content": {"core_message": "理解本节"}}],
                            } for section_id in section_ids]
                        }
                    else:
                        payload = {
                            "paper_type": "research",
                            "map_variant": "research",
                            "research_problem": {"title": "问题", "one_sentence": "研究问题"},
                            "core_method": {"name": "方法", "one_sentence": "核心方法"},
                            "section_guides": [],
                        }
                    content = json.dumps(payload, ensure_ascii=False)
                    return SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")]
                    )
                finally:
                    with self.lock:
                        self.active -= 1

        model = ResearchModel()
        sections = [{
            "section_id": f"sec:{index}",
            "title": f"Section {index}",
            "content": "Research details and evidence. " * 30,
        } for index in range(7)]
        result = _build_llm_reading_map(
            paper={"title": "Research paper", "abstract": "Abstract", "sections": sections},
            fallback={"paper_type": "research", "map_variant": "research"},
            model=model,
            skill_registry=None,
        )

        self.assertEqual(result["status"], "llm_done")
        self.assertEqual(len(result["section_guides"]), 7)
        self.assertEqual(result["generation_artifacts_summary"]["requests_total"], 4)
        self.assertEqual(result["generation_artifacts_summary"]["sections_fallback_generated"], 0)
        self.assertGreaterEqual(model.max_active, 2)
        self.assertLessEqual(model.max_active, RESEARCH_GUIDE_MAX_WORKERS)

    def test_invalid_json_reports_output_truncation(self) -> None:
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"research_problem": {"title": "cut off"}'),
            finish_reason="length",
        )])
        with self.assertRaisesRegex(ValueError, r"模型服务截断.*应用未设置 max_tokens"):
            _reading_map_response_json(response, label="研究总览")

    def test_completed_malformed_json_is_repaired_without_model_retry(self) -> None:
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"research_problem": {"title": "问题",} "core_method": {"name": "方法"}}'),
            finish_reason="stop",
        )])
        parsed = _reading_map_response_json(response, label="研究总览")

        self.assertEqual(parsed["research_problem"]["title"], "问题")
        self.assertEqual(parsed["core_method"]["name"], "方法")
        self.assertIsNotNone(repair_json_object("{'paper_type': 'research', 'map_variant': 'research'}"))

    def test_json_parser_repairs_inner_quotes_but_rejects_truncated_outer_object(self) -> None:
        parsed = extract_json_object('{"title": "for "Mind" Exploration", "items": []}')
        self.assertEqual(parsed["title"], 'for "Mind" Exploration')
        self.assertIsNone(extract_json_object('{"outer": {"ok": true}'))

    def test_survey_cards_run_in_bounded_parallel_and_use_json_settings(self) -> None:
        model = _ConcurrentJsonModel()
        paper = {
            "paper_id": "paper-1",
            "title": "A Survey of Parallel Systems",
            "abstract": "Survey abstract",
            "sections": [{
                "section_id": "sec:intro",
                "title": "1 Introduction",
                "content": "This survey compares technical routes and their limitations. " * 20,
                "start_page": 1,
            }],
        }
        fallback = {
            "paper_type": "survey",
            "map_variant": "survey",
            "prerequisite_card": {},
            "research_map": {},
            "survey_map": {},
            "section_guides": [],
        }

        started = time.perf_counter()
        result = _build_survey_plan_card_reading_map(
            paper=paper,
            fallback=fallback,
            model=model,
            skill_registry=None,
            storage=None,
            paper_id="paper-1",
            generation_id="generation-1",
        )
        elapsed = time.perf_counter() - started

        self.assertLessEqual(model.max_active, SURVEY_CARD_MAX_WORKERS)
        self.assertGreaterEqual(model.max_active, 2)
        self.assertLess(elapsed, 0.55)
        self.assertIn(result["status"], {"llm_done", "failed_partial"})
        for call in model.calls:
            self.assertEqual(call["response_format"], {"type": "json_object"})
            self.assertTrue(call["disable_thinking"])
            prompt = call["messages"][-1]["content"]
            expected_max_tokens = (
                SURVEY_PLAN_MAX_TOKENS
                if "planning a novice-oriented reading map" in prompt
                else SURVEY_CARD_MAX_TOKENS
            )
            self.assertEqual(call["max_tokens"], expected_max_tokens)
            self.assertGreater(call["timeout"], 0)
            self.assertEqual(call["max_retries"], 0)

    def test_survey_plan_enforces_task_limits(self) -> None:
        manifest = [
            {"section_id": f"sec:{index}", "section_index": index, "title": f"Section {index}"}
            for index in range(1, 50)
        ]
        plan = {
            "map_tasks": [
                {"task_id": f"map:{index}", "group_key": "technical_routes", "section_ids": ["sec:1"]}
                for index in range(30)
            ],
            "section_guide_tasks": [
                {"task_id": f"guide:{index}", "section_id": f"sec:{index + 1}", "section_ids": [f"sec:{index + 1}"]}
                for index in range(40)
            ],
        }

        normalized = _normalize_survey_card_plan(plan, manifest)
        bounded = _ensure_survey_prerequisite_task(normalized, manifest)

        self.assertLessEqual(normalized["map_tasks_count"], SURVEY_MAP_TASK_LIMIT)
        self.assertLessEqual(normalized["section_guide_tasks_count"], SURVEY_SECTION_GUIDE_TASK_LIMIT)
        self.assertLessEqual(len(bounded["tasks"]), 1 + SURVEY_MAP_TASK_LIMIT + SURVEY_SECTION_GUIDE_TASK_LIMIT)

    def test_restart_requeues_persisted_running_map_with_heartbeat(self) -> None:
        paper = {
            "paper_id": "paper-1",
            "sections": [{"section_id": "sec:1", "content": "body"}],
            "reading_map_status": "llm_running",
            "reading_map_phase": "generating_cards",
            "reading_map_generation_id": "generation-1",
            "reading_map_started_at": "2026-08-10T00:00:00+00:00",
        }

        class Storage:
            def __init__(self) -> None:
                self.saved = None

            def list_paper_documents(self):
                return [dict(paper)]

            def save_paper(self, paper_id, payload):
                self.saved = (paper_id, dict(payload))

        storage = Storage()
        state = SimpleNamespace(paper_storage=storage)
        with patch("handlers.paper_reading.handler._schedule_reading_map_generation") as schedule:
            resumed = resume_pending_reading_map_generations(state)

        self.assertEqual(resumed, 1)
        self.assertEqual(storage.saved[0], "paper-1")
        self.assertEqual(storage.saved[1]["reading_map_started_at"], paper["reading_map_started_at"])
        self.assertTrue(storage.saved[1]["reading_map_heartbeat_at"])
        self.assertTrue(storage.saved[1]["reading_map_resumed_at"])
        self.assertEqual(storage.saved[1]["reading_map_phase"], "queued")
        schedule.assert_called_once_with(state, "paper-1", generation_id="generation-1")


if __name__ == "__main__":
    unittest.main()
