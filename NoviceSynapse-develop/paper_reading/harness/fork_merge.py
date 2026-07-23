"""Fork/Merge 分支探索管理。

docx 定义的 Fork/Merge 流程:
  主阅读流 (Method章节)
    ├── Fork: 用户遇到不理解的公式
    │   ├── 加载 Math Verifier Skill
    │   ├── 专注推导 → 展开多层推导
    │   ├── 用户理解完毕
    │   └── Merge → 返回主流程，KG中新增公式推导子图
    ├── Fork: 用户想了解Baseline的更多细节
    │   ├── 加载 Domain Expert Skill
    │   └── Merge → 返回主流程
    └── 继续主阅读流
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from paper_reading.harness.session import ReadingSession, SessionManager
from paper_reading.kg.engine import KnowledgeGraphEngine

logger = logging.getLogger(__name__)


@dataclass
class MergeResult:
    """Fork 合并结果。"""

    parent_session_id: str = ""
    fork_session_id: str = ""
    kg_updates: list[dict[str, Any]] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    merged_skills: list[str] = field(default_factory=list)
    success: bool = True
    error: str = ""


class ForkMergeManager:
    """Fork/Merge 分支探索管理器。

    功能:
    - 从主阅读流 fork 子会话
    - 在 fork 会话中加载专用 skill
    - 将 fork 成果合并回主会话
    """

    def __init__(
        self,
        session_manager: SessionManager,
        kg_engine: KnowledgeGraphEngine | None = None,
    ) -> None:
        self._session_manager = session_manager
        self._kg_engine = kg_engine

    def create_fork(
        self,
        parent_session_id: str,
        fork_context: str = "",
        fork_skills: list[str] | None = None,
        fork_question: str = "",
        user_id: str = "default",
    ) -> ReadingSession | None:
        """从主阅读流 fork 一个子会话。

        Args:
            parent_session_id: 主会话 ID
            fork_context: fork 时用户关注的上下文（公式/段落引用/概念名）
            fork_skills: fork 会话要激活的 Skill ID 列表
            fork_question: fork 时用户的具体问题
            user_id: 用户标识

        Returns:
            新创建的 fork 子会话，失败返回 None
        """
        parent = self._session_manager.get_session(parent_session_id)
        if parent is None:
            logger.error("Parent session not found: %s", parent_session_id)
            return None

        # 创建子会话（共享 paper_id）
        fork_session = self._session_manager.create_session(
            paper_id=parent.paper_id,
            paper_title=parent.paper_title,
            user_id=user_id,
            parent_session_id=parent_session_id,
            fork_context=fork_context,
        )

        # 设置 fork 专用 skills
        if fork_skills:
            fork_session.active_skills = list(fork_skills)

        # 记录到父会话
        parent.fork_sessions.append(fork_session.session_id)
        self._session_manager._persist(parent)
        self._session_manager._persist(fork_session)

        logger.info(
            "Fork created: %s (parent: %s, context: %s, skills: %s)",
            fork_session.session_id, parent_session_id, fork_context[:50], fork_skills,
        )
        return fork_session

    def merge_fork(self, fork_session_id: str) -> MergeResult:
        """将 fork 会话的成果合并回主阅读流。

        Args:
            fork_session_id: 要合并的 fork 子会话 ID

        Returns:
            MergeResult 包含合并详情
        """
        fork = self._session_manager.get_session(fork_session_id)
        if fork is None:
            return MergeResult(
                fork_session_id=fork_session_id,
                success=False,
                error=f"Fork session not found: {fork_session_id}",
            )

        if fork.parent_session_id is None:
            return MergeResult(
                fork_session_id=fork_session_id,
                success=False,
                error="Fork session has no parent",
            )

        parent = self._session_manager.get_session(fork.parent_session_id)
        if parent is None:
            return MergeResult(
                parent_session_id=fork.parent_session_id,
                fork_session_id=fork_session_id,
                success=False,
                error=f"Parent session not found: {fork.parent_session_id}",
            )

        # 收集 fork 中的有效 Skill 输出
        key_findings = self._extract_key_findings(fork)

        # 合并活跃 skills
        for sid in fork.active_skills:
            if sid not in parent.active_skills:
                parent.active_skills.append(sid)

        # 标记 fork 为已完成
        fork.state = "completed"
        self._session_manager._persist(fork)
        self._session_manager._persist(parent)

        result = MergeResult(
            parent_session_id=parent.session_id,
            fork_session_id=fork.session_id,
            key_findings=key_findings,
            merged_skills=list(fork.active_skills),
        )

        logger.info(
            "Fork merged: %s → %s, %d findings",
            fork_session_id, parent.session_id, len(key_findings),
        )
        return result

    def _extract_key_findings(self, session: ReadingSession) -> list[str]:
        """从 fork 会话中提取关键发现。"""
        findings = []
        if session.fork_context:
            findings.append(f"Fork 上下文: {session.fork_context}")
        if session.checkpoints:
            last_cp = session.checkpoints[-1]
            findings.append(f"使用 Skill: {', '.join(last_cp.active_skills)}")
        return findings

    def list_forks(self, parent_session_id: str) -> list[dict[str, Any]]:
        """列出主会话的所有 fork 子会话。"""
        parent = self._session_manager.get_session(parent_session_id)
        if parent is None:
            return []

        forks = []
        for fid in parent.fork_sessions:
            fork = self._session_manager.get_session(fid)
            if fork:
                forks.append({
                    "session_id": fork.session_id,
                    "state": fork.state,
                    "context": fork.fork_context,
                    "active_skills": fork.active_skills,
                    "created_at": fork.created_at,
                })

        return forks
