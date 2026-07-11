"""采集真实领域入门输出并搜索评分权重与重试阈值。"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from itertools import combinations, product
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents.agent import create_agent
from config.manager import load_config
from handlers.domain_onboarding_handler import (
    normalize_domain_onboarding_output,
    parse_json_object,
)
from handlers.domain_onboarding_quality import (
    BASELINE_POLICY,
    CALIBRATED_POLICY,
    QualityFeatureVector,
    QualityScoringPolicy,
    extract_quality_features,
)
from models.client import OpenAIClient
from runtime.agent_runner import run_agent_detailed
from tools.registry import create_builtin_tool_registry

DEFAULT_DOMAINS = [
    "多模态大模型",
    "图神经网络",
    "联邦学习",
    "具身智能",
    "量子机器学习",
]
DIMENSIONS = tuple(CALIBRATED_POLICY.weights())


@dataclass(frozen=True, slots=True)
class LabeledFeatureSample:
    domain: str
    variant: str
    features: QualityFeatureVector
    acceptable: bool


def collect_real_sample(
    domain: str,
    *,
    timeout: float,
) -> dict[str, Any]:
    app_config = load_config()
    client_config = replace(
        app_config.client,
        timeout=timeout,
        max_retries=0,
    )
    model = OpenAIClient(client_config)
    agent = create_agent(model, "domain_onboarding")
    run = run_agent_detailed(
        agent=agent,
        user_content=f"我想入门{domain}方向",
        model=model,
        tool_registry=create_builtin_tool_registry(),
        max_steps=1,
    )
    record: dict[str, Any] = {
        "domain": domain,
        "model": client_config.model_name,
        "duration_ms": run.duration_ms,
        "usage": {
            "prompt_tokens": run.usage.prompt_tokens,
            "completion_tokens": run.usage.completion_tokens,
            "total_tokens": run.usage.total_tokens,
        },
        "raw_output": run.text,
    }
    if run.text.startswith("LLM 调用失败："):
        record["status"] = "llm_failed"
        return record

    parsed = parse_json_object(run.text)
    if parsed is None:
        record["status"] = "parse_failed"
        return record

    try:
        output = normalize_domain_onboarding_output(domain, parsed)
    except ValidationError as error:
        record["status"] = "validation_failed"
        record["validation_errors"] = error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
        return record

    features, issues = extract_quality_features(output)
    record.update(
        {
            "status": "ok",
            "normalized_output": output.model_dump(mode="json"),
            "features": features.as_dict(),
            "issues": issues,
            "current_score": features.score(CALIBRATED_POLICY),
        }
    )
    return record


def collect_real_samples(
    domains: list[str],
    *,
    timeout: float,
    workers: int,
) -> list[dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(collect_real_sample, domain, timeout=timeout): domain
            for domain in domains
        }
        for future in as_completed(futures):
            domain = futures[future]
            try:
                record = future.result()
            except Exception as error:
                record = {
                    "domain": domain,
                    "status": "collector_failed",
                    "error": str(error),
                }
            results[domain] = record
            print(
                f"[{domain}] status={record['status']} "
                f"score={record.get('current_score', '-')}",
                flush=True,
            )
    return [results[domain] for domain in domains]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        json.dumps(record, ensure_ascii=False)
        for record in records
    )
    path.write_text(content + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _vector(values: dict[str, float]) -> QualityFeatureVector:
    return QualityFeatureVector(**values)


def _independent_acceptance_label(features: QualityFeatureVector) -> bool:
    values = list(features.as_dict().values())
    return min(values) >= 0.45 and sum(values) / len(values) >= 0.72


def build_calibration_samples(
    records: list[dict[str, Any]],
) -> list[LabeledFeatureSample]:
    samples: list[LabeledFeatureSample] = []
    for record in records:
        if record.get("status") != "ok":
            continue

        domain = str(record["domain"])
        base_values = {
            key: float(value)
            for key, value in record["features"].items()
        }

        variants: list[tuple[str, dict[str, float]]] = [("real", base_values)]
        for dimension in DIMENSIONS:
            moderate = dict(base_values)
            moderate[dimension] = min(moderate[dimension], 0.55)
            variants.append((f"moderate_{dimension}", moderate))

            severe = dict(base_values)
            severe[dimension] = min(severe[dimension], 0.20)
            variants.append((f"severe_{dimension}", severe))

        for first, second in combinations(DIMENSIONS, 2):
            paired = dict(base_values)
            paired[first] = min(paired[first], 0.40)
            paired[second] = min(paired[second], 0.40)
            variants.append((f"paired_{first}_{second}", paired))

        for variant, values in variants:
            features = _vector(values)
            samples.append(
                LabeledFeatureSample(
                    domain=domain,
                    variant=variant,
                    features=features,
                    acceptable=_independent_acceptance_label(features),
                )
            )
    return samples


def _balanced_accuracy(
    samples: list[LabeledFeatureSample],
    policy: QualityScoringPolicy,
) -> tuple[float, float, float]:
    true_positive = true_negative = false_positive = false_negative = 0
    for sample in samples:
        predicted = sample.features.score(policy) >= policy.retry_threshold
        if sample.acceptable and predicted:
            true_positive += 1
        elif sample.acceptable:
            false_negative += 1
        elif predicted:
            false_positive += 1
        else:
            true_negative += 1

    sensitivity = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else 0.0
    )
    specificity = (
        true_negative / (true_negative + false_positive)
        if true_negative + false_positive
        else 0.0
    )
    balanced = (sensitivity + specificity) / 2
    return balanced, sensitivity, specificity


def candidate_policies() -> list[QualityScoringPolicy]:
    candidates: list[QualityScoringPolicy] = []
    ranges = [
        range(5, 21, 5),
        range(10, 31, 5),
        range(20, 41, 5),
        range(10, 26, 5),
        range(15, 36, 5),
    ]
    for weights in product(*ranges):
        if sum(weights) != 100:
            continue
        for threshold in range(60, 91):
            candidates.append(
                QualityScoringPolicy(
                    domain_summary_weight=weights[0],
                    prerequisites_weight=weights[1],
                    development_stages_weight=weights[2],
                    current_landscape_weight=weights[3],
                    learning_path_weight=weights[4],
                    retry_threshold=threshold,
                )
            )
    return candidates


def calibrate_policy(
    samples: list[LabeledFeatureSample],
) -> tuple[QualityScoringPolicy, tuple[float, float, float]]:
    if not samples:
        raise ValueError("No valid calibration samples.")

    current_weights = CALIBRATED_POLICY.weights()

    def policy_distance(policy: QualityScoringPolicy) -> int:
        return sum(
            abs(policy.weights()[key] - current_weights[key])
            for key in current_weights
        ) + abs(policy.retry_threshold - CALIBRATED_POLICY.retry_threshold)

    best_policy = CALIBRATED_POLICY
    best_metrics = _balanced_accuracy(samples, best_policy)
    best_key = (
        best_metrics[0],
        min(best_metrics[1], best_metrics[2]),
        -policy_distance(best_policy),
    )
    for policy in candidate_policies():
        metrics = _balanced_accuracy(samples, policy)
        key = (
            metrics[0],
            min(metrics[1], metrics[2]),
            -policy_distance(policy),
        )
        if key > best_key:
            best_policy = policy
            best_metrics = metrics
            best_key = key
    return best_policy, best_metrics


def write_report(
    path: Path,
    records: list[dict[str, Any]],
    samples: list[LabeledFeatureSample],
    recommended: QualityScoringPolicy,
    recommended_metrics: tuple[float, float, float],
) -> None:
    baseline_metrics = _balanced_accuracy(samples, BASELINE_POLICY)
    current_metrics = _balanced_accuracy(samples, CALIBRATED_POLICY)
    valid_records = [record for record in records if record.get("status") == "ok"]
    lines = [
        "# 领域入门质量评分校准报告",
        "",
        "## 方法",
        "",
        "使用真实模型生成的多领域输出提取五维完整度特征，并从每个真实输出构造中度缺失、严重缺失和双维度缺失样本。",
        "独立验收标签要求五个维度均不低于 0.45，且五维平均值不低于 0.72。",
        "候选权重以 5 分为步长、总和固定为 100，重试阈值在 60 至 90 之间搜索，以平衡准确率为主指标。",
        "",
        "## 真实样本",
        "",
        "| 领域 | 状态 | 当前分数 | 调用耗时(ms) | token |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for record in records:
        usage = record.get("usage", {})
        lines.append(
            f"| {record['domain']} | {record['status']} | "
            f"{record.get('current_score', '-')} | "
            f"{record.get('duration_ms', '-')} | "
            f"{usage.get('total_tokens', '-')} |"
        )

    lines.extend(
        [
            "",
            "## 校准结果",
            "",
            f"- 有效真实样本：{len(valid_records)}",
            f"- 扩展标注样本：{len(samples)}",
            (
                "- 校准前策略："
                f"{BASELINE_POLICY.weights()}，阈值 "
                f"{BASELINE_POLICY.retry_threshold}"
            ),
            (
                "- 校准前策略指标："
                f"balanced_accuracy={baseline_metrics[0]:.4f}，"
                f"sensitivity={baseline_metrics[1]:.4f}，"
                f"specificity={baseline_metrics[2]:.4f}"
            ),
            (
                "- 当前已应用策略："
                f"{CALIBRATED_POLICY.weights()}，阈值 "
                f"{CALIBRATED_POLICY.retry_threshold}"
            ),
            (
                "- 当前已应用策略指标："
                f"balanced_accuracy={current_metrics[0]:.4f}，"
                f"sensitivity={current_metrics[1]:.4f}，"
                f"specificity={current_metrics[2]:.4f}"
            ),
            (
                "- 推荐策略："
                f"{recommended.weights()}，阈值 {recommended.retry_threshold}"
            ),
            (
                "- 推荐策略指标："
                f"balanced_accuracy={recommended_metrics[0]:.4f}，"
                f"sensitivity={recommended_metrics[1]:.4f}，"
                f"specificity={recommended_metrics[2]:.4f}"
            ),
            "",
            "## 结论边界",
            "",
            "本报告校准的是结构化内容完整度，不评估事实准确性。扩展样本来自真实输出的受控降级，适合工程阈值初调，但不能替代团队人工标注。",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domains", nargs="+", default=DEFAULT_DOMAINS)
    parser.add_argument(
        "--samples",
        type=Path,
        default=Path("docs/calibration/domain-onboarding-real-samples.jsonl"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("docs/calibration/domain-onboarding-quality-calibration.md"),
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--calibrate-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.calibrate_only:
        records = read_jsonl(args.samples)
    else:
        records = collect_real_samples(
            args.domains,
            timeout=args.timeout,
            workers=max(1, args.workers),
        )
        write_jsonl(args.samples, records)

    samples = build_calibration_samples(records)
    recommended, metrics = calibrate_policy(samples)
    write_report(args.report, records, samples, recommended, metrics)
    print(
        json.dumps(
            {
                "weights": recommended.weights(),
                "retry_threshold": recommended.retry_threshold,
                "balanced_accuracy": round(metrics[0], 4),
                "sensitivity": round(metrics[1], 4),
                "specificity": round(metrics[2], 4),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
