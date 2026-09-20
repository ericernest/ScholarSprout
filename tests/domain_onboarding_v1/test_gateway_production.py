from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from config.schema import OpenAIClientConfig
from gateway.app import app, configure_domain_onboarding_runtime
from handlers.domain_onboarding.metrics import (
    DomainOnboardingMetrics,
    DomainOnboardingRequestTrace,
)
from runtime.agent_runner import TokenUsage


class GatewayProductionTests(unittest.TestCase):
    def test_runtime_wires_v1_pipeline_metrics_and_audit(self) -> None:
        state = SimpleNamespace()
        pipeline = object()
        audit = object()
        config = OpenAIClientConfig(
            input_cost_per_million_tokens=1.0,
            output_cost_per_million_tokens=2.0,
        )
        store = MagicMock()
        store.recover_interrupted.return_value = 0
        chat_model = object()
        embedding_model = object()

        with (
            patch("gateway.app.create_default_pipeline", return_value=pipeline) as create_pipeline,
            patch("gateway.app.create_audit_sink_from_env", return_value=audit),
            patch("gateway.app.create_job_store_from_env", return_value=store),
        ):
            configure_domain_onboarding_runtime(
                state,
                chat_model,
                config,
                embedding_model=embedding_model,
                embedding_model_name="configured-embedding",
            )

        self.assertIs(state.domain_onboarding_pipeline, pipeline)
        create_pipeline.assert_called_once_with(
            chat_model,
            embedding_model=embedding_model,
            embedding_model_name="configured-embedding",
        )
        self.assertIs(state.domain_onboarding_audit_sink, audit)
        self.assertIsInstance(state.domain_onboarding_metrics, DomainOnboardingMetrics)
        self.assertIsNotNone(state.domain_onboarding_job_manager)
        state.domain_onboarding_job_manager.close()

    def test_readiness_requires_v1_pipeline_and_observability(self) -> None:
        app.state.model = object()
        app.state.domain_onboarding_pipeline = object()
        app.state.domain_onboarding_metrics = DomainOnboardingMetrics()
        app.state.domain_onboarding_audit_sink = object()
        app.state.domain_onboarding_job_manager = object()
        app.state.research_storage = object()

        response = TestClient(app).get("/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")

    def test_prometheus_and_total_cost_are_exposed(self) -> None:
        metrics = DomainOnboardingMetrics(
            input_cost_per_million_tokens=1.0,
            output_cost_per_million_tokens=2.0,
        )
        metrics.record(
            DomainOnboardingRequestTrace(
                status="ok",
                total_duration_ms=120.0,
                first_model_calls=1,
                first_usage=TokenUsage(1_000_000, 500_000, 1_500_000, True),
                evidence_validation_modes={"multilingual_embedding": 2},
            )
        )
        app.state.domain_onboarding_metrics = metrics

        json_response = TestClient(app).get("/metrics/domain_onboarding")
        prometheus_response = TestClient(app).get(
            "/metrics/domain_onboarding/prometheus"
        )

        self.assertEqual(json_response.json()["model_cost"]["estimated_total_usd"], 2.0)
        self.assertEqual(
            json_response.json()["evidence"]["validation_modes"],
            {"multilingual_embedding": 2},
        )
        self.assertIn(
            'scholarsprout_domain_onboarding_requests_total{status="ok"} 1',
            prometheus_response.text,
        )
        self.assertIn("scholarsprout_domain_onboarding_estimated_cost_usd_total 2.0", prometheus_response.text)

    def test_container_deployment_uses_readiness_and_non_root_user(self) -> None:
        root = Path(__file__).resolve().parents[2]
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        compose = (root / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("USER scholarsprout", dockerfile)
        self.assertIn("/ready", dockerfile)
        self.assertIn("domain_onboarding_audit", compose)
        self.assertIn("DOMAIN_ONBOARDING_JOB_DB", compose)
        self.assertIn("domain_onboarding_jobs", compose)
        self.assertNotIn("openai_api_key", compose.lower())


if __name__ == "__main__":
    unittest.main()
