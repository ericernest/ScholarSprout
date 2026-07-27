"""论文精读主 handler — 对接框架消息管道。

遵循框架 handler 契约: (ChannelMessage, app_state) -> dict
通过 action 字段路由到各子处理器。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from typing import Any

from channels.base import ChannelMessage
from runtime.agent_runner import AgentRunResult, run_agent_detailed

from handlers.paper_reading.schemas.request import PaperReadingRequest
from handlers.paper_reading.schemas.response import (
    KnowledgeGraphUpdate,
    PaperReadingResponse,
    ReadingProgress,
    SessionState,
    SkillOutput,
)
from handlers.paper_reading.harness.progress import format_progress_message
from handlers.paper_reading.kg.query import KGQueryEngine
from handlers.paper_reading.pipeline.parser import PDFParser
from handlers.paper_reading.postprocessors.postprocess import postprocess_agent_output

logger = logging.getLogger(__name__)


# ── 主入口 ──

def handle_paper_reading_message(
    message: ChannelMessage,
    app_state: Any,
) -> dict[str, Any]:
    """论文精读统一 handler — 对接 gateway message_flow。

    框架调用链:
        HTTP POST /paper_reading → process_channel_input()
        → channel.receive_message() → ChannelMessage
        → handler(message, app_state) → dict
        → build_channel_output() → HTTP Response

    Args:
        message: 入站 ChannelMessage，content 应为 PaperReadingRequest 的 dict/JSON
        app_state: gateway app.state，包含所有共享组件

    Returns:
        dict (JSON-serializable)，被包装为出站 ChannelMessage 的 content
    """
    # 解析请求
    try:
        content = message.content
        if isinstance(content, str):
            content = json.loads(content)
        request = PaperReadingRequest.model_validate(content)
    except Exception as e:
        return _error(f"请求解析失败: {e}")

    # Action 路由
    handler_map = {
        "search_paper": _handle_search_paper,
        "upload_paper": _handle_upload_paper,
        "start_reading": _handle_start_reading,
        "pause_reading": _handle_pause_reading,
        "resume_reading": _handle_resume_reading,
        "fork": _handle_fork,
        "merge": _handle_merge,
        "load_skill": _handle_load_skill,
        "unload_skill": _handle_unload_skill,
        "kg_query": _handle_kg_query,
        "get_session_state": _handle_get_session_state,
        "get_progress": _handle_get_progress,
        "get_paper_detail": _handle_get_paper_detail,
    }

    handler_fn = handler_map.get(request.action)
    if handler_fn is None:
        return _error(f"未知 action: {request.action}")

    try:
        result = handler_fn(request, app_state)
        return result
    except Exception as e:
        logger.exception("Handler action '%s' failed", request.action)
        return _error(str(e), action=request.action)


# ── 错误响应 ──

def _error(message: str, action: str = "", session_id: str = "") -> dict:
    return {
        "status": "error",
        "action": action,
        "message": message,
        "session_id": session_id,
        "error": message,
    }


def _ok(action: str, data: dict | None = None, **kwargs) -> dict:
    result = {"status": "ok", "action": action, "data": data or {}}
    result.update(kwargs)
    return result


# ── Action 处理器 ──

def _handle_search_paper(request: PaperReadingRequest, app_state: Any) -> dict:
    """搜索论文。"""
    pipeline = getattr(app_state, "paper_pipeline", None)
    if pipeline is None:
        return _error("论文搜索流水线未初始化", action="search_paper")

    query = request.search_query or request.content
    if not query:
        return _error("搜索关键词不能为空", action="search_paper")

    results = asyncio.run(
        pipeline.search(
            query=query,
            sources=None if request.search_source == "all" else [request.search_source],
            max_results=request.search_max_results,
        )
    )

    papers = [
        {
            "paper_id": p.paper_id,
            "title": p.title,
            "authors": [a.name for a in p.authors],
            "year": p.year,
            "abstract": p.abstract[:500],
            "source": p.source,
            "url": p.url,
            "pdf_url": p.pdf_url,
            "citation_count": p.citation_count,
            "venue": p.venue,
        }
        for p in results
    ]

    return _ok("search_paper", {"query": query, "count": len(papers), "papers": papers})


def _handle_upload_paper(request: PaperReadingRequest, app_state: Any) -> dict:
    """上传 PDF 论文。"""
    pipeline = getattr(app_state, "paper_pipeline", None)
    storage = getattr(app_state, "paper_storage", None)

    if pipeline is None:
        return _error("论文处理流水线未初始化", action="upload_paper")

    try:
        if request.pdf_data:
            import base64
            pdf_bytes = base64.b64decode(request.pdf_data)
            metadata = pipeline.parse_pdf_bytes(pdf_bytes)
        elif request.pdf_url:
            import httpx
            import tempfile
            resp = httpx.get(request.pdf_url, follow_redirects=True, timeout=60.0)
            resp.raise_for_status()
            pdf_bytes = resp.content
            metadata = pipeline.parse_pdf_bytes(pdf_bytes)
        else:
            return _error("请提供 pdf_url 或 pdf_data", action="upload_paper")
    except Exception as e:
        return _error(f"PDF 解析失败: {e}", action="upload_paper")

    # 持久化
    if storage:
        storage.save_paper(metadata.paper_id, metadata.model_dump())
        if 'pdf_bytes' in dir():
            storage.save_upload(metadata.paper_id, pdf_bytes)

    kg_builder = getattr(app_state, "kg_builder", None)
    kg_engine = getattr(app_state, "kg_engine", None)
    kg_result = None
    if kg_builder is not None:
        kg_result = kg_builder.build_full_paper(
            paper_id=metadata.paper_id,
            paper_data=metadata.model_dump(),
            model=getattr(app_state, "model", None),
        )
        if storage and kg_engine is not None:
            storage.save_kg(metadata.paper_id, kg_engine.to_dict(metadata.paper_id))

    sections_summary = [
        {"section_id": s.section_id, "title": s.title, "level": s.level}
        for s in metadata.sections
    ]

    response_data = {
        "paper_id": metadata.paper_id,
        "title": metadata.title,
        "authors": [a.name for a in metadata.authors],
        "abstract": metadata.abstract[:500],
        "sections_count": len(metadata.sections),
        "sections": sections_summary,
        "parse_status": metadata.parse_status,
    }
    if kg_result is not None:
        response_data["kg_build"] = {
            "new_nodes": len(kg_result.new_nodes),
            "new_edges": len(kg_result.new_edges),
            "mode": "full_paper_once",
        }

    return _ok("upload_paper", response_data)


def _handle_start_reading(request: PaperReadingRequest, app_state: Any) -> dict:
    """开始/继续阅读 — 核心阅读逻辑。"""
    session_mgr = getattr(app_state, "session_manager", None)
    kg_builder = getattr(app_state, "kg_builder", None)
    kg_engine = getattr(app_state, "kg_engine", None)
    paper_agent = getattr(app_state, "paper_reading_agent", None)
    storage = getattr(app_state, "paper_storage", None)

    if session_mgr is None:
        return _error("Session manager 未初始化", action="start_reading")
    if paper_agent is None:
        return _error("论文精读 Agent 未初始化", action="start_reading")

    # 1. 会话管理
    session_id = request.session_id
    session = session_mgr.get_session(session_id) if session_id else None

    if session is None:
        paper_data_for_title = _load_paper_data(storage, request.paper_id)
        session = session_mgr.create_session(
            paper_id=request.paper_id,
            paper_title=(paper_data_for_title or {}).get("title", ""),
            user_id=request.session_id or "default",
        )

    paper_data = _load_paper_data(storage, session.paper_id or request.paper_id)
    if paper_data and not session.paper_title:
        session.paper_title = paper_data.get("title", "")
    if paper_data:
        session_mgr.set_total_sections(session.session_id, len(paper_data.get("sections", []) or []))

    current_section = _select_current_section(request, session, paper_data)
    kg_result = None
    revealed_kg: dict[str, Any] = {}
    if kg_builder is not None and paper_data:
        kg_result = kg_builder.ensure_full_paper_kg(
            paper_id=session.paper_id or request.paper_id,
            paper_data=paper_data,
            model=getattr(app_state, "model", None),
        )
        revealed_kg = kg_builder.get_revealed_subgraph(
            paper_id=session.paper_id or request.paper_id,
            current_section=current_section,
        )
        if storage and kg_engine is not None:
            storage.save_kg(session.paper_id or request.paper_id, kg_engine.to_dict(session.paper_id or request.paper_id))

    content_msg = _build_start_reading_context(
        request=request,
        session=session,
        paper_data=paper_data,
        current_section=current_section,
        revealed_kg=revealed_kg,
    )

    # 3. 执行 Agent
    result: AgentRunResult = run_agent_detailed(
        agent=paper_agent,
        user_content=content_msg,
        tool_registry=app_state.tool_registry,
        skill_registry=app_state.skill_registry,
        capability_selector=app_state.capability_selector,
        max_steps=5,
    )

    active_skill_ids = _active_skill_ids_for_context(session.active_skills, current_section)
    skill_outputs = postprocess_agent_output(
        result.text,
        skill_ids=active_skill_ids,
        paper_id=session.paper_id or request.paper_id,
        section_id=current_section,
        trigger="fork" if session.parent_session_id else "auto",
    )

    # 4. 更新进度
    session_mgr.update_progress(
        session.session_id,
        section_id=current_section or "abstract",
        paragraph_index=0,
    )

    # 5. 构建响应
    data = {
        "session_id": session.session_id,
        "agent_response": result.text,
        "model_calls": result.model_calls,
        "duration_ms": result.duration_ms,
        "current_section": current_section,
        "context": {
            "paper_loaded": paper_data is not None,
            "kg_mode": "full_paper_once_progressive_reveal",
            "active_skill_ids": active_skill_ids,
        },
        "revealed_kg": revealed_kg,
    }
    if kg_result is not None:
        data["kg_build"] = {
            "section_type": kg_result.section_type,
            "new_nodes": len(kg_result.new_nodes),
            "new_edges": len(kg_result.new_edges),
        }

    return _ok("start_reading", data,
        session={
            "session_id": session.session_id,
            "paper_id": session.paper_id,
            "paper_title": session.paper_title,
            "state": session.state,
            "current_section": current_section,
            "active_skills": session.active_skills,
        },
        progress=session.progress,
        kg_update={
            "new_nodes": [
                {"node_id": node.node_id, "node_type": node.node_type, "label": node.label}
                for node in (kg_result.new_nodes if kg_result else [])
            ],
            "new_edges": [
                {"edge_id": edge.edge_id, "edge_type": edge.edge_type, "label": edge.label}
                for edge in (kg_result.new_edges if kg_result else [])
            ],
            "updated_nodes": [],
            "fusion_events": [],
        },
        skill_outputs=skill_outputs,
    )


def _handle_pause_reading(request: PaperReadingRequest, app_state: Any) -> dict:
    """暂停阅读。"""
    session_mgr = getattr(app_state, "session_manager", None)
    if session_mgr is None:
        return _error("Session manager 未初始化", action="pause_reading")

    session = session_mgr.pause(request.session_id)
    if session is None:
        return _error("会话不存在", action="pause_reading", session_id=request.session_id)

    cp = session.checkpoints[-1] if session.checkpoints else None
    return _ok("pause_reading", {
        "checkpoint_id": cp.checkpoint_id if cp else "",
        "message": "阅读已暂停，进度已保存",
    })


def _handle_resume_reading(request: PaperReadingRequest, app_state: Any) -> dict:
    """恢复阅读。"""
    session_mgr = getattr(app_state, "session_manager", None)
    if session_mgr is None:
        return _error("Session manager 未初始化", action="resume_reading")

    session = session_mgr.resume(request.session_id)
    if session is None:
        return _error("会话不存在", action="resume_reading", session_id=request.session_id)

    return _ok("resume_reading", {
        "message": "阅读已恢复",
        "current_section": session.progress.get("current_position", {}).get("section_id", ""),
        "active_skills": session.active_skills,
    })


def _handle_fork(request: PaperReadingRequest, app_state: Any) -> dict:
    """创建分支探索。"""
    fork_mgr = getattr(app_state, "fork_manager", None)
    if fork_mgr is None:
        return _error("Fork manager 未初始化", action="fork")

    fork_session = fork_mgr.create_fork(
        parent_session_id=request.session_id,
        fork_context=request.fork_context,
        fork_skills=request.fork_skills,
        fork_question=request.fork_question,
    )
    if fork_session is None:
        return _error("创建分支失败", action="fork")

    return _ok("fork", {
        "fork_session_id": fork_session.session_id,
        "message": "分支已创建",
        "active_skills": fork_session.active_skills,
    })


def _handle_merge(request: PaperReadingRequest, app_state: Any) -> dict:
    """合并分支成果。"""
    fork_mgr = getattr(app_state, "fork_manager", None)
    if fork_mgr is None:
        return _error("Fork manager 未初始化", action="merge")

    merge_id = request.merge_session_id
    if not merge_id:
        return _error("请指定 merge_session_id", action="merge")

    result = fork_mgr.merge_fork(merge_id)
    if not result.success:
        return _error(result.error, action="merge")

    return _ok("merge", {
        "message": "分支已合并",
        "key_findings": result.key_findings,
        "merged_skills": result.merged_skills,
    })


def _handle_load_skill(request: PaperReadingRequest, app_state: Any) -> dict:
    """加载 Skill。"""
    session_mgr = getattr(app_state, "session_manager", None)
    if session_mgr is None:
        return _error("Session manager 未初始化", action="load_skill")

    session = session_mgr.activate_skills(request.session_id, request.skill_ids)
    if session is None:
        return _error("会话不存在", action="load_skill")

    return _ok("load_skill", {
        "active_skills": session.active_skills,
        "message": f"已加载 Skill: {', '.join(request.skill_ids)}",
    })


def _handle_unload_skill(request: PaperReadingRequest, app_state: Any) -> dict:
    """卸载 Skill。"""
    session_mgr = getattr(app_state, "session_manager", None)
    if session_mgr is None:
        return _error("Session manager 未初始化", action="unload_skill")

    session = session_mgr.deactivate_skills(request.session_id, request.skill_ids)
    if session is None:
        return _error("会话不存在", action="unload_skill")

    return _ok("unload_skill", {
        "active_skills": session.active_skills,
        "message": f"已卸载 Skill: {', '.join(request.skill_ids)}",
    })


def _handle_kg_query(request: PaperReadingRequest, app_state: Any) -> dict:
    """KG 驱动问答。"""
    kg_engine = getattr(app_state, "kg_engine", None)
    if kg_engine is None:
        return _error("KG 引擎未初始化", action="kg_query")

    question = request.kg_question or request.content
    if not question:
        return _error("请提供 kg_question", action="kg_query")

    storage = getattr(app_state, "paper_storage", None)
    kg_builder = getattr(app_state, "kg_builder", None)
    paper_id = request.paper_id or ""
    paper_data = _load_paper_data(storage, paper_id)
    if kg_builder is not None and paper_data:
        kg_builder.ensure_full_paper_kg(
            paper_id=paper_id,
            paper_data=paper_data,
            model=getattr(app_state, "model", None),
        )

    query_engine = getattr(app_state, "kg_query_engine", None)
    if query_engine is None:
        query_engine = KGQueryEngine(kg_engine, getattr(app_state, "model", None))

    data = query_engine.answer(
        question=question,
        paper_id=paper_id,
        query_type=request.kg_query_type,
        source_label=request.kg_source_label,
        target_label=request.kg_target_label,
        node_id=request.kg_node_id,
    )

    return _ok("kg_query", data)


def _handle_get_session_state(request: PaperReadingRequest, app_state: Any) -> dict:
    """获取会话完整状态。"""
    session_mgr = getattr(app_state, "session_manager", None)
    if session_mgr is None:
        return _error("Session manager 未初始化", action="get_session_state")

    session = session_mgr.get_session(request.session_id)
    if session is None:
        return _error("会话不存在", action="get_session_state", session_id=request.session_id)

    return _ok("get_session_state", {
        "session_id": session.session_id,
        "paper_id": session.paper_id,
        "paper_title": session.paper_title,
        "state": session.state,
        "active_skills": session.active_skills,
        "checkpoints_count": len(session.checkpoints),
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "parent_session_id": session.parent_session_id,
        "fork_sessions": session.fork_sessions,
    }, progress=session.progress)


def _handle_get_progress(request: PaperReadingRequest, app_state: Any) -> dict:
    """获取阅读进度。"""
    session_mgr = getattr(app_state, "session_manager", None)
    if session_mgr is None:
        return _error("Session manager 未初始化", action="get_progress")

    session = session_mgr.get_session(request.session_id)
    if session is None:
        return _error("会话不存在", action="get_progress", session_id=request.session_id)

    progress_msg = format_progress_message(session.progress)
    return _ok("get_progress", {
        "progress": session.progress,
        "formatted": progress_msg,
    })


def _handle_get_paper_detail(request: PaperReadingRequest, app_state: Any) -> dict:
    """获取论文完整 metadata、sections 正文和 PDF 恢复 URL。"""
    storage = getattr(app_state, "paper_storage", None)
    if storage is None:
        return _error("Paper storage 未初始化", action="get_paper_detail")

    paper_id = request.paper_id or ""
    if not paper_id and request.session_id:
        session_mgr = getattr(app_state, "session_manager", None)
        session = session_mgr.get_session(request.session_id) if session_mgr else None
        paper_id = session.paper_id if session else ""
    if not paper_id:
        return _error("请提供 paper_id", action="get_paper_detail")

    paper = _load_paper_data(storage, paper_id)
    if paper is None:
        return _error("论文不存在", action="get_paper_detail")

    upload_path = storage.get_upload_path(paper_id)
    paper_detail = _paper_detail_for_response(paper)
    initial_kg: dict[str, Any] = {}
    kg_engine = getattr(app_state, "kg_engine", None)
    kg_builder = getattr(app_state, "kg_builder", None)
    if kg_engine is not None:
        if not kg_engine.list_nodes_by_paper(paper_id):
            saved_kg = storage.load_kg(paper_id)
            if saved_kg:
                kg_engine.from_dict(copy.deepcopy(saved_kg))
        if kg_engine.list_nodes_by_paper(paper_id):
            if kg_builder is not None:
                initial_kg = kg_builder.get_revealed_subgraph(
                    paper_id=paper_id,
                    current_section="abstract",
                )
            else:
                graph = kg_engine.get_subgraph(paper_id)
                initial_kg = {
                    "current_stage": "general",
                    "node_count": graph.get("node_count", 0),
                    "edge_count": graph.get("edge_count", 0),
                    "cytoscape_elements": kg_engine.to_cytoscape(paper_id).get("elements", []),
                }
    return _ok("get_paper_detail", {
        "paper": paper_detail,
        "pdf_url": f"/paper_reading/uploads/{paper_id}.pdf" if upload_path else "",
        "has_pdf": upload_path is not None,
        "initial_kg": initial_kg,
    })


# ── 上下文与数据辅助函数 ──

def _load_paper_data(storage: Any, paper_id: str) -> dict[str, Any] | None:
    if storage is None or not paper_id:
        return None
    try:
        paper = storage.load_paper(paper_id)
        if not paper:
            return paper
        full_text = str(paper.get("full_text", ""))
        if full_text and PDFParser.sections_need_repair(paper.get("sections")):
            parser = PDFParser()
            repaired = parser.extract_sections(full_text)
            if repaired:
                paper = dict(paper)
                paper["sections"] = [section.model_dump(mode="json") for section in repaired]
                repaired_title = parser.extract_title(full_text)
                if repaired_title:
                    paper["title"] = repaired_title
                repaired_authors = parser.extract_authors(full_text)
                if len(repaired_authors) >= 2:
                    paper["authors"] = [
                        author.model_dump(mode="json")
                        for author in repaired_authors
                    ]
        return paper
    except Exception as error:
        logger.warning("Failed to load paper %s: %s", paper_id, error)
        return None


def _select_current_section(
    request: PaperReadingRequest,
    session: Any,
    paper_data: dict[str, Any] | None,
) -> str:
    if request.target_section:
        return request.target_section
    current = session.progress.get("current_position", {}).get("section_id", "")
    if current:
        return current
    sections = (paper_data or {}).get("sections", []) or []
    if sections:
        for section in sections:
            title = str(section.get("title", "")).lower()
            if "abstract" in title or "摘要" in title:
                return section.get("section_id", "")
        return sections[0].get("section_id", "")
    return "abstract"


def _build_start_reading_context(
    request: PaperReadingRequest,
    session: Any,
    paper_data: dict[str, Any] | None,
    current_section: str,
    revealed_kg: dict[str, Any],
) -> str:
    user_question = request.content or "请继续阅读并分析当前章节。"
    if not paper_data:
        return (
            f"[会话]\n"
            f"- session_id: {session.session_id}\n"
            f"- paper_id: {session.paper_id}\n"
            f"- current_section: {current_section}\n\n"
            f"[用户问题]\n{user_question}"
        )

    sections = paper_data.get("sections", []) or []
    current = _find_section(sections, current_section)
    section_index = _section_index_for_prompt(sections)
    kg_summary = _kg_summary_for_prompt(revealed_kg)
    active_skills = ", ".join(session.active_skills) if session.active_skills else "auto"
    return (
        "[论文元信息]\n"
        f"- paper_id: {paper_data.get('paper_id', session.paper_id)}\n"
        f"- title: {paper_data.get('title', '')}\n"
        f"- authors: {', '.join(_author_names(paper_data.get('authors', [])))}\n"
        f"- abstract: {(paper_data.get('abstract') or '')[:1200]}\n\n"
        "[阅读状态]\n"
        f"- session_id: {session.session_id}\n"
        f"- current_section: {current_section}\n"
        f"- active_skills: {active_skills}\n"
        f"- progress: {json.dumps(session.progress, ensure_ascii=False)}\n\n"
        "[论文目录]\n"
        f"{section_index}\n\n"
        "[当前章节正文]\n"
        f"标题: {current.get('title', current_section)}\n"
        f"{(current.get('content') or '')[:9000]}\n\n"
        "[已展开知识图谱摘要]\n"
        f"{kg_summary}\n\n"
        "[用户问题]\n"
        f"{user_question}\n\n"
        "请基于当前章节正文、论文元信息和已展开 KG 回答。"
        "如果输出某个 Skill 的结构化结果，请优先输出该 Skill 约定的 JSON。"
    )


def _find_section(sections: list[dict[str, Any]], section_id: str) -> dict[str, Any]:
    for section in sections:
        if section.get("section_id") == section_id:
            return section
    for section in sections:
        title = str(section.get("title", "")).lower()
        if section_id.lower() and section_id.lower() in title:
            return section
    return sections[0] if sections else {}


def _section_index_for_prompt(sections: list[dict[str, Any]]) -> str:
    lines = []
    for section in sections[:30]:
        indent = "  " * max(int(section.get("level", 1)) - 1, 0)
        lines.append(f"{indent}- {section.get('section_id', '')}: {section.get('title', '')}")
    return "\n".join(lines) or "(无章节索引)"


def _kg_summary_for_prompt(revealed_kg: dict[str, Any]) -> str:
    nodes = revealed_kg.get("nodes", []) or []
    edges = revealed_kg.get("edges", []) or []
    node_lines = [
        f"- [{node.get('node_type', '')}] {node.get('label', '')}"
        for node in nodes[:20]
    ]
    edge_lines = [
        f"- {edge.get('source', '')} --{edge.get('edge_type', '')}--> {edge.get('target', '')}"
        for edge in edges[:20]
    ]
    return (
        f"阶段: {revealed_kg.get('current_stage', '')}\n"
        f"节点数: {len(nodes)}, 边数: {len(edges)}\n"
        f"节点:\n{chr(10).join(node_lines) or '(无)'}\n"
        f"关系:\n{chr(10).join(edge_lines) or '(无)'}"
    )


def _author_names(authors: list[Any]) -> list[str]:
    names = []
    for author in authors:
        if isinstance(author, dict):
            names.append(str(author.get("name", "")))
        else:
            names.append(str(author))
    return [name for name in names if name]


def _active_skill_ids_for_context(active_skills: list[str], current_section: str) -> list[str]:
    if active_skills:
        return list(active_skills)
    section = current_section.lower()
    if "experiment" in section or "result" in section or "实验" in section:
        return ["reading.critique_agent"]
    if "conclusion" in section or "结论" in section:
        return ["reading.idea_generator"]
    return ["reading.method_analyst"]


def _paper_detail_for_response(paper: dict[str, Any]) -> dict[str, Any]:
    """返回前端渲染论文正文需要的完整但稳定的 paper payload。"""
    sections = []
    for section in paper.get("sections", []) or []:
        sections.append({
            "section_id": section.get("section_id", ""),
            "title": section.get("title", ""),
            "level": section.get("level", 1),
            "content": section.get("content", ""),
            "paragraphs": section.get("paragraphs", []),
            "start_page": section.get("start_page"),
            "end_page": section.get("end_page"),
        })

    return {
        "paper_id": paper.get("paper_id", ""),
        "source": paper.get("source", ""),
        "source_id": paper.get("source_id", ""),
        "title": paper.get("title", ""),
        "authors": _author_names(paper.get("authors", [])),
        "abstract": paper.get("abstract", ""),
        "published_date": paper.get("published_date"),
        "updated_date": paper.get("updated_date"),
        "year": paper.get("year"),
        "categories": paper.get("categories", []),
        "keywords": paper.get("keywords", []),
        "doi": paper.get("doi", ""),
        "url": paper.get("url", ""),
        "pdf_url": paper.get("pdf_url", ""),
        "citation_count": paper.get("citation_count"),
        "venue": paper.get("venue", ""),
        "sections": sections,
        "sections_count": len(sections),
        "figures": paper.get("figures", []),
        "tables": paper.get("tables", []),
        "references": paper.get("references", []),
        "full_text": paper.get("full_text", ""),
        "parse_status": paper.get("parse_status", ""),
        "stored_at": paper.get("stored_at", ""),
    }
