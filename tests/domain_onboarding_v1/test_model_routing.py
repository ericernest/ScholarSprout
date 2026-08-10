from __future__ import annotations

import unittest
from unittest.mock import patch

from handlers.domain_onboarding.llm import StructuredLLMError, invoke_json
from handlers.domain_onboarding.model_routing import (
    RoutedChatModel,
    run_with_model_route,
)
from handlers.domain_onboarding.pipeline import create_default_pipeline

from .fakes import FakeJSONModel


class ModelRoutingTests(unittest.TestCase):
    def test_timeout_falls_back_within_one_bounded_route(self) -> None:
        delegate = FakeJSONModel(
            [TimeoutError("primary timeout"), {"ok": True}]
        )
        model = RoutedChatModel(
            delegate,
            ["fast-primary", "stable-backup"],
            route_name="planning",
        )

        payload, stats = invoke_json(
            model,
            system_prompt="Return JSON",
            user_prompt="{}",
            timeout_seconds=30.0,
        )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(stats.model_calls, 2)
        self.assertEqual(
            [call["model_name"] for call in delegate.calls],
            ["fast-primary", "stable-backup"],
        )
        self.assertEqual(
            [call["timeout"] for call in delegate.calls],
            [14.0, 14.0],
        )
        snapshot = model.snapshot()
        self.assertEqual(snapshot["selected_model"], "stable-backup")
        self.assertEqual(
            [item["status"] for item in snapshot["attempts"]],
            ["failed", "selected"],
        )

    def test_all_failures_report_attempt_count_without_error_text(self) -> None:
        model = RoutedChatModel(
            FakeJSONModel([TimeoutError("secret-one"), RuntimeError("secret-two")]),
            ["first", "second"],
            route_name="landscape",
        )

        with self.assertRaises(StructuredLLMError) as context:
            invoke_json(
                model,
                system_prompt="Return JSON",
                user_prompt="{}",
                timeout_seconds=20.0,
            )

        self.assertEqual(context.exception.stats.model_calls, 2)
        self.assertNotIn("secret", str(context.exception))
        self.assertIsNone(model.snapshot()["selected_model"])

    def test_pipeline_routes_can_be_configured_per_module(self) -> None:
        delegate = FakeJSONModel([{"ok": True}])
        environment = {
            "DOMAIN_ONBOARDING_PLANNING_MODELS": "planner-fast,planner-backup",
            "DOMAIN_ONBOARDING_DEVELOPMENT_MODELS": "development-strong",
            "DOMAIN_ONBOARDING_LANDSCAPE_MODELS": "landscape-strong,landscape-fast",
            "DOMAIN_ONBOARDING_LEARNING_PATH_MODELS": "path-fast",
            "DOMAIN_ONBOARDING_REPAIR_MODELS": "repair-fast",
        }

        with patch.dict("os.environ", environment, clear=False):
            pipeline = create_default_pipeline(delegate)
        try:
            self.assertEqual(
                pipeline.planner.model.model_names,
                ["planner-fast", "planner-backup"],
            )
            self.assertEqual(
                pipeline.generator.section_models["development"].model_names,
                ["development-strong"],
            )
            self.assertEqual(
                pipeline.generator.section_models["landscape"].model_names,
                ["landscape-strong", "landscape-fast"],
            )
            self.assertEqual(
                pipeline.generator.section_models["learning_path"].model_names,
                ["path-fast"],
            )
            self.assertEqual(
                pipeline.generator.repair_model.model_names,
                ["repair-fast"],
            )
        finally:
            pipeline.close()

    def test_default_generation_route_does_not_mix_provider_model_ids(self) -> None:
        class ConfiguredModel(FakeJSONModel):
            class Config:
                model_name = "deepseek-v4-flash"

            config = Config()

        with patch.dict("os.environ", {}, clear=True):
            pipeline = create_default_pipeline(ConfiguredModel([{"ok": True}]))
        try:
            self.assertEqual(
                pipeline.generator.model.model_names,
                ["deepseek-v4-flash", "deepseek-v4-pro"],
            )
        finally:
            pipeline.close()

    def test_caller_validation_failure_uses_next_model(self) -> None:
        delegate = FakeJSONModel([{"wrong": True}, {"learning_path": []}])
        model = RoutedChatModel(
            delegate,
            ["invalid-json-shape", "valid-backup"],
            route_name="learning_path",
        )
        attempt_timeouts = []

        def operation(candidate, timeout):
            attempt_timeouts.append(timeout)
            payload, _ = invoke_json(
                candidate,
                system_prompt="Return JSON",
                user_prompt="{}",
                timeout_seconds=timeout,
            )
            if "learning_path" not in payload:
                raise ValueError("missing learning_path")
            return payload

        payload = run_with_model_route(
            model,
            operation,
            timeout_seconds=30.0,
        )

        self.assertEqual(payload, {"learning_path": []})
        self.assertEqual(len(attempt_timeouts), 2)
        self.assertTrue(all(0 < timeout <= 30.0 for timeout in attempt_timeouts))
        self.assertLess(attempt_timeouts[1], attempt_timeouts[0])
        self.assertEqual(model.snapshot()["selected_model"], "valid-backup")
        self.assertEqual(
            [item["error_type"] for item in model.snapshot()["attempts"][:-1]],
            ["ValueError"],
        )


if __name__ == "__main__":
    unittest.main()
