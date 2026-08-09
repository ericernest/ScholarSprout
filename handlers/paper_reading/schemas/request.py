"""论文精读统一请求体 Pydantic 模型。

采用单一 POST /paper_reading 端点 + action 字段区分操作的协议设计。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PaperReadingRequest(BaseModel):
    """论文精读统一请求体。

    所有 paper_reading action 共用此请求模型，
    通过 action 字段路由到不同的子处理器。
    """

    action: Literal[
        "search_paper",        # 搜索论文
        "upload_paper",        # 上传 PDF（base64 编码或 URL）
        "start_reading",       # 开始/继续阅读
        "pause_reading",       # 暂停阅读（自动保存 checkpoint）
        "resume_reading",      # 恢复阅读（还原最后 checkpoint）
        "fork",                # 创建分支探索子会话
        "merge",               # 合并分支探索成果
        "load_skill",          # 手动加载 Skill
        "unload_skill",        # 手动卸载 Skill
        "get_session_state",   # 获取会话完整状态
        "get_progress",        # 获取阅读进度
        "get_paper_detail",    # 获取论文完整元数据和章节正文
        "regenerate_reading_map",  # 重新生成导读地图与智能索引
    ] = "start_reading"

    # ── 通用字段 ──
    session_id: str = Field(
        default="",
        description="会话 ID（首次请求为空字符串，系统自动创建）",
    )
    paper_id: str = Field(
        default="",
        description="论文内部 ID",
    )
    content: str = Field(
        default="",
        description="用户输入的消息/指令/问题",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="附加元数据，透传到 ChannelMessage.metadata",
    )

    # ── search_paper action 专用字段 ──
    search_query: str = Field(
        default="",
        description="论文搜索关键词",
    )
    search_source: Literal["arxiv", "semantic_scholar", "all"] = Field(
        default="all",
        description="论文搜索来源",
    )
    search_max_results: int = Field(
        default=10,
        ge=1,
        le=50,
        description="最大搜索结果数",
    )

    # ── upload_paper action 专用字段 ──
    pdf_url: str = Field(
        default="",
        description="PDF 文件的 URL（arxiv PDF URL 或直接链接）",
    )
    pdf_data: str = Field(
        default="",
        description="PDF 文件的 Base64 编码数据（上传本地文件时用）",
    )

    # ── start_reading / continue_reading 专用字段 ──
    target_section: str = Field(
        default="",
        description="指定阅读的章节 ID（如 'sec:3.2'），空字符串表示从当前进度继续",
    )
    skip_to_next: bool = Field(
        default=False,
        description="是否跳过当前章节，直接到下一章节",
    )

    # ── fork action 专用字段 ──
    fork_context: str = Field(
        default="",
        description="Fork 时用户关注的上下文（公式引用/段落引用/概念名）",
    )
    fork_skills: list[str] = Field(
        default_factory=list,
        description="Fork 子会话要加载的 Skill ID 列表",
    )
    fork_question: str = Field(
        default="",
        description="Fork 时用户的具体问题",
    )

    # ── merge action 专用字段 ──
    merge_session_id: str = Field(
        default="",
        description="要合并回主会话的子会话 ID",
    )

    # ── load_skill / unload_skill action 专用字段 ──
    skill_ids: list[str] = Field(
        default_factory=list,
        description="要加载/卸载的 Skill ID 列表",
    )
