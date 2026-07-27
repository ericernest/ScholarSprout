"""阅读进度追踪工具。"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Literal


def build_initial_progress(total_sections: int = 0) -> dict:
    """构建初始进度状态。

    Args:
        total_sections: 论文总章节数（PDF 解析后可知）

    Returns:
        标准进度字典
    """
    return {
        "total_sections": total_sections,
        "completed_sections": [],
        "percentage": 0.0,
        "current_position": {"section_id": "", "paragraph_index": 0},
        "section_statuses": {},
        "started_at": "",
        "last_activity_at": "",
    }


def update_section_status(
    progress: dict,
    section_id: str,
    status: Literal["not_started", "reading", "completed"],
) -> dict:
    """更新章节状态。

    Args:
        progress: 进度字典
        section_id: 章节 ID
        status: 新状态

    Returns:
        更新后的进度字典
    """
    progress.setdefault("section_statuses", {})
    progress["section_statuses"][section_id] = status
    progress["last_activity_at"] = datetime.now(timezone.utc).isoformat()

    # 标记阅读开始时间
    if not progress.get("started_at"):
        progress["started_at"] = datetime.now(timezone.utc).isoformat()

    # 如果完成，更新已完成列表和百分比
    if status == "completed":
        completed = progress.setdefault("completed_sections", [])
        if section_id not in completed:
            completed.append(section_id)
        total = progress.get("total_sections", 1) or 1
        progress["percentage"] = min(100.0, len(completed) / total * 100)

    return progress


def format_progress_message(progress: dict) -> str:
    """格式化为人类可读的进度消息。"""
    pct = progress.get("percentage", 0)
    completed = len(progress.get("completed_sections", []))
    total = progress.get("total_sections", 0)
    current = progress.get("current_position", {}).get("section_id", "?")

    bar_width = 20
    filled = int(bar_width * pct / 100)
    bar = "█" * filled + "░" * (bar_width - filled)

    return (
        f"📖 阅读进度: {bar} {pct:.0f}%\n"
        f"   已完成: {completed}/{total} 章节\n"
        f"   当前位置: {current}"
    )


def calculate_progress_stats(progress: dict) -> dict:
    """计算进度统计指标。"""
    total = progress.get("total_sections", 0) or 1
    completed = len(progress.get("completed_sections", []))
    pct = progress.get("percentage", 0)

    return {
        "total_sections": total,
        "completed_sections": completed,
        "remaining_sections": max(0, total - completed),
        "percentage": round(pct, 1),
        "is_complete": pct >= 100.0,
        "is_started": progress.get("started_at", "") != "",
        "bar_visual": "█" * int(20 * pct / 100) + "░" * max(0, 20 - int(20 * pct / 100)),
    }
