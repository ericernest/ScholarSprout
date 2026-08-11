from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from gateway.app import app


STATIC_DIR = Path(__file__).resolve().parents[1] / "gateway" / "static"


class DomainOnboardingFrontendTests(unittest.TestCase):
    def test_workspace_route_and_assets_exist(self) -> None:
        response = TestClient(app).get("/app/domain-onboarding")
        package_config = (STATIC_DIR.parents[1] / "pyproject.toml").read_text(encoding="utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("领域学习工作台", response.text)
        self.assertTrue((STATIC_DIR / "domain-onboarding" / "app.js").is_file())
        self.assertTrue((STATIC_DIR / "domain-onboarding" / "styles.css").is_file())
        self.assertIn('"static/domain-onboarding/*"', package_config)

    def test_chat_submits_background_job_and_returns_workspace_card(self) -> None:
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn('fetch("/domain_onboarding/jobs"', script)
        self.assertIn("appendDomainOnboardingCard", script)
        self.assertIn("/app/domain-onboarding?task_id=", script)
        self.assertNotIn("window.location.assign", script)
        self.assertIn('watchDomainOnboardingCard(job.task_id, job.access_token || "")', script)
        self.assertIn("restoreDomainOnboardingCard()", script)
        self.assertIn('value?.schema_version === "1.9"', script)
        self.assertIn("getPendingDomainRequestId", script)
        self.assertIn("findReusableDomainJob", script)
        self.assertIn("你可以随时进入工作台查看进度", script)
        self.assertNotIn('initialMode === "paper_reading"', script)

    def test_domain_workspace_restore_is_scoped_to_its_conversation(self) -> None:
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        restore = script.split("function restoreDomainOnboardingCard()", 1)[1].split(
            "function loadDomainWorkspace()", 1
        )[0]

        self.assertIn("saved.request?.session_id !== sessionId", restore)

    def test_workspace_consumes_snapshot_sse_and_paper_import(self) -> None:
        html = (STATIC_DIR / "domain-onboarding" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (STATIC_DIR / "domain-onboarding" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="topbar-retry-button"', html)
        self.assertIn("new EventSource", script)
        self.assertIn("/domain_onboarding/jobs/", script)
        self.assertIn('"llm_delta"', script)
        self.assertIn('"stage_plan_ready"', script)
        self.assertIn('"stage_retrieval_ready"', script)
        self.assertIn('"final_quality_ready"', script)
        self.assertIn("STREAM_STAGE_LABELS[state.activeLLMStage]", script)
        self.assertIn('}/retry`', script)
        self.assertIn("terminal && Boolean(snapshot.retryable)", script)
        self.assertIn('failed: "生成失败，可重试"', script)
        self.assertIn("replace_paths", script)
        self.assertIn("return { ...partial, ...result };", script)
        self.assertIn("formatPercentScore(paper.final_score)", script)
        self.assertIn("paperGuidance(paper)", script)
        self.assertIn("sectionStatusCopy", script)
        self.assertIn("待完善", script)
        self.assertIn("待生成", script)
        self.assertIn('action: "upload_paper"', script)
        self.assertIn('profileItem("适用对象", "普通科研新手")', script)
        self.assertIn('profileItem("路线类型", "标准学习路线")', script)
        self.assertIn("detailDescription", script)
        self.assertIn("detailPaperIds", script)
        self.assertIn('detailList("典型研究任务", item.typical_tasks)', script)
        self.assertIn('detailList("数据集与基准", item.datasets_and_benchmarks)', script)
        self.assertIn("item.starter_project", script)
        self.assertIn("论文依据 ·", script)
        self.assertIn("detail-evidence", script)
        self.assertIn('domain_onboarding_workspace_v1_9', script)
        self.assertIn('domain_onboarding_workspace_v1_5', script)
        self.assertIn("pendingRequestId", script)
        self.assertIn("development_stage_plans", script)
        self.assertIn("function mergeLandscapeItems", script)
        self.assertIn("if (!name || byName.has(name)) continue", script)
        self.assertIn("data.current_landscape?.problem_details", script)
        self.assertIn("data.current_landscape?.subdirection_details", script)
        self.assertNotIn("landscape.problem_details ||", script)
        self.assertNotIn("landscape.subdirection_details ||", script)
        self.assertIn("STANDARD ROUTE", html)
        self.assertNotIn("PERSONAL ROUTE", html)
        self.assertNotIn("新建领域", html)
        self.assertNotIn("正在生成个性化学习路线", script)
        self.assertNotIn("学习者画像已完成", script)

    def test_paper_actions_download_pdf_without_opening_metadata_page(self) -> None:
        html = (STATIC_DIR / "domain-onboarding" / "index.html").read_text(
            encoding="utf-8"
        )
        script = (STATIC_DIR / "domain-onboarding" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("function paperPdfUrl", script)
        self.assertIn("async function downloadDomainPaper", script)
        self.assertIn('abstract: paper.abstract || ""', script)
        self.assertIn('source_url: paper.url || pdfUrl', script)
        self.assertIn("查看 PDF 原文", script)
        self.assertNotIn('window.open(paper.url', script)
        self.assertNotIn('id="quality"', html)
        self.assertNotIn("renderQuality(data)", script)

    def test_protected_job_requests_propagate_access_token(self) -> None:
        chat_script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        workspace_script = (STATIC_DIR / "domain-onboarding" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("watchDomainOnboardingCard(saved.task_id, saved.access_token", chat_script)
        self.assertIn("access_token: payload.access_token", chat_script)
        self.assertIn("Authorization: `Bearer ${accessToken}`", chat_script)

        self.assertIn('accessToken: ""', workspace_script)
        self.assertIn("jobAuthHeaders({ Accept:", workspace_script)
        self.assertIn('query.set("access_token", state.accessToken)', workspace_script)
        self.assertIn("headers: jobAuthHeaders()", workspace_script)
        self.assertIn("access_token: state.accessToken", workspace_script)


if __name__ == "__main__":
    unittest.main()
