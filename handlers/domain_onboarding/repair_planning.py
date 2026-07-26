"""将质量问题转换为可审计的定向修复计划。"""

from __future__ import annotations

from .schemas import ContentQuality, QualityIssue, RepairActionRecord, RepairPlan, stable_id


class RepairPlanner:
    llm_issue_types = {
        "missing_coverage",
        "weak_development_stage",
        "beginner_mismatch",
        "structure_error",
        "missing_evidence",
        "unsupported_claim",
    }

    def plan(self, quality: ContentQuality, *, max_content_repairs: int) -> RepairPlan:
        if not quality.issues:
            return RepairPlan()

        actions = [self._action("code", "normalize_output", quality.issues)]
        llm_issues = [
            issue for issue in quality.issues if issue.issue_type in self.llm_issue_types
        ]
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
