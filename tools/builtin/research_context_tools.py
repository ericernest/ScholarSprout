"""On-demand access to research workspaces linked to the current chat."""

from __future__ import annotations

import json
from typing import Any

from storage.catalog import ResearchCatalog
from storage.local_store import LocalResearchStore
from tools.base import BaseTool, ToolSpec


def _runtime(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    context = arguments.get("_runtime_context")
    if not isinstance(context, dict):
        return "", {}
    active = context.get("active_context")
    return str(context.get("conversation_id") or ""), active if isinstance(active, dict) else {}


def _requested_id(arguments: dict[str, Any], active: dict[str, Any], kind: str) -> str:
    explicit = str(arguments.get("id") or arguments.get("reading_session_id") or "").strip()
    if explicit:
        return explicit
    if str(active.get("kind") or "") == kind:
        return str(active.get("id") or "").strip()
    return ""


class _ResearchTool(BaseTool):
    def __init__(self, store: LocalResearchStore) -> None:
        self.store = store

    def _reading(self, arguments: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        conversation_id, active = _runtime(arguments)
        reading_id = _requested_id(arguments, active, "paper_reading")
        if not conversation_id:
            return None, "缺少当前主会话上下文"
        if not reading_id:
            return None, "请先选择当前讨论的论文，或提供 reading_session_id"
        row = self.store.get_reading_session_record(reading_id)
        if row is None:
            return None, "论文精读会话不存在"
        if row["conversation_id"] != conversation_id:
            return None, "该论文精读不属于当前主会话"
        return row, None


class SearchPaperReadingDialogueTool(_ResearchTool):
    def __init__(self, store: LocalResearchStore) -> None:
        super().__init__(store)
        self.spec = ToolSpec(
            name="search_paper_reading_dialogue",
            description="按需搜索当前主会话中某篇论文的精读右栏对话。不会默认加载完整历史。",
            parameters={
                "type": "object",
                "properties": {
                    "reading_session_id": {"type": "string", "description": "论文精读会话 ID；已选择当前论文时可省略"},
                    "query": {"type": "string", "description": "要检索的关键词；留空返回最近消息"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                },
                "required": [],
            },
        )

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        reading, error = self._reading(arguments)
        if error:
            return {"error": error, "messages": []}
        query = str(arguments.get("query") or "").strip()
        limit = max(1, min(int(arguments.get("limit") or 8), 20))
        with self.store._connection() as connection:
            params: list[Any] = [reading["dialogue_conversation_id"]]
            clause = ""
            if query:
                clause = "AND content LIKE ?"
                params.append(f"%{query}%")
            params.append(limit)
            rows = connection.execute(
                f"""SELECT role, content, created_at FROM messages
                    WHERE conversation_id = ? {clause}
                    ORDER BY sequence_number DESC LIMIT ?""",
                params,
            ).fetchall()
        return {
            "reading_session_id": reading["reading_session_id"],
            "query": query,
            "messages": [dict(row) for row in reversed(rows)],
        }


class GetPaperReadingContextTool(_ResearchTool):
    def __init__(self, store: LocalResearchStore) -> None:
        super().__init__(store)
        self.spec = ToolSpec(
            name="get_paper_reading_context",
            description="按需读取当前主会话中某篇论文的元数据、阅读进度和已有分析块。",
            parameters={
                "type": "object",
                "properties": {
                    "reading_session_id": {"type": "string", "description": "论文精读会话 ID；已选择当前论文时可省略"},
                    "block_limit": {"type": "integer", "minimum": 0, "maximum": 12, "default": 6},
                },
                "required": [],
            },
        )

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        reading, error = self._reading(arguments)
        if error:
            return {"error": error}
        block_limit = max(0, min(int(arguments.get("block_limit") or 6), 12))
        with self.store._connection() as connection:
            paper = connection.execute(
                """SELECT title, authors_json, abstract, publication_year, venue, doi, arxiv_id
                   FROM papers WHERE paper_id = ?""",
                (reading["paper_id"],),
            ).fetchone()
            blocks = connection.execute(
                """SELECT block_type, rendered_text, content_json, created_at
                   FROM paper_reading_blocks WHERE reading_session_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (reading["reading_session_id"], block_limit),
            ).fetchall()
        return {
            "reading_session_id": reading["reading_session_id"],
            "paper_id": reading["paper_id"],
            "paper": {
                "title": paper["title"],
                "authors": json.loads(paper["authors_json"] or "[]"),
                "abstract": paper["abstract"] or "",
                "publication_year": paper["publication_year"],
                "venue": paper["venue"] or "",
                "doi": paper["doi"] or "",
                "arxiv_id": paper["arxiv_id"] or "",
            } if paper else {},
            "state": reading["state"],
            "current_section_id": reading["current_section_id"],
            "progress": reading["progress"],
            "analysis_blocks": [
                {
                    "block_type": row["block_type"],
                    "text": row["rendered_text"] or "",
                    "content": json.loads(row["content_json"] or "{}"),
                    "created_at": row["created_at"],
                }
                for row in reversed(blocks)
            ],
        }


class GetDomainOnboardingResultTool(_ResearchTool):
    def __init__(self, store: LocalResearchStore) -> None:
        super().__init__(store)
        self.spec = ToolSpec(
            name="get_domain_onboarding_result",
            description="按需读取当前主会话关联的领域入门结构化结果。领域入门本身不使用聊天记忆。",
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "领域入门 artifact ID；已选择当前领域时可省略"}
                },
                "required": [],
            },
        )

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        conversation_id, active = _runtime(arguments)
        artifact_id = _requested_id(arguments, active, "domain_onboarding")
        if not conversation_id:
            return {"error": "缺少当前主会话上下文"}
        if not artifact_id:
            return {"error": "请先选择当前讨论的领域，或提供领域入门 artifact ID"}
        detail = ResearchCatalog(self.store).get_domain_onboarding(artifact_id)
        if detail is None:
            return {"error": "领域入门结果不存在"}
        if detail.get("conversation_id") != conversation_id:
            return {"error": "该领域入门结果不属于当前主会话"}
        learning_path = detail.get("learning_path") or []
        paper_list: list[dict[str, Any]] = []
        seen_papers: set[str] = set()
        for paper in detail.get("recommendations") or []:
            if isinstance(paper, dict):
                key = str(paper.get("paper_id") or paper.get("title") or "").strip()
                if key and key not in seen_papers:
                    paper_list.append(paper)
                    seen_papers.add(key)
        for step in learning_path if isinstance(learning_path, list) else []:
            if not isinstance(step, dict):
                continue
            for paper in step.get("papers") or []:
                if not isinstance(paper, dict):
                    continue
                key = str(paper.get("paper_id") or paper.get("title") or "").strip()
                if key and key not in seen_papers:
                    paper_list.append(paper)
                    seen_papers.add(key)
        return {
            key: detail.get(key)
            for key in (
                "artifact_id", "title", "query", "state", "current_stage",
                "overview", "research_plan", "learning_path", "recommendations",
            )
        } | {"paper_list": paper_list}
