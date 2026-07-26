"""根据当前任务选择零个或一个专项 Skill。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from .models import CapabilitySelection, SkillSummary


class CapabilitySelectionError(ValueError):
    """表示能力选择结果无法解析或超出 Agent 权限。"""


# 从对象或字典中读取字段。
def _get_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


# 从 OpenAI-compatible 响应中读取文本。
def _get_response_content(response: Any) -> str:
    choices = _get_value(response, "choices", [])
    if not choices:
        return ""
    message = _get_value(choices[0], "message", {})
    return str(_get_value(message, "content", "") or "")


# 从可能带有额外文本的响应中提取 JSON object。
def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        return parsed

    decoder = json.JSONDecoder()
    search_from = 0
    while True:
        object_start = text.find("{", search_from)
        if object_start < 0:
            raise CapabilitySelectionError("Selector did not return a JSON object.")
        try:
            candidate, _ = decoder.raw_decode(text, object_start)
        except json.JSONDecodeError:
            search_from = object_start + 1
            continue
        if isinstance(candidate, dict):
            return candidate
        search_from = object_start + 1


# 再次校验 Selector 结果没有超出 Agent 注册范围。
def validate_capability_selection(
    selection: CapabilitySelection,
    allowed_skill_ids: list[str],
) -> CapabilitySelection:
    if selection.skill is not None and selection.skill not in allowed_skill_ids:
        raise CapabilitySelectionError(
            f"Selector returned unauthorized Skill: {selection.skill}"
        )

    return selection


# 使用现有模型接口完成一次轻量能力选择。
class CapabilitySelector:
    # 设置最多两次 JSON 解析尝试。
    def __init__(self, max_attempts: int = 2) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.max_attempts = max_attempts

    # 选择当前任务需要的零个或一个专项 Skill。
    def select(
        self,
        model: Any,
        role: str,
        user_task: str,
        skill_summaries: list[SkillSummary],
    ) -> CapabilitySelection:
        payload = {
            "agent_role": role,
            "user_task": user_task,
            "candidate_skills": [
                summary.model_dump(mode="json") for summary in skill_summaries
            ],
        }
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "Select at most one optional Skill needed for the current task. "
                    "The agent's default Skill is already active. "
                    "Return one JSON object with skill and reason. "
                    "skill must be a candidate Skill id or null."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ]
        last_error: Exception | None = None

        for attempt in range(self.max_attempts):
            try:
                response = model.chat(messages=messages)
                content = _get_response_content(response)
                raw_selection = _parse_json_object(content)
                return CapabilitySelection.model_validate(raw_selection)
            except (CapabilitySelectionError, ValidationError) as error:
                last_error = error
                if attempt + 1 < self.max_attempts:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "The previous response was invalid. Return only a valid JSON "
                                "object with skill and reason."
                            ),
                        }
                    )
            except Exception as error:
                raise CapabilitySelectionError(
                    f"Capability selector model call failed: {error}"
                ) from error

        raise CapabilitySelectionError(
            f"Capability selector returned invalid JSON: {last_error}"
        )
