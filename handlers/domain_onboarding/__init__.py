"""NoviceSynapse 领域入门 V1 流水线。"""

from .config import DomainOnboardingConfig
from .pipeline import DomainOnboardingPipeline, create_default_pipeline
from .schemas import DomainOnboardingRequest

__all__ = [
    "DomainOnboardingConfig",
    "DomainOnboardingPipeline",
    "DomainOnboardingRequest",
    "create_default_pipeline",
]
