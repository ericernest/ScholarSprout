"""Tests for calibrated quality policy and metrics exposure."""

from __future__ import annotations

import unittest
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated",
)
from fastapi.testclient import TestClient

from gateway.app import app
from handlers.domain_onboarding_metrics import (
    DomainOnboardingMetrics,
    DomainOnboardingRequestTrace,
)
from handlers.domain_onboarding_quality import (
    CALIBRATED_POLICY,
    QualityFeatureVector,
)
from runtime.agent_runner import TokenUsage
from scripts.calibrate_domain_onboarding_quality import (
    build_calibration_samples,
    calibrate_policy,
)


class CalibratedQualityPolicyTests(unittest.TestCase):
    def test_calibrated_policy_is_locked_to_real_sample_result(self) -> None:
        self.assertEqual(
            CALIBRATED_POLICY.weights(),
            {
                "domain_summary": 15,
                "prerequisites": 20,
                "development_stages": 25,
                "current_landscape": 15,
                "learning_path": 25,
            },
        )
        self.assertEqual(CALIBRATED_POLICY.retry_threshold, 89)
        self.assertEqual(sum(CALIBRATED_POLICY.weights().values()), 100)

    def test_complete_feature_vector_scores_100(self) -> None:
        features = QualityFeatureVector(
            domain_summary=1.0,
            prerequisites=1.0,
            development_stages=1.0,
            current_landscape=1.0,
            learning_path=1.0,
        )

        self.assertEqual(features.score(CALIBRATED_POLICY), 100)

    def test_calibration_search_recovers_applied_policy(self) -> None:
        record = {
            "domain": "真实样本",
            "status": "ok",
            "features": {
                "domain_summary": 1.0,
                "prerequisites": 1.0,
                "development_stages": 1.0,
                "current_landscape": 1.0,
                "learning_path": 1.0,
            },
        }
        samples = build_calibration_samples([record])

        policy, metrics = calibrate_policy(samples)

        self.assertEqual(policy, CALIBRATED_POLICY)
        self.assertEqual(metrics, (1.0, 1.0, 1.0))


class DomainOnboardingMetricsEndpointTests(unittest.TestCase):
    def test_metrics_endpoint_returns_aggregate_snapshot(self) -> None:
        metrics = DomainOnboardingMetrics()
        metrics.record(
            DomainOnboardingRequestTrace(
                status="ok",
                total_duration_ms=120.0,
                first_call_duration_ms=80.0,
                retry_call_duration_ms=40.0,
                first_model_calls=1,
                retry_model_calls=1,
                first_usage=TokenUsage(100, 50, 150, True),
                retry_usage=TokenUsage(60, 40, 100, True),
                first_score=70,
                final_score=92,
                retry_status="improved",
            )
        )
        app.state.domain_onboarding_metrics = metrics

        response = TestClient(app).get("/metrics/domain_onboarding")

        self.assertEqual(response.status_code, 200)
        content = response.json()
        self.assertEqual(content["requests_total"], 1)
        self.assertEqual(content["retry"]["rate"], 1.0)
        self.assertEqual(content["retry"]["improvement_rate"], 1.0)
        self.assertEqual(content["extra_call_cost"]["total_tokens"], 100)
        self.assertTrue(content["extra_call_cost"]["usage_reported"])
        self.assertFalse(content["extra_call_cost"]["pricing_configured"])
        self.assertIsNone(content["extra_call_cost"]["estimated_cost"])


if __name__ == "__main__":
    unittest.main()
