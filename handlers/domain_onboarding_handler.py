"""提供 domain_onboarding mode 的业务 handler。"""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from channels.base import ChannelMessage
from handlers.domain_onboarding_quality import (
    build_quality_retry_prompt,
    evaluate_content_quality,
)
from handlers.domain_onboarding_metrics import DomainOnboardingRequestTrace
from handlers.domain_onboarding_schema import (
    AttemptNumber,
    ContentQuality,
    DomainOnboardingOutput,
    DomainOnboardingSuccessResult,
    RetryStatus,
)
from runtime.agent_runner import run_agent_detailed

MODE = "domain_onboarding"


def build_empty_result(query: str = "") -> dict[str, Any]:
    return {
        "status": "invalid_input",
        "mode": MODE,
        "query": query,
        "text": "请输入想入门的研究方向。",
        "domain": "",
        "prerequisites": [],
        "development_stages": [],
        "current_landscape": {
            "problems": [],
            "subdirections": [],
        },
        "learning_path": [],
    }


def parse_json_object(raw_text: str) -> dict[str, Any] | None:
    text = raw_text.strip()
    if not text:
        return None

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
            return None

        try:
            candidate, _ = decoder.raw_decode(text, object_start)
        except json.JSONDecodeError:
            search_from = object_start + 1
            continue

        if isinstance(candidate, dict):
            return candidate

        search_from = object_start + 1


def normalize_domain_onboarding_output(
    query: str,
    result: dict[str, Any],
) -> DomainOnboardingOutput:
    domain = result.get("domain")
    if domain is None or domain == "":
        domain = query

    text = result.get("text")
    if text is None or text == "":
        text = f"已生成 {domain if isinstance(domain, str) else query} 的领域入门方案。"

    payload = {
        **result,
        "domain": domain,
        "text": text,
    }
    return DomainOnboardingOutput.model_validate(payload)


def build_success_result(
    query: str,
    output: DomainOnboardingOutput,
    quality: ContentQuality,
) -> dict[str, Any]:
    payload = {
        **output.model_dump(mode="json"),
        "status": "ok",
        "mode": MODE,
        "query": query,
        "quality": quality.model_dump(mode="json"),
    }
    validated = DomainOnboardingSuccessResult.model_validate(payload)
    return validated.model_dump(mode="json")


def with_retry_metadata(
    quality: ContentQuality,
    *,
    selected_attempt: AttemptNumber,
    retry_status: RetryStatus,
) -> ContentQuality:
    return ContentQuality.model_validate(
        {
            **quality.model_dump(mode="json"),
            "attempts": 2,
            "selected_attempt": selected_attempt,
            "retry_status": retry_status,
        }
    )


def build_parse_failed_result(query: str, raw_text: str) -> dict[str, Any]:
    return {
        "status": "parse_failed",
        "mode": MODE,
        "query": query,
        "domain": query,
        "text": raw_text or "模型未返回可解析内容。",
        "raw_text": raw_text,
        "prerequisites": [],
        "development_stages": [],
        "current_landscape": {
            "problems": [],
            "subdirections": [],
        },
        "learning_path": [],
    }


def build_llm_failed_result(query: str, raw_text: str) -> dict[str, Any]:
    return {
        "status": "llm_failed",
        "mode": MODE,
        "query": query,
        "domain": query,
        "text": raw_text,
        "prerequisites": [],
        "development_stages": [],
        "current_landscape": {
            "problems": [],
            "subdirections": [],
        },
        "learning_path": [],
    }


def build_validation_failed_result(
    query: str,
    raw_text: str,
    error: ValidationError,
) -> dict[str, Any]:
    return {
        "status": "validation_failed",
        "mode": MODE,
        "query": query,
        "domain": query,
        "text": raw_text or "模型输出未通过结构校验。",
        "raw_text": raw_text,
        "validation_errors": error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        ),
        "prerequisites": [],
        "development_stages": [],
        "current_landscape": {
            "problems": [],
            "subdirections": [],
        },
        "learning_path": [],
    }


# 执行领域入门业务流程，并将模型调用信息写入当前请求 trace。
def _handle_domain_onboarding_message(
    message: ChannelMessage,
    app_state: Any,
    trace: DomainOnboardingRequestTrace,
) -> dict[str, Any]:
    query = str(message.content or "").strip()
    if not query:
        return build_empty_result()

    first_run = run_agent_detailed(
        agent=app_state.domain_onboarding_agent,
        user_content=query,
        model=app_state.model,
        tool_registry=app_state.tool_registry,
        max_steps=1,
    )
    trace.first_call_duration_ms = first_run.duration_ms
    trace.first_model_calls = first_run.model_calls
    trace.first_usage = first_run.usage
    text = first_run.text

    if text.startswith("LLM 调用失败："):
        return build_llm_failed_result(query, text)

    parsed = parse_json_object(text)
    if parsed is None:
        return build_parse_failed_result(query, text)

    try:
        first_output = normalize_domain_onboarding_output(query, parsed)
    except ValidationError as error:
        return build_validation_failed_result(query, text, error)

    first_quality = evaluate_content_quality(first_output)
    trace.first_score = first_quality.score
    if first_quality.score >= first_quality.threshold:
        return build_success_result(query, first_output, first_quality)

    retry_run = run_agent_detailed(
        agent=app_state.domain_onboarding_agent,
        user_content=build_quality_retry_prompt(query, first_quality),
        model=app_state.model,
        tool_registry=app_state.tool_registry,
        max_steps=1,
    )
    trace.retry_call_duration_ms = retry_run.duration_ms
    trace.retry_model_calls = retry_run.model_calls
    trace.retry_usage = retry_run.usage
    retry_text = retry_run.text
    if retry_text.startswith("LLM 调用失败："):
        trace.retry_status = "llm_failed"
        quality = with_retry_metadata(
            first_quality,
            selected_attempt=1,
            retry_status="llm_failed",
        )
        return build_success_result(query, first_output, quality)

    retry_parsed = parse_json_object(retry_text)
    if retry_parsed is None:
        trace.retry_status = "invalid_response"
        quality = with_retry_metadata(
            first_quality,
            selected_attempt=1,
            retry_status="invalid_response",
        )
        return build_success_result(query, first_output, quality)

    try:
        retry_output = normalize_domain_onboarding_output(query, retry_parsed)
    except ValidationError:
        trace.retry_status = "invalid_response"
        quality = with_retry_metadata(
            first_quality,
            selected_attempt=1,
            retry_status="invalid_response",
        )
        return build_success_result(query, first_output, quality)

    retry_quality = evaluate_content_quality(retry_output)
    if retry_quality.score > first_quality.score:
        trace.retry_status = "improved"
        quality = with_retry_metadata(
            retry_quality,
            selected_attempt=2,
            retry_status="improved",
        )
        return build_success_result(query, retry_output, quality)

    trace.retry_status = "not_improved"
    quality = with_retry_metadata(
        first_quality,
        selected_attempt=1,
        retry_status="not_improved",
    )
    return build_success_result(query, first_output, quality)


# 处理 domain_onboarding mode 消息并汇总请求级监控数据。
def handle_domain_onboarding_message(message: ChannelMessage, app_state: Any) -> dict[str, Any]:
    trace = DomainOnboardingRequestTrace()
    started_at = perf_counter()
    result: dict[str, Any] | None = None

    try:
        result = _handle_domain_onboarding_message(message, app_state, trace)
        trace.status = str(result.get("status") or "unknown")
        quality = result.get("quality")
        if isinstance(quality, dict):
            trace.final_score = quality.get("score")
            trace.retry_status = str(
                quality.get("retry_status") or trace.retry_status
            )
        return result
    finally:
        trace.total_duration_ms = round((perf_counter() - started_at) * 1000, 3)
        metrics = getattr(app_state, "domain_onboarding_metrics", None)
        if metrics is not None:
            metrics.record(trace)
