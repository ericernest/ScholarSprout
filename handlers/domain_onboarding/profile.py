"""从请求和 metadata 构建请求级学习者画像。"""

from __future__ import annotations

import re
from typing import Protocol

from .schemas import DomainOnboardingRequest, LearnerProfile, Preference


class LearnerProfileBuilder(Protocol):
    def build(self, request: DomainOnboardingRequest) -> LearnerProfile: ...


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,，、;；]", value) if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


class RuleBasedProfileBuilder:
    def build(self, request: DomainOnboardingRequest) -> LearnerProfile:
        metadata = request.metadata
        background = _string_list(metadata.get("background")) or self._infer_background(request.query)
        known = _string_list(metadata.get("known_concepts"))
        goal = str(metadata.get("goal") or "").strip() or self._infer_goal(request.query)
        return LearnerProfile(
            background=background,
            goal=goal,
            time_budget_weeks=self._read_weeks(metadata.get("time_budget_weeks"), request.query),
            preference=self._read_preference(metadata.get("preference"), request.query),
            known_concepts=known,
        )

    def _read_weeks(self, raw: object, query: str) -> int | None:
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return raw
        if isinstance(raw, str) and raw.strip().isdigit():
            return int(raw.strip())
        match = re.search(r"(\d{1,3})\s*(?:周|星期|weeks?)", query, re.IGNORECASE)
        if match:
            return int(match.group(1))
        chinese_match = re.search(r"([一二两三四五六七八九十]{1,3})\s*(?:周|星期)", query)
        return self._chinese_number(chinese_match.group(1)) if chinese_match else None

    @staticmethod
    def _chinese_number(value: str) -> int | None:
        digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if value == "十":
            return 10
        if "十" in value:
            left, _, right = value.partition("十")
            tens = digits.get(left, 1) if left else 1
            ones = digits.get(right, 0) if right else 0
            return tens * 10 + ones
        return digits.get(value)

    def _read_preference(self, raw: object, query: str) -> Preference:
        normalized = str(raw or "").strip().lower()
        aliases: dict[str, Preference] = {
            "theory_first": "theory_first", "theory": "theory_first", "理论": "theory_first",
            "experiment_first": "experiment_first", "experiment": "experiment_first",
            "practice": "experiment_first", "实践": "experiment_first", "实验": "experiment_first",
            "balanced": "balanced", "平衡": "balanced",
        }
        if normalized in aliases:
            return aliases[normalized]
        if re.search(r"偏(?:向|重)?(?:实验|实践)|动手|复现", query):
            return "experiment_first"
        if re.search(r"偏(?:向|重)?理论|数学推导|原理", query):
            return "theory_first"
        return "balanced"

    def _infer_background(self, query: str) -> list[str]:
        for pattern in (r"(?:已经|已|掌握|学过|熟悉)([^，。；;]{2,40})", r"(?:背景是|基础是)([^，。；;]{2,40})"):
            match = re.search(pattern, query)
            if match:
                return _string_list(match.group(1))
        return []

    def _infer_goal(self, query: str) -> str:
        if re.search(r"复现|实验|项目", query):
            return "建立领域基础认知，并完成一个可复现的基线实验"
        if re.search(r"选题|研究问题|论文", query):
            return "建立领域知识框架并形成可继续研究的论文阅读与选题路径"
        return "建立领域基础认知并具备阅读代表论文的能力"
