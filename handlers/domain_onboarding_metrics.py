"""兼容旧 import 的领域入门指标导出。"""

from handlers.domain_onboarding.metrics import (
    DomainOnboardingMetrics,
    DomainOnboardingRequestTrace,
)

__all__ = ["DomainOnboardingMetrics", "DomainOnboardingRequestTrace"]
