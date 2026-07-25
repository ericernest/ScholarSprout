"""领域入门接口层：校验请求、调用 Pipeline、记录指标并返回响应。"""

from __future__ import annotations

from time import perf_counter
from typing import Any

from pydantic import ValidationError

from channels.base import ChannelMessage
from handlers.domain_onboarding.legacy import (
    build_empty_result,
    build_llm_failed_result,
    build_parse_failed_result,
    build_success_result,
    build_validation_failed_result,
    handle_domain_onboarding_message as handle_legacy_domain_onboarding_message,
    normalize_domain_onboarding_output,
    parse_json_object,
    with_retry_metadata,
)
from handlers.domain_onboarding.metrics import DomainOnboardingRequestTrace
from handlers.domain_onboarding.schemas import DomainOnboardingRequest


def handle_domain_onboarding_message(message: ChannelMessage, app_state: Any) -> dict[str, Any]:
    """使用 V1 Pipeline；未装配 Pipeline 时保持 V0 兼容行为。"""
    pipeline = getattr(app_state, "domain_onboarding_pipeline", None)
    if pipeline is None:
        return handle_legacy_domain_onboarding_message(message, app_state)

    trace = DomainOnboardingRequestTrace()
    started_at = perf_counter()
    try:
        try:
            request = DomainOnboardingRequest(
                query=str(message.content or ""),
                session_id=message.session_id,
                user_id=message.user_id,
                metadata=message.metadata,
            )
        except ValidationError:
            trace.status = "invalid_input"
            return build_empty_result()

        result = pipeline.run(request, trace)
        trace.status = result.status
        return result.to_response()
    except Exception as error:
        trace.status = "internal_error"
        return {
            "status": "internal_error",
            "mode": "domain_onboarding",
            "query": str(message.content or "").strip(),
            "error": str(error),
        }
    finally:
        trace.total_duration_ms = round((perf_counter() - started_at) * 1000, 3)
        metrics = getattr(app_state, "domain_onboarding_metrics", None)
        if metrics is not None:
            metrics.record(trace)


__all__ = [
    "build_empty_result",
    "build_llm_failed_result",
    "build_parse_failed_result",
    "build_success_result",
    "build_validation_failed_result",
    "handle_domain_onboarding_message",
    "normalize_domain_onboarding_output",
    "parse_json_object",
    "with_retry_metadata",
]
