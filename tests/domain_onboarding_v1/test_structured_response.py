from __future__ import annotations

import json
import unittest

from handlers.domain_onboarding.llm import invoke_json
from handlers.domain_onboarding.response_contracts import (
    DEVELOPMENT_STAGE_CONTRACT,
    LANDSCAPE_SECTION_CONTRACT,
    LEARNING_PATH_SECTION_CONTRACT,
)
from handlers.domain_onboarding.structured_response import (
    FieldRule,
    ResponseContract,
    StructuredResponseError,
    adapt_structured_response,
)


class _JSONModel:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def chat(self, **_: object) -> dict[str, object]:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(self.payload, ensure_ascii=False)
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
        }


class StructuredResponseTests(unittest.TestCase):
    def test_type_correct_alias_wins_over_scalar_canonical_field(self) -> None:
        stage = {"stage_id": "s2", "name": "方法阶段", "summary": "方法演进"}

        result = adapt_structured_response(
            {
                "development_stage": "第二阶段",
                "stage": stage,
                "paper_guidance": "not a list",
                "evidence_claims": [{"claim": "supported"}, "bad"],
            },
            DEVELOPMENT_STAGE_CONTRACT,
        )

        self.assertEqual(result.data["development_stage"], stage)
        self.assertNotIn("paper_guidance", result.data)
        self.assertEqual(result.data["evidence_claims"], [{"claim": "supported"}])
        self.assertIn("alias_applied", [event.code for event in result.events])
        self.assertIn(
            "wrong_type_candidate_skipped",
            [event.code for event in result.events],
        )

    def test_unknown_bounded_wrapper_and_outer_companion_fields_are_merged(self) -> None:
        result = adapt_structured_response(
            {
                "provider_payload": {
                    "landscape": {
                        "problems": ["p1"],
                        "subdirections": ["d1"],
                    }
                },
                "evidence_claims": [{"claim": "outer evidence"}],
            },
            LANDSCAPE_SECTION_CONTRACT,
        )

        self.assertEqual(result.source_path, "$.provider_payload")
        self.assertEqual(result.data["current_landscape"]["problems"], ["p1"])
        self.assertEqual(
            result.data["evidence_claims"], [{"claim": "outer evidence"}]
        )

    def test_single_object_becomes_list_but_string_never_becomes_characters(self) -> None:
        result = adapt_structured_response(
            {"steps": {"step": "1", "goal": "入门"}, "evidence_claims": "bad"},
            LEARNING_PATH_SECTION_CONTRACT,
        )

        self.assertEqual(
            result.data["learning_path"], [{"step": "1", "goal": "入门"}]
        )
        self.assertNotIn("evidence_claims", result.data)

    def test_direct_stage_object_is_wrapped(self) -> None:
        stage = {"stage_id": "s1", "name": "基础阶段", "summary": "奠定基础"}

        result = adapt_structured_response(stage, DEVELOPMENT_STAGE_CONTRACT)

        self.assertEqual(result.data["development_stage"], stage)
        self.assertIn(
            "direct_object_wrapped", [event.code for event in result.events]
        )

    def test_equally_valid_conflicting_siblings_are_rejected(self) -> None:
        contract = ResponseContract(
            name="ambiguous",
            fields={"items": FieldRule("array", required=True)},
            wrappers=("left", "right"),
        )

        with self.assertRaisesRegex(StructuredResponseError, "ambiguous"):
            adapt_structured_response(
                {"left": {"items": [1]}, "right": {"items": [2]}}, contract
            )

    def test_invoke_json_applies_contract_before_returning(self) -> None:
        payload, stats = invoke_json(
            _JSONModel(
                {
                    "result": {
                        "path": [{"step": "1", "goal": "理解核心问题"}]
                    }
                }
            ),
            system_prompt="return json",
            user_prompt="{}",
            contract=LEARNING_PATH_SECTION_CONTRACT,
        )

        self.assertEqual(payload["learning_path"][0]["step"], "1")
        self.assertEqual(stats.total_tokens, 5)


if __name__ == "__main__":
    unittest.main()
