"""为领域入门流程提供固定的普通科研新手画像。"""

from __future__ import annotations

from typing import Protocol

from .schemas import DomainOnboardingRequest, LearnerProfile


class LearnerProfileBuilder(Protocol):
    def build(self, request: DomainOnboardingRequest) -> LearnerProfile: ...


def standard_novice_profile() -> LearnerProfile:
    """返回兼容旧输出协议的固定画像，不读取任何个性化输入。"""

    return LearnerProfile()


class RuleBasedProfileBuilder:
    """兼容旧依赖注入名称；领域入门现在只生成标准新手路线。"""

    def build(self, request: DomainOnboardingRequest) -> LearnerProfile:
        del request
        return standard_novice_profile()
