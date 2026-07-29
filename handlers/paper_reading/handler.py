"""论文精读主 handler — 对接框架消息管道。

遵循框架 handler 契约: (ChannelMessage, app_state) -> dict
通过 action 字段路由到各子处理器。
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

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
from handlers.paper_reading.postprocessors.common import extract_json_object
from handlers.paper_reading.postprocessors.postprocess import postprocess_agent_output

logger = logging.getLogger(__name__)
LAYOUT_PARSER_VERSION = "section-first-v7-bbox-text"
READING_MAP_VERSION = "novice-reading-map-v1"
READING_MAP_SKILL_ID = "reading.novice_map_builder"


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

    if storage is None:
        return _error("Paper storage 未初始化", action="upload_paper")

    try:
        if request.pdf_data:
            import base64
            pdf_bytes = base64.b64decode(request.pdf_data)
        elif request.pdf_url:
            import httpx
            resp = httpx.get(request.pdf_url, follow_redirects=True, timeout=60.0)
            resp.raise_for_status()
            pdf_bytes = resp.content
        else:
            return _error("请提供 pdf_url 或 pdf_data", action="upload_paper")
    except Exception as e:
        return _error(f"PDF 获取失败: {e}", action="upload_paper")

    paper_id = str(uuid4())
    quick_payload = _build_quick_paper_payload(
        paper_id=paper_id,
        pdf_bytes=pdf_bytes,
        pdf_url=request.pdf_url,
        metadata=request.metadata,
    )
    storage.save_upload(paper_id, pdf_bytes)
    storage.save_paper(paper_id, quick_payload)
    _schedule_background_parse(app_state, paper_id, pdf_bytes)

    response_data = {
        "paper_id": paper_id,
        "title": quick_payload.get("title", ""),
        "authors": quick_payload.get("authors", []),
        "abstract": quick_payload.get("abstract", ""),
        "sections_count": 0,
        "sections": [],
        "figures_count": 0,
        "tables_count": 0,
        "layout_elements_count": 0,
        "parse_status": quick_payload.get("parse_status", "queued"),
        "pdf_url": f"/paper_reading/uploads/{paper_id}.pdf",
        "has_pdf": True,
        "page_count": quick_payload.get("page_count", 0),
        "text_layer_available": bool(quick_payload.get("full_text", "").strip()),
    }

    return _ok("upload_paper", response_data)


def _build_quick_paper_payload(
    *,
    paper_id: str,
    pdf_bytes: bytes,
    pdf_url: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a minimal paper record so the PDF reader can open immediately."""
    metadata = metadata or {}
    title = str(metadata.get("original_filename") or "Parsing paper").removesuffix(".pdf")
    first_text = ""
    page_count = 0
    try:
        import fitz

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            page_count = doc.page_count
            doc_title = str(doc.metadata.get("title") or "").strip()
            if doc_title:
                title = doc_title
            if doc.page_count:
                first_text = doc[0].get_text("text")[:2500]
                if not doc_title:
                    for line in first_text.splitlines():
                        cleaned = line.strip()
                        if len(cleaned) >= 8:
                            title = cleaned[:180]
                            break
    except Exception as error:
        logger.warning("Quick PDF metadata extraction failed for %s: %s", paper_id, error)

    now = datetime.now(timezone.utc).isoformat()
    return {
        "paper_id": paper_id,
        "source": "upload",
        "source_id": "",
        "title": title or "Parsing paper",
        "authors": [],
        "abstract": "",
        "year": None,
        "categories": [],
        "keywords": [],
        "arxiv_id": "",
        "doi": "",
        "url": pdf_url,
        "pdf_url": pdf_url,
        "citation_count": None,
        "venue": "",
        "sections": [],
        "figures": [],
        "tables": [],
        "layout_elements": [],
        "references": [],
        "full_text": first_text,
        "parse_status": "parsing",
        "parse_error": "",
        "stored_at": now,
        "page_count": page_count,
        "reading_map": _empty_reading_map("parsing"),
        "reading_map_status": "pending",
    }


def _schedule_background_parse(app_state: Any, paper_id: str, pdf_bytes: bytes) -> None:
    running = getattr(app_state, "_paper_parse_threads", None)
    if running is None:
        running = set()
        setattr(app_state, "_paper_parse_threads", running)
    if paper_id in running:
        return
    running.add(paper_id)

    def worker() -> None:
        try:
            _run_background_parse(app_state, paper_id, pdf_bytes)
        finally:
            running.discard(paper_id)

    thread = threading.Thread(
        target=worker,
        name=f"paper-parse-{paper_id[:8]}",
        daemon=True,
    )
    thread.start()


def _run_background_parse(app_state: Any, paper_id: str, pdf_bytes: bytes) -> None:
    pipeline = getattr(app_state, "paper_pipeline", None)
    storage = getattr(app_state, "paper_storage", None)
    if pipeline is None or storage is None:
        return
    paper = storage.load_paper(paper_id) or {"paper_id": paper_id}
    paper["parse_status"] = "parsing"
    storage.save_paper(paper_id, paper)
    try:
        metadata = pipeline.parse_pdf_bytes(pdf_bytes)
        metadata.paper_id = paper_id
        payload = metadata.model_dump(mode="json")
        payload["paper_id"] = paper_id
        payload["stored_at"] = paper.get("stored_at") or datetime.now(timezone.utc).isoformat()
        payload["page_count"] = paper.get("page_count", 0)
        payload["url"] = payload.get("url") or paper.get("url", "")
        payload["pdf_url"] = payload.get("pdf_url") or paper.get("pdf_url", "")
        payload["figure_extraction_status"] = "done"
        payload["layout_extraction_status"] = "done"
        payload["layout_parser_version"] = LAYOUT_PARSER_VERSION
        payload["reading_map"] = _build_reading_map(payload)
        payload["reading_map_status"] = "llm_running"
        _persist_figure_assets(storage, paper_id, metadata.figures)
        _persist_table_assets(storage, paper_id, metadata.tables)
        _persist_layout_assets(storage, paper_id, metadata.layout_elements)
        storage.save_paper(paper_id, payload)
        payload["reading_map"] = _build_llm_reading_map(
            paper=payload,
            fallback=payload["reading_map"],
            model=getattr(app_state, "model", None),
            skill_registry=getattr(app_state, "skill_registry", None),
        )
        payload["reading_map_status"] = payload["reading_map"].get("status", "done")
        storage.save_paper(paper_id, payload)
    except Exception as error:
        logger.exception("Background PDF parse failed for %s", paper_id)
        paper = storage.load_paper(paper_id) or {"paper_id": paper_id}
        paper["parse_status"] = "failed"
        paper["parse_error"] = str(error)
        paper["reading_map"] = _empty_reading_map("failed")
        paper["reading_map_status"] = "failed"
        storage.save_paper(paper_id, paper)


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
    if kg_builder is not None and kg_engine is not None and paper_data:
        paper_id_for_kg = session.paper_id or request.paper_id
        if kg_engine.list_nodes_by_paper(paper_id_for_kg):
            revealed_kg = kg_builder.get_revealed_subgraph(
                paper_id=paper_id_for_kg,
                current_section="general",
            )

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

    active_skill_ids = _active_skill_ids_for_context(
        session.active_skills,
        current_section,
        request.content,
    )
    skill_outputs = postprocess_agent_output(
        result.text,
        skill_ids=active_skill_ids,
        paper_id=session.paper_id or request.paper_id,
        section_id=current_section,
        trigger="fork" if session.parent_session_id else "auto",
    )

    # 4. 更新进度（章节完成一次精读即计入已完成）
    session_mgr.update_progress(
        session.session_id,
        section_id=current_section or "abstract",
        paragraph_index=0,
        completed=True,
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
            "kg_mode": "full_paper_once_full_display",
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
    if paper.get("parse_status") == "done":
        paper = _ensure_paper_figures(
            paper=paper,
            upload_path=upload_path,
            storage=storage,
            pipeline=getattr(app_state, "paper_pipeline", None),
        )
    paper_detail = _paper_detail_for_response(paper)
    paper_index = _build_paper_index(paper)
    paper_detail["paper_index"] = paper_index
    reading_map = paper.get("reading_map") or _empty_reading_map(paper.get("parse_status", "pending"))
    paper_detail["reading_map"] = reading_map
    parse_quality = _parse_quality_for_paper(paper)
    text_layer_available = bool(str(paper.get("full_text", "")).strip())
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
                    current_section="general",
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
        "paper_index": paper_index,
        "reading_map": reading_map,
        "reading_map_status": paper.get("reading_map_status", reading_map.get("status", "")),
        "text_layer_available": text_layer_available,
        "parse_quality": parse_quality,
        "parse_status": paper.get("parse_status", ""),
        "parse_error": paper.get("parse_error", ""),
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
        if (
            paper.get("parse_status") == "done"
            and full_text
            and PDFParser.sections_need_repair(paper.get("sections"))
        ):
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
    current_text = _section_text_for_prompt(sections, current)
    selection_context = _selection_context_for_prompt(request, paper_data, current)
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
        "[用户选区 / PDF 定位]\n"
        f"{selection_context}\n\n"
        "[当前章节正文]\n"
        f"标题: {current.get('title', current_section)}\n"
        f"{current_text[:9000]}\n\n"
        "[已展开知识图谱摘要]\n"
        f"{kg_summary}\n\n"
        "[用户问题]\n"
        f"{user_question}\n\n"
        "请优先基于用户选区回答；如果选区不足，再使用同页附近文本、当前章节正文、论文元信息和已展开 KG。"
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


def _section_text_for_prompt(
    sections: list[dict[str, Any]],
    current: dict[str, Any],
) -> str:
    if not current:
        return ""
    try:
        current_index = sections.index(current)
    except ValueError:
        current_index = next(
            (
                index
                for index, section in enumerate(sections)
                if section.get("section_id") == current.get("section_id")
            ),
            -1,
        )
    chunks: list[str] = []
    own_content = str(current.get("content") or "").strip()
    if own_content:
        chunks.append(own_content)

    current_level = int(current.get("level") or 1)
    if current_index >= 0:
        for section in sections[current_index + 1:]:
            level = int(section.get("level") or 1)
            if level <= current_level:
                break
            content = str(section.get("content") or "").strip()
            if not content:
                continue
            title = str(section.get("title") or section.get("section_id") or "").strip()
            chunks.append(f"### {title}\n{content}")
            if sum(len(chunk) for chunk in chunks) >= 9000:
                break

    return "\n\n".join(chunks).strip()


def _selection_context_for_prompt(
    request: PaperReadingRequest,
    paper_data: dict[str, Any],
    current: dict[str, Any],
) -> str:
    metadata = request.metadata or {}
    selected_text = str(metadata.get("selected_text") or "").strip()
    selected_page = metadata.get("selected_page")
    selected_rect = metadata.get("selected_rect")
    source_view = str(metadata.get("source_view") or "index")
    source_section_id = str(metadata.get("source_section_id") or current.get("section_id") or "")
    page_context = _page_context_for_prompt(
        paper_data=paper_data,
        selected_page=selected_page,
        source_section_id=source_section_id,
    )
    lines = [
        f"- source_view: {source_view}",
        f"- source_section_id: {source_section_id}",
        f"- selected_page: {selected_page or ''}",
        f"- selected_rect: {json.dumps(selected_rect, ensure_ascii=False) if selected_rect else ''}",
    ]
    if selected_text:
        lines.append(f"\n[选中文本]\n{selected_text[:6000]}")
    if page_context:
        lines.append(f"\n[同页/相邻索引文本]\n{page_context[:3000]}")
    if not selected_text and not page_context:
        lines.append("(无选区，使用当前章节上下文。)")
    return "\n".join(lines)


def _page_context_for_prompt(
    *,
    paper_data: dict[str, Any],
    selected_page: Any,
    source_section_id: str,
) -> str:
    try:
        page = int(selected_page)
    except (TypeError, ValueError):
        page = 0
    sections = paper_data.get("sections", []) or []
    if page > 0:
        chunks = []
        for section in sections:
            start = int(section.get("start_page") or 0)
            end = int(section.get("end_page") or start or 0)
            if start and start <= page <= max(start, end):
                title = str(section.get("title") or section.get("section_id") or "")
                content = str(section.get("content") or "")
                chunks.append(f"### {title}\n{content[:1800]}")
        if chunks:
            return "\n\n".join(chunks)
    if source_section_id:
        section = _find_section(sections, source_section_id)
        return str(section.get("content") or "")[:2400]
    return ""


def _build_paper_index(paper: dict[str, Any]) -> dict[str, Any]:
    sections = []
    for section in paper.get("sections", []) or []:
        content = str(section.get("content") or "")
        paragraphs = section.get("paragraphs") or [
            paragraph.strip()
            for paragraph in content.split("\n\n")
            if paragraph.strip()
        ]
        text_chunks = []
        for index, paragraph in enumerate(paragraphs[:8], start=1):
            text = str(paragraph).strip()
            if not text:
                continue
            text_chunks.append({
                "chunk_id": f"{section.get('section_id', 'sec')}::chunk:{index}",
                "text": text[:1200],
                "page_refs": [
                    page for page in (
                        section.get("start_page"),
                        section.get("end_page"),
                    )
                    if page
                ],
            })
        sections.append({
            "section_id": section.get("section_id", ""),
            "title": section.get("title", ""),
            "level": section.get("level", 1),
            "start_page": section.get("start_page"),
            "end_page": section.get("end_page"),
            "text_chunks": text_chunks,
            "page_refs": [
                page for page in (
                    section.get("start_page"),
                    section.get("end_page"),
                )
                if page
            ],
        })
    return {
        "version": "pdf-first-index-v1",
        "sections": sections,
        "sections_count": len(sections),
        "full_text_length": len(str(paper.get("full_text") or "")),
    }


def _empty_reading_map(status: str = "pending") -> dict[str, Any]:
    return {
        "version": READING_MAP_VERSION,
        "status": status,
        "research_problem": {},
        "core_method": {},
        "method_steps": [],
        "experimental_support": [],
        "limitations_and_questions": [],
    }


def _build_reading_map(paper: dict[str, Any]) -> dict[str, Any]:
    sections = paper.get("sections", []) or []
    if not sections:
        return _empty_reading_map("pending")

    def stage(section: dict[str, Any]) -> str:
        title = str(section.get("title") or "").lower()
        if "abstract" in title or "摘要" in title:
            return "abstract"
        if any(token in title for token in ("intro", "background", "related work", "引言", "背景")):
            return "introduction"
        if any(token in title for token in ("method", "approach", "model", "framework", "algorithm", "prelim", "方法", "模型")):
            return "method"
        if any(token in title for token in ("experiment", "evaluation", "result", "analysis", "实验", "评估", "结果")):
            return "experiment"
        if any(token in title for token in ("discussion", "limitation", "conclusion", "future", "讨论", "局限", "结论")):
            return "conclusion"
        return "general"

    by_stage: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        by_stage.setdefault(stage(section), []).append(section)

    title = str(paper.get("title") or "Core Method")
    abstract = str(paper.get("abstract") or "")
    intro_sections = by_stage.get("abstract", []) + by_stage.get("introduction", [])
    method_sections = by_stage.get("method", []) or sections[: min(4, len(sections))]
    experiment_sections = by_stage.get("experiment", [])
    conclusion_sections = by_stage.get("conclusion", [])

    intro_text = _compact_section_text(intro_sections, limit=1600) or abstract
    method_text = _compact_section_text(method_sections, limit=1600)
    experiment_text = _compact_section_text(experiment_sections, limit=1600)
    conclusion_text = _compact_section_text(conclusion_sections, limit=1200)

    problem_sentence = _first_sentence(intro_text) or _first_sentence(abstract) or "Identify the paper's core research problem from the abstract and introduction."
    method_sentence = _first_sentence(method_text) or _first_sentence(abstract) or title

    method_steps = []
    for section in method_sections[:5]:
        section_title = str(section.get("title") or "Method step")
        text = str(section.get("content") or "")
        method_steps.append({
            "name": section_title,
            "goal": _first_sentence(text) or "Understand this method component.",
            "input": "See source section",
            "operation": _second_sentence(text) or "Read how this step transforms the problem setup into the proposed solution.",
            "output": "A clearer part of the proposed method",
            "why_needed": "This section explains one link in the paper's method pipeline.",
            "source_sections": [_source_ref(section)],
        })

    experimental_support = []
    for section in experiment_sections[:5]:
        text = str(section.get("content") or "")
        experimental_support.append({
            "claim": _first_sentence(text) or f"Evidence discussed in {section.get('title', 'experiment section')}",
            "evidence": _second_sentence(text) or "Use this section to connect results back to the paper's claims.",
            "datasets": _find_terms(text, ("dataset", "benchmark", "corpus", "qa", "imagenet", "cifar", "glue", "hotpot", "wiki", "数据集")),
            "metrics": _find_terms(text, ("accuracy", "f1", "em", "pass@", "score", "precision", "recall", "指标")),
            "figures_or_tables": _find_figure_table_refs(text),
            "source_sections": [_source_ref(section)],
        })

    limitations = []
    source_for_limits = conclusion_sections or sections[-2:]
    for section in source_for_limits[:5]:
        text = str(section.get("content") or "")
        limitations.append({
            "limitation": _first_sentence(text) or f"Open question from {section.get('title', 'final section')}",
            "why_it_matters": _second_sentence(text) or "This helps novice readers separate proven claims from future work.",
            "novice_question": f"What should I be cautious about when reading {section.get('title', 'this section')}?",
            "source_sections": [_source_ref(section)],
        })

    return {
        "version": READING_MAP_VERSION,
        "status": "done",
        "research_problem": {
            "title": "Research Problem",
            "one_sentence": problem_sentence,
            "why_it_matters": _second_sentence(intro_text) or "This explains why the paper's method is needed.",
            "source_sections": [_source_ref(item) for item in intro_sections[:3]],
        },
        "core_method": {
            "name": title,
            "one_sentence": method_sentence,
            "main_idea": _second_sentence(method_text) or "Read the method sections to understand the paper's central mechanism.",
            "source_sections": [_source_ref(item) for item in method_sections[:3]],
        },
        "method_steps": method_steps,
        "experimental_support": experimental_support,
        "limitations_and_questions": limitations,
        "section_guides": _build_heuristic_section_guides(sections),
    }


def _build_llm_reading_map(
    *,
    paper: dict[str, Any],
    fallback: dict[str, Any],
    model: Any | None,
    skill_registry: Any | None,
) -> dict[str, Any]:
    if model is None:
        fallback["status"] = "heuristic_done"
        return fallback
    prompt = _build_reading_map_prompt(paper, fallback, skill_registry)
    try:
        response = model.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a paper-reading map builder for novice researchers. "
                        "Return only a valid JSON object that follows the requested schema."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
        )
        content = response.choices[0].message.content or ""
        parsed = extract_json_object(content)
        if not parsed:
            fallback["status"] = "heuristic_done"
            fallback["llm_error"] = "No valid JSON object returned."
            return fallback
        merged = _normalize_reading_map(parsed, fallback)
        merged["status"] = "llm_done"
        return merged
    except Exception as error:
        logger.warning("LLM reading map extraction failed: %s", error)
        fallback["status"] = "heuristic_done"
        fallback["llm_error"] = str(error)
        return fallback


def _build_reading_map_prompt(
    paper: dict[str, Any],
    fallback: dict[str, Any],
    skill_registry: Any | None,
) -> str:
    skill_instructions = ""
    if skill_registry is not None:
        try:
            skill_instructions = skill_registry.get_instructions(READING_MAP_SKILL_ID)
        except Exception as error:
            logger.debug("Unable to load %s instructions: %s", READING_MAP_SKILL_ID, error)

    sections_payload = []
    for section in (paper.get("sections", []) or [])[:28]:
        text = " ".join(str(section.get("content") or "").split())
        sections_payload.append({
            "section_id": section.get("section_id", ""),
            "title": section.get("title", ""),
            "level": section.get("level", 1),
            "start_page": section.get("start_page"),
            "end_page": section.get("end_page"),
            "text": text[:1400],
        })

    prompt_payload = {
        "paper": {
            "title": paper.get("title", ""),
            "abstract": str(paper.get("abstract") or "")[:1800],
        },
        "sections": sections_payload,
        "heuristic_fallback": fallback,
    }
    return (
        f"{skill_instructions}\n\n"
        "Build a deep novice-oriented reading_map and smart_index for this paper.\n"
        "The output must be JSON only. Use this schema exactly:\n"
        "{\n"
        '  "research_problem": {"title": "", "one_sentence": "", "why_it_matters": "", "novice_takeaway": "", "source_sections": []},\n'
        '  "core_method": {"name": "", "one_sentence": "", "main_idea": "", "technical_route": "", "source_sections": []},\n'
        '  "method_steps": [{"name": "", "goal": "", "input": "", "operation": "", "output": "", "why_needed": "", "source_sections": []}],\n'
        '  "experimental_support": [{"claim": "", "evidence": "", "datasets": [], "dataset_format": "", "experiment_setting": "", "baselines": [], "metrics": [], "protocol": "", "figures_or_tables": [], "source_sections": []}],\n'
        '  "limitations_and_questions": [{"limitation": "", "why_it_matters": "", "novice_question": "", "source_sections": []}],\n'
        '  "section_guides": [{"section_id": "", "title": "", "main_content": "", "core_idea": "", "technical_route": "", "implementation_plan": "", "datasets": [], "dataset_format": "", "experiment_setting": "", "baselines": [], "experiment_protocol": "", "novice_focus": "", "source_page": null}]\n'
        "}\n"
        "Rules: keep each field compact but substantive; do not copy long paragraphs; "
        "only fill experiment fields for experiment/evaluation/result sections; "
        "for non-method sections, technical_route and implementation_plan may be empty; "
        "source_sections entries should be objects with section_id, title, page when possible; "
        "write Chinese content for readers.\n\n"
        f"INPUT:\n{json.dumps(prompt_payload, ensure_ascii=False)}"
    )


def _normalize_reading_map(parsed: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "version": READING_MAP_VERSION,
        "status": "llm_done",
        "research_problem": parsed.get("research_problem") or fallback.get("research_problem", {}),
        "core_method": parsed.get("core_method") or fallback.get("core_method", {}),
        "method_steps": _list_or_fallback(parsed.get("method_steps"), fallback.get("method_steps", []), 5),
        "experimental_support": _list_or_fallback(parsed.get("experimental_support"), fallback.get("experimental_support", []), 5),
        "limitations_and_questions": _list_or_fallback(parsed.get("limitations_and_questions"), fallback.get("limitations_and_questions", []), 5),
        "section_guides": _list_or_fallback(parsed.get("section_guides"), fallback.get("section_guides", []), 40),
    }
    return normalized


def _list_or_fallback(value: Any, fallback: Any, limit: int) -> list[Any]:
    items = value if isinstance(value, list) else fallback
    return list(items or [])[:limit]


def _build_heuristic_section_guides(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    guides = []
    for section in sections[:40]:
        title = str(section.get("title") or "")
        content = str(section.get("content") or "")
        text = " ".join(content.split())
        lower_title = title.lower()
        is_experiment = any(token in lower_title for token in ("experiment", "evaluation", "result", "analysis", "实验", "评估", "结果"))
        is_method = any(token in lower_title for token in ("method", "approach", "model", "framework", "algorithm", "prelim", "方法", "模型"))
        guide = {
            "section_id": section.get("section_id", ""),
            "title": title,
            "main_content": _first_sentence(text) or "This section anchors one part of the paper.",
            "core_idea": _second_sentence(text) or "Read it as a compact guide to the section's role in the paper.",
            "technical_route": _third_sentence(text) if is_method else "",
            "implementation_plan": "Identify inputs, operations, and outputs in this section." if is_method else "",
            "datasets": _find_terms(text, ("dataset", "benchmark", "corpus", "qa", "imagenet", "cifar", "glue", "hotpot", "wiki", "数据集")) if is_experiment else [],
            "dataset_format": "",
            "experiment_setting": _first_sentence(text) if is_experiment else "",
            "baselines": _find_baselines(text) if is_experiment else [],
            "experiment_protocol": _second_sentence(text) if is_experiment else "",
            "novice_focus": "Focus on why this section is needed and how it supports the paper's main claim.",
            "source_page": section.get("start_page"),
        }
        guides.append(guide)
    return guides


def _compact_section_text(sections: list[dict[str, Any]], limit: int = 1600) -> str:
    chunks = []
    remaining = limit
    for section in sections:
        if remaining <= 0:
            break
        text = " ".join(str(section.get("content") or "").split())
        if not text:
            continue
        take = text[:remaining]
        chunks.append(take)
        remaining -= len(take)
    return " ".join(chunks).strip()


def _first_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    parts = [part.strip() for part in re_split_sentences(text) if part.strip()]
    return parts[0][:360] if parts else text[:360]


def _second_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    parts = [part.strip() for part in re_split_sentences(text) if part.strip()]
    return parts[1][:360] if len(parts) > 1 else ""


def _third_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    parts = [part.strip() for part in re_split_sentences(text) if part.strip()]
    return parts[2][:360] if len(parts) > 2 else ""


def re_split_sentences(text: str) -> list[str]:
    import re

    return re.split(r"(?<=[.!?。！？])\s+", text)


def _source_ref(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_id": section.get("section_id", ""),
        "title": section.get("title", ""),
        "page": section.get("start_page"),
    }


def _find_terms(text: str, needles: tuple[str, ...]) -> list[str]:
    lowered = str(text or "").lower()
    hits = []
    for needle in needles:
        if needle.lower() in lowered:
            hits.append(needle)
    return hits[:6]


def _find_figure_table_refs(text: str) -> list[str]:
    import re

    refs = re.findall(r"\b(?:Figure|Fig\.|Table)\s*\d+[A-Za-z]?\b", str(text or ""), flags=re.IGNORECASE)
    seen = []
    for ref in refs:
        normalized = " ".join(ref.split())
        if normalized not in seen:
            seen.append(normalized)
    return seen[:8]


def _find_baselines(text: str) -> list[str]:
    import re

    matches = re.findall(r"\b(?:PPO|GRPO|DPO|SFT|MAML|ReAct|RAG|CoT|GPT-4|Llama|Qwen|DeepSeek|Claude)[A-Za-z0-9_.-]*\b", str(text or ""))
    seen = []
    for item in matches:
        if item not in seen:
            seen.append(item)
    return seen[:8]


def _parse_quality_for_paper(paper: dict[str, Any]) -> str:
    full_text_length = len(str(paper.get("full_text") or "").strip())
    sections_count = len(paper.get("sections", []) or [])
    if full_text_length < 500:
        return "scanned_or_ocr_needed"
    if sections_count < 2 or full_text_length < 3000:
        return "degraded"
    return "good"


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


def _active_skill_ids_for_context(
    active_skills: list[str],
    current_section: str,
    user_content: str = "",
) -> list[str]:
    text = f"{current_section}\n{user_content}".lower()
    if any(token in text for token in ("formula", "equation", "derivation", "math", "公式", "推导", "数学")):
        return ["reading.math_verifier"]
    if any(token in text for token in ("critique", "review", "weakness", "limitation", "审稿", "批判", "局限", "不足")):
        return ["reading.critique_agent"]
    if any(token in text for token in ("code", "implementation", "reproduce", "复现", "代码", "实现")):
        return ["reading.code_reviewer"]
    if any(token in text for token in ("domain", "concept", "background", "领域", "概念", "脉络", "背景")):
        return ["reading.domain_expert"]
    if any(token in text for token in ("writing", "rhetoric", "narrative", "写作", "行文", "表达")):
        return ["reading.writing_coach"]
    if any(token in text for token in ("idea", "future", "extension", "创新", "想法", "未来", "后续")):
        return ["reading.idea_generator"]
    if any(token in text for token in ("related", "citation", "cross", "相关工作", "引用", "跨论文")):
        return ["reading.cross_paper_linker"]
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

    paper_id = paper.get("paper_id", "")
    figures = []
    for figure in paper.get("figures", []) or []:
        item = {
            key: value
            for key, value in figure.items()
            if key != "image_data"
        }
        asset_name = str(item.get("asset_name", ""))
        item["image_url"] = (
            f"/paper_reading/figures/{paper_id}/{asset_name}"
            if paper_id and asset_name
            else ""
        )
        figures.append(item)

    tables = []
    for table in paper.get("tables", []) or []:
        item = {
            key: value
            for key, value in table.items()
            if key != "image_data"
        }
        asset_name = str(item.get("asset_name", ""))
        item["image_url"] = (
            f"/paper_reading/figures/{paper_id}/{asset_name}"
            if paper_id and asset_name
            else ""
        )
        tables.append(item)

    layout_elements = []
    for element in paper.get("layout_elements", []) or []:
        item = {
            key: value
            for key, value in element.items()
            if key != "image_data"
        }
        asset_name = str(item.get("asset_name", ""))
        asset_url = (
            f"/paper_reading/figures/{paper_id}/{asset_name}"
            if paper_id and asset_name
            else ""
        )
        item["asset_url"] = asset_url
        item["image_url"] = asset_url
        layout_elements.append(item)

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
        "figures": figures,
        "tables": tables,
        "layout_elements": layout_elements,
        "references": paper.get("references", []),
        "full_text": paper.get("full_text", ""),
        "parse_status": paper.get("parse_status", ""),
        "parse_error": paper.get("parse_error", ""),
        "page_count": paper.get("page_count", 0),
        "reading_map": paper.get("reading_map") or _empty_reading_map(paper.get("parse_status", "pending")),
        "reading_map_status": paper.get("reading_map_status", ""),
        "stored_at": paper.get("stored_at", ""),
    }


def _persist_figure_assets(
    storage: Any,
    paper_id: str,
    figures: list[Any],
) -> None:
    for figure in figures:
        image_data = getattr(figure, "image_data", b"")
        asset_name = getattr(figure, "asset_name", "")
        if image_data and asset_name:
            storage.save_figure(paper_id, asset_name, image_data)


def _persist_table_assets(
    storage: Any,
    paper_id: str,
    tables: list[Any],
) -> None:
    for table in tables:
        image_data = getattr(table, "image_data", b"")
        asset_name = getattr(table, "asset_name", "")
        if image_data and asset_name:
            storage.save_figure(paper_id, asset_name, image_data)


def _persist_layout_assets(
    storage: Any,
    paper_id: str,
    elements: list[Any],
) -> None:
    for element in elements:
        image_data = getattr(element, "image_data", b"")
        asset_name = getattr(element, "asset_name", "")
        if image_data and asset_name:
            storage.save_figure(paper_id, asset_name, image_data)


def _ensure_paper_figures(
    *,
    paper: dict[str, Any],
    upload_path: Any,
    storage: Any,
    pipeline: Any,
) -> dict[str, Any]:
    """Backfill extracted figures for papers stored before figure assets existed."""
    if upload_path is None or pipeline is None:
        return paper

    figures = paper.get("figures", []) or []
    layout_elements = paper.get("layout_elements", []) or []
    assets_available = bool(figures) and all(
        figure.get("asset_name")
        and storage.get_figure_path(paper.get("paper_id", ""), figure["asset_name"])
        for figure in figures
    )
    layout_assets_available = all(
        not element.get("asset_name")
        or storage.get_figure_path(paper.get("paper_id", ""), element["asset_name"])
        for element in layout_elements
    )
    if (
        paper.get("figure_extraction_status") == "done"
        and paper.get("layout_extraction_status") == "done"
        and paper.get("layout_parser_version") == LAYOUT_PARSER_VERSION
        and (not figures or assets_available)
        and layout_assets_available
    ):
        return paper

    try:
        reparsed = pipeline.parse_pdf(upload_path)
        paper_id = paper.get("paper_id", "")
        _persist_figure_assets(storage, paper_id, reparsed.figures)
        _persist_table_assets(storage, paper_id, reparsed.tables)
        _persist_layout_assets(storage, paper_id, reparsed.layout_elements)
        paper["figures"] = [figure.model_dump() for figure in reparsed.figures]
        paper["tables"] = [table.model_dump() for table in reparsed.tables]
        paper["layout_elements"] = [
            element.model_dump() for element in reparsed.layout_elements
        ]
        paper["figure_extraction_status"] = "done"
        paper["layout_extraction_status"] = "done"
        paper["layout_parser_version"] = LAYOUT_PARSER_VERSION
        paper["sections"] = [
            section.model_dump(mode="json")
            for section in reparsed.sections
        ]
        paper["full_text"] = reparsed.full_text
        storage.save_paper(paper_id, paper)
    except Exception as exc:
        logger.warning("Unable to backfill paper figures for %s: %s", paper.get("paper_id", ""), exc)
    return paper
