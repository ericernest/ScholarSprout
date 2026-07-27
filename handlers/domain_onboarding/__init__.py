"""NoviceSynapse 领域入门 V1 流水线。"""

from .config import DomainOnboardingConfig
from .pipeline import DomainOnboardingPipeline, create_default_pipeline
from .policy import DomainOnboardingPolicy, PolicyRegistry
from .schemas import DomainOnboardingRequest

__all__ = [
    "DomainOnboardingConfig",
    "DomainOnboardingPolicy",
    "DomainOnboardingPipeline",
    "DomainOnboardingRequest",
    "PolicyRegistry",
    "create_default_pipeline",
]
