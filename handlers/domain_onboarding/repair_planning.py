"""将质量问题转换为可审计的定向修复计划。"""

from __future__ import annotations

from .schemas import ContentQuality, QualityIssue, RepairActionRecord, RepairPlan, stable_id


class RepairPlanner:
    default_llm_issue_types = {
        "missing_coverage",
        "weak_development_stage",
        "beginner_mismatch",
        "structure_error",
        "missing_evidence",
        "unsupported_claim",
    }

    def __init__(
        self,
        llm_issue_types: list[str] | set[str] | None = None,
        max_llm_issues: int = 6,
    ) -> None:
        self.llm_issue_types = set(llm_issue_types or self.default_llm_issue_types)
        self.max_llm_issues = max(1, int(max_llm_issues))

    def plan(self, quality: ContentQuality, *, max_content_repairs: int) -> RepairPlan:
        if not quality.issues:
            return RepairPlan()

        actions = [self._action("code", "normalize_output", quality.issues)]
        llm_issues = [
            issue for issue in quality.issues if issue.issue_type in self.llm_issue_types
        ]
        severity_order = {"critical": 0, "error": 1, "warning": 2, "info": 3}
        llm_issues.sort(
            key=lambda issue: (
                not issue.hard_gate,
                severity_order.get(issue.severity, 4),
            )
        )
        llm_issues = llm_issues[: self.max_llm_issues]
        if llm_issues:
            action = self._action("llm", "targeted_content_repair", llm_issues)
            if max_content_repairs == 0:
                action.status = "skipped"
                action.error = "content repair is disabled by configuration"
            actions.append(action)
        return RepairPlan(actions=actions)

    @staticmethod
    def _action(
        action_type: str,
        name: str,
        issues: list[QualityIssue],
    ) -> RepairActionRecord:
        issue_ids = list(dict.fromkeys(str(issue.issue_id) for issue in issues))
        target_paths = list(dict.fromkeys(issue.target_path for issue in issues))
        identity = f"{action_type}:{name}:{'|'.join(issue_ids)}"
        return RepairActionRecord(
            action_id=stable_id("repair", identity),
            action_type=action_type,
            status="planned",
            issue_ids=issue_ids,
            target_paths=target_paths,
        )
