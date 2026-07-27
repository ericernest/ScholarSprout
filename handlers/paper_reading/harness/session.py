"""会话管理、Checkpoint 保存/恢复、阅读进度追踪。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    """阅读进度快照。"""

    checkpoint_id: str = field(default_factory=lambda: str(uuid4()))
    position: dict = field(default_factory=dict)
    active_skills: list[str] = field(default_factory=list)
    kg_state_snapshot: dict = field(default_factory=dict)
    conversation_history: list[dict] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class ReadingSession:
    """论文阅读会话。"""

    session_id: str = field(default_factory=lambda: str(uuid4()))
    paper_id: str = ""
    paper_title: str = ""
    user_id: str = "default"
    state: Literal["active", "paused", "completed"] = "active"
    checkpoints: list[Checkpoint] = field(default_factory=list)
    progress: dict = field(default_factory=dict)
    active_skills: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    # Fork 相关
    parent_session_id: str | None = None
    fork_sessions: list[str] = field(default_factory=list)
    fork_context: str = ""


class SessionManager:
    """会话生命周期管理器。

    功能:
    - 创建/获取/暂停/恢复/完成 会话
    - 阅读进度更新
    - 自动保存 Checkpoint
    - 内存缓存 + 文件持久化
    """

    def __init__(self, storage=None) -> None:
        """初始化会话管理器。

        Args:
            storage: PaperReadingStorage 实例，不传则仅内存存储
        """
        self._storage = storage
        self._active_sessions: dict[str, ReadingSession] = {}

    # ── 创建与获取 ──

    def create_session(
        self,
        paper_id: str = "",
        paper_title: str = "",
        user_id: str = "default",
        parent_session_id: str | None = None,
        fork_context: str = "",
    ) -> ReadingSession:
        """创建新会话。

        Args:
            paper_id: 关联的论文 ID
            paper_title: 论文标题
            user_id: 用户标识
            parent_session_id: fork 父会话 ID
            fork_context: fork 上下文

        Returns:
            新创建的 ReadingSession 实例
        """
        session = ReadingSession(
            paper_id=paper_id,
            paper_title=paper_title,
            user_id=user_id,
            parent_session_id=parent_session_id,
            fork_context=fork_context,
        )
        self._active_sessions[session.session_id] = session
        self._persist(session)
        logger.info("Session created: %s (paper: %s)", session.session_id, paper_id)
        return session

    def get_session(self, session_id: str) -> ReadingSession | None:
        """获取会话（内存缓存优先，回退到文件加载）。"""
        if session_id in self._active_sessions:
            return self._active_sessions[session_id]
        if self._storage:
            data = self._storage.load_session(session_id)
            if data:
                session = self._from_dict(data)
                self._active_sessions[session_id] = session
                return session
        return None

    def _from_dict(self, data: dict) -> ReadingSession:
        """从字典反序列化会话（兼容不同来源的数据格式）。"""
        checkpoints = [
            Checkpoint(**cp) if isinstance(cp, dict) else cp
            for cp in data.pop("checkpoints", [])
        ]
        data.pop("_last_checkpoint", None)  # 清理内部字段
        return ReadingSession(checkpoints=checkpoints, **data)

    # ── 暂停与恢复 ──

    def pause(self, session_id: str) -> ReadingSession | None:
        """暂停阅读，自动保存 Checkpoint。

        Returns:
            更新后的会话，如果会话不存在返回 None
        """
        session = self._active_sessions.get(session_id)
        if session is None:
            session = self.get_session(session_id)
            if session is None:
                return None

        # 创建快照
        checkpoint = Checkpoint(
            position=session.progress.get("current_position", {}),
            active_skills=list(session.active_skills),
            conversation_history=[],  # 由 handler 负责填充
        )
        session.checkpoints.append(checkpoint)
        session.state = "paused"
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist(session)
        logger.info("Session paused: %s (checkpoint: %s)", session_id, checkpoint.checkpoint_id)
        return session

    def resume(self, session_id: str) -> ReadingSession | None:
        """恢复阅读。

        Returns:
            已恢复的会话，如果会话不存在返回 None
        """
        session = self.get_session(session_id)
        if session is None:
            return None
        session.state = "active"
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._active_sessions[session_id] = session
        self._persist(session)
        logger.info("Session resumed: %s", session_id)
        return session

    def complete(self, session_id: str) -> ReadingSession | None:
        """标记阅读为完成。"""
        session = self._active_sessions.get(session_id)
        if session is None:
            session = self.get_session(session_id)
            if session is None:
                return None
        session.state = "completed"
        session.progress["percentage"] = 100.0
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist(session)
        return session

    # ── 进度更新 ──

    def update_progress(
        self,
        session_id: str,
        section_id: str,
        paragraph_index: int = 0,
        completed: bool = False,
    ) -> ReadingSession | None:
        """更新阅读进度。

        Args:
            session_id: 会话 ID
            section_id: 当前章节 ID
            paragraph_index: 当前段落索引
            completed: 是否标记该章节为已完成

        Returns:
            更新后的会话
        """
        session = self._active_sessions.get(session_id)
        if session is None:
            return None

        session.progress.setdefault("current_position", {})
        session.progress["current_position"] = {
            "section_id": section_id,
            "paragraph_index": paragraph_index,
        }

        if completed:
            completed_list = session.progress.setdefault("completed_sections", [])
            if section_id not in completed_list:
                completed_list.append(section_id)
            total = session.progress.get("total_sections", 1)
            session.progress["completed_sections"] = completed_list
            session.progress["percentage"] = (
                len(completed_list) / max(total, 1) * 100
            )
            session.progress.setdefault("section_statuses", {})[section_id] = "completed"
        else:
            statuses = session.progress.setdefault("section_statuses", {})
            if statuses.get(section_id) != "completed":
                statuses[section_id] = "reading"

        session.updated_at = datetime.now(timezone.utc).isoformat()
        return session

    def set_total_sections(self, session_id: str, total: int) -> None:
        """设置总章节数（PDF 解析完成后调用）。"""
        session = self._active_sessions.get(session_id)
        if session:
            session.progress["total_sections"] = total

    # ── Skill 管理 ──

    def activate_skills(self, session_id: str, skill_ids: list[str]) -> ReadingSession | None:
        """激活 Skill。"""
        session = self._active_sessions.get(session_id)
        if session is None:
            return None
        for sid in skill_ids:
            if sid not in session.active_skills:
                session.active_skills.append(sid)
        session.updated_at = datetime.now(timezone.utc).isoformat()
        return session

    def deactivate_skills(self, session_id: str, skill_ids: list[str]) -> ReadingSession | None:
        """卸载 Skill。"""
        session = self._active_sessions.get(session_id)
        if session is None:
            return None
        session.active_skills = [s for s in session.active_skills if s not in skill_ids]
        session.updated_at = datetime.now(timezone.utc).isoformat()
        return session

    # ── Checkpoint ──

    def save_checkpoint(
        self,
        session_id: str,
        kg_snapshot: dict | None = None,
        conversation_history: list[dict] | None = None,
    ) -> Checkpoint | None:
        """手动保存 Checkpoint。"""
        session = self._active_sessions.get(session_id)
        if session is None:
            return None
        checkpoint = Checkpoint(
            position=session.progress.get("current_position", {}),
            active_skills=list(session.active_skills),
            kg_state_snapshot=kg_snapshot or {},
            conversation_history=conversation_history or [],
        )
        session.checkpoints.append(checkpoint)
        session.updated_at = datetime.now(timezone.utc).isoformat()
        self._persist(session)
        return checkpoint

    def get_latest_checkpoint(self, session_id: str) -> Checkpoint | None:
        """获取最近的 Checkpoint。"""
        session = self.get_session(session_id)
        if session is None or not session.checkpoints:
            return None
        return session.checkpoints[-1]

    # ── 持久化 ──

    def _persist(self, session: ReadingSession) -> None:
        """持久化到文件存储。"""
        if self._storage:
            try:
                self._storage.save_session(
                    session.session_id,
                    self._to_dict(session),
                )
            except Exception as e:
                logger.error("Failed to persist session %s: %s", session.session_id, e)

    @staticmethod
    def _to_dict(session: ReadingSession) -> dict:
        """序列化会话为字典。"""
        import dataclasses
        data = dataclasses.asdict(session)
        data["checkpoints"] = [
            dataclasses.asdict(cp) if isinstance(cp, Checkpoint) else cp
            for cp in data.get("checkpoints", [])
        ]
        return data

    # ── 批量操作 ──

    def list_sessions(self, paper_id: str | None = None) -> list[dict]:
        """列出所有会话的摘要信息。"""
        if self._storage:
            return self._storage.list_sessions(paper_id=paper_id)
        return [
            self._to_dict(s)
            for s in self._active_sessions.values()
            if paper_id is None or s.paper_id == paper_id
        ]

    def remove_session(self, session_id: str) -> bool:
        """删除会话。"""
        if session_id in self._active_sessions:
            del self._active_sessions[session_id]
        if self._storage:
            return self._storage.delete_session(session_id)
        return True
