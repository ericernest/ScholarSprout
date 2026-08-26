"""论文精读统一响应体 Pydantic 模型。

所有 paper_reading action 共用此响应模型，
status 字段区分成功/错误/进行中等状态。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── 顶层响应 ──

class PaperReadingResponse(BaseModel):
    """论文精读统一响应体。

    对应框架 handler 契约: (ChannelMessage, app_state) -> dict。
    """

    status: Literal["ok", "error", "pending", "fork_active"] = "ok"
    action: str = ""
    message: str = Field(
        default="",
        description="人类可读的状态消息",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="核心返回数据，不同 action 返回不同的 data 结构",
    )
    session: SessionState | None = Field(
        default=None,
        description="当前会话状态（read/write action 时提供）",
    )
    progress: ReadingProgress | None = Field(
        default=None,
        description="阅读进度信息",
    )
    skill_outputs: list[SkillOutput] = Field(
        default_factory=list,
        description="已激活 Skill 的输出结果",
    )
    error: str = Field(
        default="",
        description="错误详情（status='error' 时填充）",
    )


# ── 会话状态 ──

class SessionState(BaseModel):
    """论文阅读会话的当前状态。"""

    session_id: str = ""
    paper_id: str = ""
    paper_title: str = ""
    state: Literal["active", "paused", "completed"] = "active"
    current_section: str | None = Field(
        default=None,
        description="当前正在阅读的章节 ID",
    )
    paragraph_index: int = Field(
        default=0,
        description="当前阅读到的段落索引",
    )
    active_skills: list[str] = Field(
        default_factory=list,
        description="当前已激活的 Skill ID 列表",
    )
    parent_session_id: str | None = Field(
        default=None,
        description="父会话 ID（fork 子会话时有值）",
    )
    fork_sessions: list[str] = Field(
        default_factory=list,
        description="子 fork 会话 ID 列表",
    )
    created_at: str = ""
    updated_at: str = ""


# ── 阅读进度 ──

class ReadingProgress(BaseModel):
    """按章节追踪的阅读进度。"""

    total_sections: int = Field(default=0, ge=0)
    completed_sections: int = Field(default=0, ge=0)
    percentage: float = Field(default=0.0, ge=0.0, le=100.0)
    current_section: str = ""
    current_paragraph: int = 0
    sections: list[SectionProgress] = Field(default_factory=list)


class SectionProgress(BaseModel):
    """单个章节的阅读进度。"""

    section_id: str = ""
    title: str = ""
    level: int = Field(default=1, ge=1, le=6)
    status: Literal["not_started", "reading", "completed"] = "not_started"
    insights_created: int = Field(default=0, ge=0)


# ── Skill 输出 ──

class SkillOutput(BaseModel):
    """单个 Skill 的输出结果。"""

    skill_id: str = ""
    skill_name: str = ""
    trigger: Literal["auto", "manual", "fork"] = "auto"
    output_type: str = Field(
        default="",
        description="各 Skill 定义的输出类型标识",
    )
    content: dict[str, Any] = Field(
        default_factory=dict,
        description="结构化的 Skill 输出（JSON）",
    )
    rendered: str = Field(
        default="",
        description="Markdown 渲染版本，供前端直接展示",
    )


# ── search_paper action 的 data 结构 ──

class PaperSearchResult(BaseModel):
    """论文搜索结果。"""

    paper_id: str = ""
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    source: str = ""
    url: str = ""
    pdf_url: str = ""
    citation_count: int | None = None
