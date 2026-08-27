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


def _requested_id(
    arguments: dict[str, Any], active: dict[str, Any], kind: str
) -> tuple[str, str | None]:
    explicit = str(arguments.get("id") or arguments.get("reading_session_id") or "").strip()
    active_kind = str(active.get("kind") or "").strip()
    active_id = str(active.get("id") or "").strip()
    if active_kind != kind or not active_id:
        return "", "该工具只能访问当前讨论中选定的同类研究对象"
    if explicit and explicit != active_id:
        return "", "不能访问当前讨论之外的研究对象"
    return active_id, None


class _ResearchTool(BaseTool):
    def __init__(self, store: LocalResearchStore) -> None:
        self.store = store

    def _reading(self, arguments: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        conversation_id, active = _runtime(arguments)
        reading_id, scope_error = _requested_id(arguments, active, "paper_reading")
        if not conversation_id:
            return None, "缺少当前主会话上下文"
        if scope_error:
            return None, scope_error
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
            description="按需读取当前讨论论文的原文、论文索引、智能索引、研究总览、阅读进度和已有分析块。",
            parameters={
                "type": "object",
                "properties": {
                    "reading_session_id": {"type": "string", "description": "论文精读会话 ID；已选择当前论文时可省略"},
                    "block_limit": {"type": "integer", "minimum": 0, "maximum": 12, "default": 6},
                    "section_id": {"type": "string", "description": "可选；读取指定章节完整文本"},
                    "include_full_text": {"type": "boolean", "default": False, "description": "是否读取 PDF 提取后的全文"},
                    "max_chars": {"type": "integer", "minimum": 1000, "maximum": 50000, "default": 12000},
                    "text_offset": {"type": "integer", "minimum": 0, "default": 0, "description": "长原文或章节分段读取的起始字符位置"},
                },
                "required": [],
            },
        )

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        reading, error = self._reading(arguments)
        if error:
            return {"error": error}
        block_limit = max(0, min(int(arguments.get("block_limit") or 6), 12))
        max_chars = max(1000, min(int(arguments.get("max_chars") or 12000), 50000))
        text_offset = max(0, int(arguments.get("text_offset") or 0))
        requested_section_id = str(arguments.get("section_id") or "").strip()
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
            document_row = connection.execute(
                "SELECT document_json FROM paper_documents WHERE paper_id = ?",
                (reading["paper_id"],),
            ).fetchone()
            files = connection.execute(
                "SELECT file_kind, storage_uri FROM paper_files WHERE paper_id = ? ORDER BY created_at DESC",
                (reading["paper_id"],),
            ).fetchall()
        document = json.loads(document_row["document_json"] or "{}") if document_row else {}
        reading_map = document.get("reading_map") if isinstance(document.get("reading_map"), dict) else {}
        sections = document.get("sections") if isinstance(document.get("sections"), list) else []
        selected_section = next(
            (section for section in sections if str(section.get("section_id") or "") == requested_section_id),
            None,
        )
        section_index = [
            {
                "section_id": section.get("section_id"),
                "title": section.get("title"),
                "level": section.get("level"),
                "start_page": section.get("start_page"),
                "end_page": section.get("end_page"),
            }
            for section in sections
            if isinstance(section, dict)
        ]
        full_text = str(document.get("full_text") or "")
        selected_content = str(selected_section.get("content") or "") if isinstance(selected_section, dict) else ""
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
            "original_pdf": {
                "files": [dict(row) for row in files],
                "full_text": full_text[text_offset:text_offset + max_chars] if arguments.get("include_full_text") else "",
                "full_text_total_chars": len(full_text),
                "full_text_next_offset": min(len(full_text), text_offset + max_chars) if arguments.get("include_full_text") else None,
                "full_text_truncated": bool(arguments.get("include_full_text") and len(full_text) > text_offset + max_chars),
            },
            "paper_index": section_index,
            "selected_section": (
                {
                    **selected_section,
                    "content": selected_content[text_offset:text_offset + max_chars],
                    "content_total_chars": len(selected_content),
                    "content_next_offset": min(len(selected_content), text_offset + max_chars),
                }
                if isinstance(selected_section, dict) else None
            ),
            "smart_index": reading_map.get("section_guides", []),
            "research_overview": reading_map,
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
        artifact_id, scope_error = _requested_id(arguments, active, "domain_onboarding")
        if not conversation_id:
            return {"error": "缺少当前主会话上下文"}
        if scope_error:
            return {"error": scope_error}
        detail = ResearchCatalog(self.store).get_domain_onboarding(artifact_id)
        if detail is None:
            return {"error": "领域入门结果不存在"}
        if detail.get("conversation_id") != conversation_id:
            return {"error": "该领域入门结果不属于当前主会话"}
        job_row = None
        try:
            with self.store._connection() as connection:
                job_row = connection.execute(
                    "SELECT result_json FROM jobs WHERE task_id = ?", (artifact_id,)
                ).fetchone()
        except Exception:
            job_row = None
        job_result = json.loads(job_row["result_json"] or "{}") if job_row else {}
        result = job_result if isinstance(job_result, dict) and job_result else {
            **(detail.get("overview") or {}),
            "research_plan": detail.get("research_plan") or {},
            "learning_path": detail.get("learning_path") or [],
            "knowledge_graph": detail.get("knowledge_graph") or {},
            "papers": detail.get("recommendations") or [],
        }
        learning_path = result.get("learning_path") or detail.get("learning_path") or []
        paper_list: list[dict[str, Any]] = []
        seen_papers: set[str] = set()
        for paper in (result.get("papers") or detail.get("recommendations") or []):
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
                "overview", "research_plan", "learning_path", "knowledge_graph", "recommendations",
            )
        } | {
            "prerequisites": result.get("prerequisites") or result.get("prerequisite_knowledge") or [],
            "development_path": result.get("development_path") or result.get("development_stages") or result.get("learning_path") or [],
            "concept_landscape": result.get("concept_landscape") or result.get("knowledge_graph") or {},
            "current_landscape": result.get("current_landscape") or {},
            "paper_list": paper_list,
            "result": result,
        }
