"""论文精读主 handler — 对接框架消息管道。

遵循框架 handler 契约: (ChannelMessage, app_state) -> dict
通过 action 字段路由到各子处理器。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from channels.base import ChannelMessage
from runtime.agent_runner import AgentRunResult, run_agent_detailed

from handlers.paper_reading.schemas.request import PaperReadingRequest
from handlers.paper_reading.harness.progress import format_progress_message
from handlers.paper_reading.pipeline.parser import PDFParser
from handlers.paper_reading.postprocessors.common import extract_json_object, repair_json_object
from handlers.paper_reading.postprocessors.postprocess import postprocess_agent_output

logger = logging.getLogger(__name__)
LAYOUT_PARSER_VERSION = "section-first-v7-bbox-text"
READING_MAP_VERSION = "novice-reading-map-v2"
READING_MAP_SKILL_ID = "reading.novice_map_builder"
PAPER_READING_AGENT_MAX_STEPS = 7
SURVEY_CHUNK_MAX_CHARS = 16000
SURVEY_CHUNK_MAX_WORKERS = 3
SURVEY_CHUNK_BATCH_SIZE = 3
SURVEY_CHUNK_BATCH_TIMEOUT_SECONDS = 180
SURVEY_MERGE_PROMPT_LIMIT = 60000
SURVEY_CARD_SECTION_TEXT_LIMIT = 12000
SURVEY_CARD_CONTEXT_LIMIT = 26000
SURVEY_INTRO_CONTEXT_LIMIT = 18000
RESEARCH_OVERVIEW_MAX_TOKENS = 8000
RESEARCH_OVERVIEW_REQUEST_TIMEOUT_SECONDS = 120.0
RESEARCH_GUIDE_MAX_TOKENS = 3500
RESEARCH_GUIDE_REQUEST_TIMEOUT_SECONDS = 75.0
RESEARCH_GUIDE_MAX_WORKERS = 4
RESEARCH_GUIDE_SECTIONS_PER_REQUEST = 3
RESEARCH_GUIDE_SECTION_LIMIT = 120
RESEARCH_GUIDE_SECTION_TEXT_LIMIT = 7000
SURVEY_PLAN_MAX_TOKENS = 5000
SURVEY_PLAN_REQUEST_TIMEOUT_SECONDS = 75.0
SURVEY_CARD_MAX_TOKENS = 4000
SURVEY_CARD_REQUEST_TIMEOUT_SECONDS = 75.0
SURVEY_CARD_MAX_WORKERS = 4
SURVEY_MAP_TASK_LIMIT = 12
SURVEY_SECTION_GUIDE_TASK_LIMIT = 19
SURVEY_FACT_MAX_TOKENS = 4500
SURVEY_FACT_REQUEST_TIMEOUT_SECONDS = 75.0
SURVEY_MERGE_MAX_TOKENS = 7000
SURVEY_MERGE_REQUEST_TIMEOUT_SECONDS = 90.0
SURVEY_CARD_PLAN_VERSION = "survey-card-plan-v1"
SURVEY_MAP_GROUP_KEYS = (
    "field_overview",
    "development_timeline",
    "pain_points",
    "taxonomy",
    "technical_routes",
    "representative_methods",
    "datasets",
    "evaluation_protocols",
    "applications",
    "open_challenges",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reading_map_json_chat(
    model: Any,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    timeout: float,
) -> Any:
    """Run one bounded JSON request; an explicit timeout disables SDK retries."""
    return model.chat(
        messages=messages,
        response_format={"type": "json_object"},
        disable_thinking=True,
        max_tokens=max_tokens,
        timeout=timeout,
        max_retries=0,
    )


def _reading_map_response_json(
    response: Any,
    *,
    label: str,
    max_tokens: int,
) -> dict[str, Any]:
    choices = getattr(response, "choices", None) or (
        response.get("choices", []) if isinstance(response, dict) else []
    )
    if not choices:
        raise ValueError(f"{label}未返回 choices")
    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None) or (
        choice.get("finish_reason") if isinstance(choice, dict) else None
    )
    message = getattr(choice, "message", None) or (
        choice.get("message", {}) if isinstance(choice, dict) else {}
    )
    content = getattr(message, "content", None) if not isinstance(message, dict) else message.get("content")
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    content = str(content or "")
    parsed = extract_json_object(content)
    if parsed is not None:
        return parsed

    normalized_reason = str(finish_reason or "unknown").lower()
    diagnostics = f"finish_reason={normalized_reason}, content_chars={len(content)}"
    if normalized_reason in {"length", "max_tokens"}:
        logger.warning("Truncated JSON for %s (%s)", label, diagnostics)
        raise ValueError(f"{label}输出在 max_tokens={max_tokens} 处被截断（{diagnostics}）")
    repaired = repair_json_object(content)
    if repaired is not None:
        logger.warning("Repaired malformed JSON for %s (%s)", label, diagnostics)
        return repaired
    logger.warning("Invalid JSON for %s (%s)", label, diagnostics)
    raise ValueError(f"{label}未返回有效 JSON（{diagnostics}）")


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
        if isinstance(content, dict) and not str(content.get("session_id") or "").strip():
            content = {**content, "session_id": message.session_id}
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
        "get_session_state": _handle_get_session_state,
        "get_progress": _handle_get_progress,
        "get_paper_detail": _handle_get_paper_detail,
        "regenerate_reading_map": _handle_regenerate_reading_map,
    }

    handler_fn = handler_map.get(request.action)
    if handler_fn is None:
        return _error(f"未知 action: {request.action}")

    try:
        if request.action == "start_reading":
            result = handler_fn(
                request,
                app_state,
                on_text_delta=message.metadata.get("_stream_text_delta"),
                on_reasoning_delta=message.metadata.get("_stream_reasoning_delta"),
                cancel_event=message.metadata.get("_stream_cancel_event"),
            )
        else:
            result = handler_fn(request, app_state)
        _persist_skill_outputs(app_state, result)
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

    research_store = getattr(storage, "research_store", None)
    file_hash = hashlib.sha256(pdf_bytes).hexdigest()
    matched_by_hash = (
        research_store.find_paper_by_file_hash(file_hash)
        if research_store is not None and not request.paper_id
        else None
    )
    matched_by_identity = (
        research_store.find_paper_by_identity(
            arxiv_id=_arxiv_id_from_url(request.pdf_url) or None,
            source_url=request.pdf_url or None,
        )
        if research_store is not None and not request.paper_id and not matched_by_hash
        else None
    )
    # Explicit attachments win; otherwise reuse a binary/identity match.
    paper_id = request.paper_id or matched_by_hash or matched_by_identity or str(uuid4())
    if matched_by_hash:
        existing = storage.load_paper(paper_id)
        if (
            existing
            and existing.get("parse_status") != "failed"
            and storage.get_upload_path(paper_id) is not None
        ):
            research_store.ensure_library_item(paper_id, reading_status="unread")
            return _ok(
                "upload_paper",
                _upload_response_data(paper_id, existing, deduplicated=True),
            )
    quick_payload = _build_quick_paper_payload(
        paper_id=paper_id,
        pdf_bytes=pdf_bytes,
        pdf_url=request.pdf_url,
        metadata=request.metadata,
    )
    storage.save_upload(paper_id, pdf_bytes, sha256=file_hash)
    storage.save_paper(paper_id, quick_payload)
    if research_store is not None:
        research_store.ensure_library_item(paper_id, reading_status="unread")
    _schedule_background_parse(app_state, paper_id, pdf_bytes)

    return _ok(
        "upload_paper",
        _upload_response_data(
            paper_id,
            quick_payload,
            deduplicated=bool(matched_by_identity),
        ),
    )


def _upload_response_data(
    paper_id: str,
    paper: dict[str, Any],
    *,
    deduplicated: bool,
) -> dict[str, Any]:
    sections = paper.get("sections", []) or []
    return {
        "paper_id": paper_id,
        "title": paper.get("title", ""),
        "authors": paper.get("authors", []),
        "abstract": paper.get("abstract", ""),
        "sections_count": len(sections),
        "sections": sections,
        "figures_count": len(paper.get("figures", []) or []),
        "tables_count": len(paper.get("tables", []) or []),
        "layout_elements_count": len(paper.get("layout_elements", []) or []),
        "parse_status": paper.get("parse_status", "queued"),
        "section_extraction_source": paper.get("section_extraction_source", "pending"),
        "section_extraction_status": paper.get("section_extraction_status", "pending"),
        "section_extraction_message": paper.get("section_extraction_message", ""),
        "outline_entries_count": paper.get("outline_entries_count", 0),
        "pdf_url": f"/paper_reading/uploads/{paper_id}.pdf",
        "has_pdf": True,
        "page_count": paper.get("page_count", 0),
        "text_layer_available": bool(str(paper.get("full_text", "")).strip()),
        "deduplicated": deduplicated,
    }


def _build_quick_paper_payload(
    *,
    paper_id: str,
    pdf_bytes: bytes,
    pdf_url: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a minimal paper record so the PDF reader can open immediately."""
    metadata = metadata or {}
    metadata_title = str(metadata.get("title") or "").strip()
    title = str(
        metadata_title
        or metadata.get("original_filename")
        or "Parsing paper"
    ).removesuffix(".pdf")
    authors = _author_names(metadata.get("authors", []))
    abstract = str(metadata.get("abstract") or "").strip()
    first_text = ""
    page_count = 0
    year = _optional_year(metadata.get("year"))
    arxiv_id = _arxiv_id_from_url(pdf_url)
    source = "arxiv" if arxiv_id else "upload"
    try:
        import fitz

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            page_count = doc.page_count
            doc_title = str(doc.metadata.get("title") or "").strip()
            if doc_title and not metadata_title:
                title = doc_title
            if doc.page_count:
                first_text = doc[0].get_text("text")[:2500]
                if not doc_title and not metadata_title:
                    for line in first_text.splitlines():
                        cleaned = line.strip()
                        if len(cleaned) >= 8:
                            title = cleaned[:180]
                            break
            year = year or PDFParser.extract_year(
                first_text,
                document_metadata=doc.metadata,
                source_hint=f"{pdf_url} {metadata.get('original_filename', '')}",
            )
            if not abstract:
                abstract = PDFParser().extract_abstract(first_text)
    except Exception as error:
        logger.warning("Quick PDF metadata extraction failed for %s: %s", paper_id, error)

    now = datetime.now(timezone.utc).isoformat()
    return {
        "paper_id": paper_id,
        "source": source,
        "source_id": arxiv_id,
        "title": title or "Parsing paper",
        "authors": authors,
        "abstract": abstract,
        "year": year,
        "categories": [],
        "keywords": [],
        "arxiv_id": arxiv_id,
        "doi": "",
        "url": str(metadata.get("source_url") or pdf_url),
        "pdf_url": pdf_url,
        "citation_count": None,
        "venue": "",
        "sections": [],
        "figures": [],
        "tables": [],
        "layout_elements": [],
        "references": [],
        "full_text": first_text,
        "section_extraction_source": "pending",
        "section_extraction_status": "pending",
        "section_extraction_message": "PDF 正在解析，尚未确定目录来源。",
        "outline_entries_count": 0,
        "parse_status": "parsing",
        "parse_error": "",
        "stored_at": now,
        "page_count": page_count,
        "reading_map": _empty_reading_map("parsing"),
        "reading_map_status": "pending",
    }


def _optional_year(value: Any) -> int | None:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= 2100 else None


def _arxiv_id_from_url(value: str) -> str:
    match = re.search(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)(\d{4}\.\d{4,5})(?:v\d+)?", value or "", re.IGNORECASE)
    return match.group(1) if match else ""


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
        payload = _preserve_imported_paper_metadata(
            metadata.model_dump(mode="json"), paper
        )
        payload["paper_id"] = paper_id
        payload["stored_at"] = paper.get("stored_at") or datetime.now(timezone.utc).isoformat()
        payload["page_count"] = paper.get("page_count", 0)
        payload["year"] = payload.get("year") or paper.get("year")
        payload["source"] = paper.get("source") if paper.get("source") == "arxiv" else payload.get("source", "upload")
        payload["source_id"] = payload.get("source_id") or paper.get("source_id", "")
        payload["arxiv_id"] = payload.get("arxiv_id") or paper.get("arxiv_id", "")
        payload["url"] = payload.get("url") or paper.get("url", "")
        payload["pdf_url"] = payload.get("pdf_url") or paper.get("pdf_url", "")
        payload["figure_extraction_status"] = "done"
        payload["layout_extraction_status"] = "done"
        payload["layout_parser_version"] = LAYOUT_PARSER_VERSION
        generation_id = str(uuid4())
        fallback_reading_map = _build_reading_map(payload)
        payload["reading_map"] = _empty_reading_map("llm_running")
        payload["reading_map_status"] = "llm_running"
        payload["reading_map_phase"] = "queued"
        payload["reading_map_progress"] = 0
        payload["reading_map_error"] = ""
        payload["reading_map_generation_id"] = generation_id
        generation_started_at = _utc_now_iso()
        payload["reading_map_started_at"] = generation_started_at
        payload["reading_map_heartbeat_at"] = generation_started_at
        payload.pop("reading_map_completed_at", None)
        _persist_figure_assets(storage, paper_id, metadata.figures)
        _persist_table_assets(storage, paper_id, metadata.tables)
        _persist_layout_assets(storage, paper_id, metadata.layout_elements)
        storage.save_paper(paper_id, payload)
        try:
            payload["reading_map"] = _generate_reading_map_for_paper(
                paper=payload,
                fallback=fallback_reading_map,
                model=getattr(app_state, "model", None),
                skill_registry=getattr(app_state, "skill_registry", None),
                storage=storage,
                paper_id=paper_id,
                generation_id=generation_id,
            )
        except Exception as error:
            logger.exception("Background reading map generation failed for %s", paper_id)
            _persist_failed_reading_map(
                storage,
                paper_id,
                f"导读地图与智能索引生成失败：{error}",
                generation_id=generation_id,
            )
            return
        latest = storage.load_paper(paper_id) or payload
        if latest.get("reading_map_generation_id") != generation_id:
            return
        latest["reading_map"] = payload["reading_map"]
        latest["reading_map_status"] = payload["reading_map"].get("status", "failed")
        if latest["reading_map_status"] == "llm_done":
            latest["reading_map_phase"] = "llm_done"
            latest["reading_map_progress"] = 100
            latest["reading_map_error"] = ""
        elif latest["reading_map_status"] in {"failed", "failed_partial"}:
            latest["reading_map_phase"] = "failed"
            latest["reading_map_error"] = payload["reading_map"].get("error", "")
        completed_at = _utc_now_iso()
        latest["reading_map_heartbeat_at"] = completed_at
        latest["reading_map_completed_at"] = completed_at
        storage.save_paper(paper_id, latest)
    except Exception as error:
        logger.exception("Background PDF parse failed for %s", paper_id)
        paper = storage.load_paper(paper_id) or {"paper_id": paper_id}
        paper["parse_status"] = "failed"
        paper["parse_error"] = str(error)
        paper["reading_map"] = _empty_reading_map("failed")
        paper["reading_map_status"] = "failed"
        paper["reading_map_phase"] = "failed"
        paper["reading_map_progress"] = 0
        paper["reading_map_error"] = str(error)
        storage.save_paper(paper_id, paper)


def _preserve_imported_paper_metadata(
    parsed: dict[str, Any], existing: dict[str, Any]
) -> dict[str, Any]:
    """Keep trusted import metadata when PDF extraction leaves a field empty."""
    merged = dict(parsed)
    for field in ("title", "authors", "abstract"):
        if not merged.get(field) and existing.get(field):
            merged[field] = existing[field]
    return merged


def _schedule_reading_map_generation(
    app_state: Any,
    paper_id: str,
    *,
    generation_id: str = "",
    force: bool = False,
) -> None:
    running = getattr(app_state, "_paper_reading_map_threads", None)
    if running is None:
        running = set()
        setattr(app_state, "_paper_reading_map_threads", running)
    if paper_id in running and not force:
        return
    running.add(paper_id)

    def worker() -> None:
        try:
            _run_reading_map_generation(app_state, paper_id, generation_id=generation_id)
        finally:
            running.discard(paper_id)

    thread = threading.Thread(
        target=worker,
        name=f"reading-map-{paper_id[:8]}",
        daemon=True,
    )
    thread.start()


def resume_pending_reading_map_generations(app_state: Any) -> int:
    """Requeue persisted in-flight maps after a service restart."""
    storage = getattr(app_state, "paper_storage", None)
    if storage is None:
        return 0
    try:
        papers = storage.list_paper_documents()
    except Exception as error:
        logger.warning("Failed to inspect persisted reading maps during startup: %s", error)
        return 0

    resumed = 0
    for paper in papers:
        if paper.get("reading_map_status") != "llm_running" or not paper.get("sections"):
            continue
        paper_id = str(paper.get("paper_id") or "")
        if not paper_id:
            continue
        generation_id = str(paper.get("reading_map_generation_id") or uuid4())
        now = _utc_now_iso()
        paper["reading_map_generation_id"] = generation_id
        paper["reading_map_started_at"] = paper.get("reading_map_started_at") or now
        paper["reading_map_heartbeat_at"] = now
        paper["reading_map_resumed_at"] = now
        paper["reading_map_phase"] = "queued"
        paper["reading_map_error"] = ""
        storage.save_paper(paper_id, paper)
        _schedule_reading_map_generation(
            app_state,
            paper_id,
            generation_id=generation_id,
        )
        resumed += 1
    if resumed:
        logger.info("Requeued %s persisted reading-map generation task(s)", resumed)
    return resumed


def _run_reading_map_generation(app_state: Any, paper_id: str, *, generation_id: str = "") -> None:
    storage = getattr(app_state, "paper_storage", None)
    if storage is None:
        return
    try:
        paper = _load_paper_data(storage, paper_id)
        if paper is None:
            return
        if generation_id and paper.get("reading_map_generation_id") != generation_id:
            return
        if not paper.get("sections"):
            _persist_failed_reading_map(
                storage,
                paper_id,
                "论文目录和章节正文尚未解析完成，无法生成导读地图。",
                generation_id=generation_id,
            )
            return
        fallback = _build_reading_map(paper)
        reading_map = _generate_reading_map_for_paper(
            paper=paper,
            fallback=fallback,
            model=getattr(app_state, "model", None),
            skill_registry=getattr(app_state, "skill_registry", None),
            storage=storage,
            paper_id=paper_id,
            generation_id=generation_id,
        )
        latest = _load_paper_data(storage, paper_id) or paper
        if generation_id and latest.get("reading_map_generation_id") != generation_id:
            return
        paper = latest
        paper["reading_map"] = reading_map
        paper["reading_map_status"] = paper["reading_map"].get("status", "failed")
        if paper["reading_map_status"] == "llm_done":
            paper["reading_map_phase"] = "llm_done"
            paper["reading_map_progress"] = 100
            paper["reading_map_error"] = ""
        elif paper["reading_map_status"] in {"failed", "failed_partial"}:
            paper["reading_map_phase"] = "failed"
            paper["reading_map_error"] = paper["reading_map"].get("error", "")
        completed_at = _utc_now_iso()
        paper["reading_map_heartbeat_at"] = completed_at
        paper["reading_map_completed_at"] = completed_at
        storage.save_paper(paper_id, paper)
    except Exception as error:
        logger.exception("Reading map generation failed for %s", paper_id)
        _persist_failed_reading_map(
            storage,
            paper_id,
            f"导读地图与智能索引生成失败：{error}",
            generation_id=generation_id,
        )


def _handle_start_reading(
    request: PaperReadingRequest,
    app_state: Any,
    *,
    on_text_delta: Any | None = None,
    on_reasoning_delta: Any | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """开始/继续阅读 — 核心阅读逻辑。"""
    session_mgr = getattr(app_state, "session_manager", None)
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
            session_id=request.session_id or None,
            paper_id=request.paper_id,
            paper_title=(paper_data_for_title or {}).get("title", ""),
            user_id=request.session_id or "default",
        )

    paper_data = _load_paper_data(storage, session.paper_id or request.paper_id)
    research_store = getattr(storage, "research_store", None)
    if research_store is not None and (session.paper_id or request.paper_id):
        research_store.ensure_library_item(
            session.paper_id or request.paper_id, reading_status="reading"
        )
    if paper_data and not session.paper_title:
        session.paper_title = paper_data.get("title", "")
    if paper_data:
        session_mgr.set_total_sections(session.session_id, len(paper_data.get("sections", []) or []))

    current_section = _select_current_section(request, session, paper_data)
    content_msg = _build_start_reading_context(
        request=request,
        session=session,
        paper_data=paper_data,
        current_section=current_section,
    )
    _record_reading_message(
        storage,
        session.session_id,
        role="user",
        content=request.content,
    )

    # 3. 执行 Agent
    result: AgentRunResult = run_agent_detailed(
        agent=paper_agent,
        user_content=content_msg,
        tool_registry=app_state.tool_registry,
        skill_registry=app_state.skill_registry,
        capability_selector=app_state.capability_selector,
        max_steps=PAPER_READING_AGENT_MAX_STEPS,
        on_text_delta=on_text_delta,
        on_reasoning_delta=on_reasoning_delta,
        cancel_event=cancel_event,
    )

    if result.cancelled:
        return _ok(
            "start_reading",
            {
                "session_id": session.session_id,
                "agent_response": "",
                "model_calls": result.model_calls,
                "duration_ms": result.duration_ms,
                "current_section": current_section,
                "interrupted": True,
                "reasoning": result.reasoning,
            },
            session={
                "session_id": session.session_id,
                "paper_id": session.paper_id,
                "paper_title": session.paper_title,
                "state": session.state,
                "current_section": current_section,
                "active_skills": session.active_skills,
            },
            progress=session.progress,
            skill_outputs=[],
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
        "reasoning": result.reasoning,
        "model_calls": result.model_calls,
        "duration_ms": result.duration_ms,
        "current_section": current_section,
        "context": {
            "paper_loaded": paper_data is not None,
            "active_skill_ids": active_skill_ids,
        },
    }

    _record_reading_message(
        storage,
        session.session_id,
        role="assistant",
        content=result.text,
    )

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
        skill_outputs=skill_outputs,
    )


def _persist_skill_outputs(app_state: Any, result: dict[str, Any]) -> None:
    storage = getattr(app_state, "paper_storage", None)
    research_store = getattr(storage, "research_store", None)
    if research_store is None:
        return
    session = result.get("session") if isinstance(result.get("session"), dict) else {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    session_id = str(session.get("session_id") or data.get("session_id") or "")
    if not session_id:
        return
    for output in result.get("skill_outputs") or []:
        if not isinstance(output, dict):
            continue
        content = output.get("content")
        if not isinstance(content, dict):
            content = {"value": content}
        research_store.save_reading_block(
            session_id,
            block_type=str(output.get("output_type") or output.get("skill_id") or "analysis"),
            content_schema_version="paper-reading-skill-output-v1",
            content=content,
            rendered_text=str(output.get("rendered") or ""),
        )


def _record_reading_message(
    storage: Any,
    session_id: str,
    *,
    role: str,
    content: str,
) -> None:
    """Persist only visible Agent Q&A under the resolved reading session."""
    text = str(content or "").strip()
    research_store = getattr(storage, "research_store", None)
    if research_store is None or not session_id or not text:
        return
    research_store.append_message(
        session_id,
        role=role,
        content=text,
        mode="paper_reading",
        channel="web",
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


def _handle_regenerate_reading_map(request: PaperReadingRequest, app_state: Any) -> dict:
    storage = getattr(app_state, "paper_storage", None)
    if storage is None:
        return _error("Paper storage 未初始化", action="regenerate_reading_map")
    paper_id = request.paper_id or ""
    if not paper_id and request.session_id:
        session_mgr = getattr(app_state, "session_manager", None)
        session = session_mgr.get_session(request.session_id) if session_mgr else None
        paper_id = session.paper_id if session else ""
    if not paper_id:
        return _error("请提供 paper_id", action="regenerate_reading_map")

    paper = _load_paper_data(storage, paper_id)
    if paper is None:
        return _error("论文不存在", action="regenerate_reading_map")
    if not paper.get("sections"):
        return _error("论文目录和章节正文尚未解析完成，暂时无法生成导读地图。", action="regenerate_reading_map")

    generation_id = str(uuid4())
    paper["reading_map"] = _empty_reading_map("llm_running")
    paper["reading_map_status"] = "llm_running"
    paper["reading_map_phase"] = "queued"
    paper["reading_map_progress"] = 0
    paper["reading_map_error"] = ""
    paper["reading_map_generation_id"] = generation_id
    generation_started_at = _utc_now_iso()
    paper["reading_map_started_at"] = generation_started_at
    paper["reading_map_heartbeat_at"] = generation_started_at
    paper.pop("reading_map_completed_at", None)
    storage.save_paper(paper_id, paper)
    _schedule_reading_map_generation(app_state, paper_id, generation_id=generation_id, force=True)
    return _ok("regenerate_reading_map", {
        "paper_id": paper_id,
        "reading_map": paper["reading_map"],
        "reading_map_status": "llm_running",
        "reading_map_phase": "queued",
        "reading_map_progress": 0,
        "reading_map_error": "",
        "reading_map_card_progress": {},
        "reading_map_started_at": paper["reading_map_started_at"],
        "reading_map_heartbeat_at": paper["reading_map_heartbeat_at"],
        "message": "导读地图与智能索引已重新提交生成。",
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
    reading_map_artifacts = paper.get("reading_map_artifacts") if isinstance(paper.get("reading_map_artifacts"), dict) else {}
    card_progress = reading_map_artifacts.get("survey_card_progress") if isinstance(reading_map_artifacts.get("survey_card_progress"), dict) else {}
    parse_quality = _parse_quality_for_paper(paper)
    section_info = _section_extraction_info(paper)
    text_layer_available = bool(str(paper.get("full_text", "")).strip())
    return _ok("get_paper_detail", {
        "paper": paper_detail,
        "paper_index": paper_index,
        "reading_map": reading_map,
        "reading_map_status": paper.get("reading_map_status", reading_map.get("status", "")),
        "reading_map_phase": paper.get("reading_map_phase", ""),
        "reading_map_progress": paper.get("reading_map_progress", 0),
        "reading_map_error": paper.get("reading_map_error", reading_map.get("error", "")),
        "reading_map_card_progress": card_progress,
        "reading_map_started_at": paper.get("reading_map_started_at", ""),
        "reading_map_heartbeat_at": paper.get("reading_map_heartbeat_at", ""),
        "reading_map_completed_at": paper.get("reading_map_completed_at", ""),
        "text_layer_available": text_layer_available,
        "parse_quality": parse_quality,
        "parse_status": paper.get("parse_status", ""),
        "parse_error": paper.get("parse_error", ""),
        "section_extraction_source": section_info["source"],
        "section_extraction_status": section_info["status"],
        "section_extraction_message": section_info["message"],
        "outline_entries_count": section_info["outline_entries_count"],
        "pdf_url": f"/paper_reading/uploads/{paper_id}.pdf" if upload_path else "",
        "has_pdf": upload_path is not None,
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
        if not paper.get("year"):
            inferred_year = PDFParser.extract_year(
                full_text,
                source_hint=" ".join(
                    str(paper.get(key) or "")
                    for key in ("pdf_url", "url", "source_id", "arxiv_id")
                ),
            )
            if inferred_year:
                paper = dict(paper)
                paper["year"] = inferred_year
        if (
            paper.get("parse_status") in (None, "", "done")
            and paper.get("section_extraction_source") != "pdf_outline"
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
        "[用户问题]\n"
        f"{user_question}\n\n"
        "请优先基于用户选区回答；如果选区不足，再使用同页附近文本、当前章节正文和论文元信息。"
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
        "paper_type": "unknown",
        "map_variant": "research",
        "prerequisite_card": {"concepts": [], "baseline_papers": [], "reading_order": []},
        "research_map": {},
        "survey_map": {},
        "research_problem": {},
        "core_method": {},
        "method_steps": [],
        "experimental_support": [],
        "limitations_and_questions": [],
        "section_guides": [],
    }


def _failed_reading_map(message: str) -> dict[str, Any]:
    reading_map = _empty_reading_map("failed")
    reading_map["error"] = message
    reading_map["llm_error"] = message
    return reading_map


def _llm_visible_base(fallback: dict[str, Any]) -> dict[str, Any]:
    base = _empty_reading_map("pending")
    paper_type = str(fallback.get("paper_type") or "unknown")
    map_variant = str(fallback.get("map_variant") or ("survey" if paper_type == "survey" else "research"))
    if paper_type in {"research", "survey", "theory", "system"}:
        base["paper_type"] = paper_type
    if map_variant in {"research", "survey"}:
        base["map_variant"] = map_variant
    return base


def _reading_map_has_visible_content(reading_map: dict[str, Any]) -> bool:
    survey = reading_map.get("survey_map") if isinstance(reading_map.get("survey_map"), dict) else {}
    research = reading_map.get("research_map") if isinstance(reading_map.get("research_map"), dict) else {}
    has_guides = bool(reading_map.get("section_guides"))
    if reading_map.get("map_variant") == "survey":
        survey_lists = [
            "development_timeline",
            "pain_points",
            "taxonomy",
            "technical_routes",
            "representative_methods",
            "datasets",
            "evaluation_protocols",
            "applications",
            "open_challenges",
        ]
        field_overview = survey.get("field_overview") if isinstance(survey.get("field_overview"), dict) else {}
        map_content = any(field_overview.values()) or any(survey.get(key) for key in survey_lists)
        return map_content and has_guides
    map_content = any(
        reading_map.get(key)
        for key in ("research_problem", "core_method", "method_steps", "experimental_support", "limitations_and_questions")
    ) or any(research.get(key) for key in ("research_problem", "core_method", "method_steps", "experimental_support", "limitations_and_questions"))
    return map_content and has_guides


def _build_reading_map(paper: dict[str, Any]) -> dict[str, Any]:
    sections = paper.get("sections", []) or []
    if not sections:
        return _empty_reading_map("pending")

    paper_type = _infer_paper_type(paper, sections)
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        by_stage.setdefault(_section_role(section, paper_type), []).append(section)

    title = str(paper.get("title") or "Core Method")
    abstract = str(paper.get("abstract") or "")
    intro_sections = by_stage.get("abstract", []) + by_stage.get("introduction", [])
    method_sections = by_stage.get("method", []) or sections[: min(4, len(sections))]
    experiment_sections = by_stage.get("experiment", [])
    conclusion_sections = by_stage.get("conclusion", [])

    intro_text = _compact_section_text(intro_sections, limit=1600) or abstract
    method_text = _compact_section_text(method_sections, limit=1600)

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

    research_problem = {
        "title": "Research Problem",
        "one_sentence": problem_sentence,
        "why_it_matters": _second_sentence(intro_text) or "This explains why the paper's method is needed.",
        "source_sections": [_source_ref(item) for item in intro_sections[:3]],
    }
    core_method = {
        "name": title,
        "one_sentence": method_sentence,
        "main_idea": _second_sentence(method_text) or "Read the method sections to understand the paper's central mechanism.",
        "source_sections": [_source_ref(item) for item in method_sections[:3]],
    }
    research_map = {
        "research_problem": research_problem,
        "core_method": core_method,
        "method_steps": method_steps,
        "experimental_support": experimental_support,
        "limitations_and_questions": limitations,
    }
    survey_map = _build_heuristic_survey_map(paper, sections, by_stage)

    return {
        "version": READING_MAP_VERSION,
        "status": "done",
        "paper_type": paper_type,
        "map_variant": "survey" if paper_type == "survey" else "research",
        "prerequisite_card": _build_heuristic_prerequisite_card(paper, sections, paper_type),
        "research_map": research_map,
        "survey_map": survey_map if paper_type == "survey" else {},
        "research_problem": research_problem,
        "core_method": core_method,
        "method_steps": method_steps,
        "experimental_support": experimental_support,
        "limitations_and_questions": limitations,
        "section_guides": _build_heuristic_section_guides(sections, paper_type),
    }


def _build_llm_reading_map(
    *,
    paper: dict[str, Any],
    fallback: dict[str, Any],
    model: Any | None,
    skill_registry: Any | None,
    storage: Any | None = None,
    paper_id: str = "",
    generation_id: str = "",
) -> dict[str, Any]:
    if model is None:
        return _failed_reading_map("LLM 模型未初始化，无法生成导读地图与智能索引。")
    sections = _research_reading_sections(paper, fallback)
    if not sections:
        return _failed_reading_map("未找到可用于导读地图生成的章节正文。")

    groups = [
        sections[index:index + RESEARCH_GUIDE_SECTIONS_PER_REQUEST]
        for index in range(0, len(sections), RESEARCH_GUIDE_SECTIONS_PER_REQUEST)
    ]
    task_total = 1 + len(groups)
    if not _save_reading_map_phase(
        storage,
        paper_id,
        generation_id,
        phase="generating_research_map",
        progress=5,
        artifacts={
            "research_generation_progress": {
                "total": task_total,
                "completed": 0,
                "failed": 0,
                "sections_total": len(sections),
            }
        },
    ):
        return _failed_reading_map("生成任务已被新一轮请求替换。")

    overview: dict[str, Any] | None = None
    guides_by_id: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    completed = 0
    executor = ThreadPoolExecutor(max_workers=min(RESEARCH_GUIDE_MAX_WORKERS, task_total))
    stop_without_waiting = False
    try:
        future_to_label: dict[Any, str] = {
            executor.submit(
                _generate_research_overview,
                model,
                paper,
                fallback,
                skill_registry,
            ): "研究总览"
        }
        for index, group in enumerate(groups, start=1):
            future_to_label[
                executor.submit(_generate_research_section_guide_group, model, paper, group, fallback)
            ] = f"章节导读组 {index}"

        for future in as_completed(future_to_label):
            if not _generation_is_current(storage, paper_id, generation_id):
                stop_without_waiting = True
                for pending_future in future_to_label:
                    pending_future.cancel()
                return _failed_reading_map("生成任务已被新一轮请求替换。")
            label = future_to_label[future]
            try:
                result = future.result()
                if label == "研究总览":
                    overview = result
                else:
                    for guide in result:
                        section_id = str(guide.get("section_id") or "")
                        if section_id:
                            guides_by_id[section_id] = guide
            except Exception as error:
                logger.warning("Research reading map task failed (%s): %s", label, error)
                failures.append(f"{label}：{error}")
            completed += 1
            _save_reading_map_phase(
                storage,
                paper_id,
                generation_id,
                phase="generating_research_map",
                progress=min(95, 5 + int(completed / task_total * 90)),
                artifacts={
                    "research_generation_progress": {
                        "total": task_total,
                        "completed": completed,
                        "failed": len(failures),
                        "sections_total": len(sections),
                        "sections_generated": len(guides_by_id),
                    }
                },
            )
    finally:
        executor.shutdown(wait=not stop_without_waiting, cancel_futures=stop_without_waiting)

    if overview is None:
        detail = next((item for item in failures if item.startswith("研究总览")), "研究总览未生成")
        return _failed_reading_map(f"导读地图生成失败：{detail}")

    merged = _normalize_reading_map(overview, _llm_visible_base(fallback))
    fallback_guides = {
        str(guide.get("section_id") or ""): guide
        for guide in _build_heuristic_section_guides(sections, str(merged.get("paper_type") or "research"))
        if isinstance(guide, dict) and guide.get("section_id")
    }
    generated_count = len(guides_by_id)
    ordered_guides: list[dict[str, Any]] = []
    fallback_count = 0
    for section in sections:
        section_id = str(section.get("section_id") or "")
        guide = guides_by_id.get(section_id)
        if guide is None:
            guide = fallback_guides.get(section_id)
            fallback_count += int(guide is not None)
            if guide is not None:
                guide = dict(guide)
                guide["fallback_generated"] = True
        if guide is not None:
            ordered_guides.append(guide)
    merged["section_guides"] = _normalize_section_guides(ordered_guides)
    merged["status"] = "llm_done"
    merged["partial"] = bool(failures or fallback_count)
    merged["generation_artifacts_summary"] = {
        "requests_total": task_total,
        "requests_failed": len(failures),
        "sections_total": len(sections),
        "sections_llm_generated": generated_count,
        "sections_fallback_generated": fallback_count,
        "max_workers": min(RESEARCH_GUIDE_MAX_WORKERS, task_total),
    }
    if failures or fallback_count:
        merged["generation_warning"] = "部分章节导读由本地章节内容补全；研究总览和其余智能索引可正常使用。"
    if not _reading_map_has_visible_content(merged):
        return _failed_reading_map("LLM 返回结果缺少可展示内容，导读地图与智能索引生成失败。")
    return merged


def _generate_research_overview(
    model: Any,
    paper: dict[str, Any],
    fallback: dict[str, Any],
    skill_registry: Any | None,
) -> dict[str, Any]:
    response = _reading_map_json_chat(
        model,
        [
            {
                "role": "system",
                "content": (
                    "You are a paper-reading map builder for novice researchers. "
                    "Return only a valid JSON object that follows the requested schema."
                ),
            },
            {"role": "user", "content": _build_reading_map_prompt(paper, fallback, skill_registry)},
        ],
        max_tokens=RESEARCH_OVERVIEW_MAX_TOKENS,
        timeout=RESEARCH_OVERVIEW_REQUEST_TIMEOUT_SECONDS,
    )
    return _reading_map_response_json(
        response,
        label="研究总览",
        max_tokens=RESEARCH_OVERVIEW_MAX_TOKENS,
    )


def _research_reading_sections(
    paper: dict[str, Any],
    fallback: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        section
        for section in (paper.get("sections", []) or [])
        if isinstance(section, dict)
        and str(section.get("content") or "").strip()
        and _section_role(section, str(fallback.get("paper_type") or "research")) != "references"
    ][:RESEARCH_GUIDE_SECTION_LIMIT]


def _generate_research_section_guide_group(
    model: Any,
    paper: dict[str, Any],
    sections: list[dict[str, Any]],
    fallback: dict[str, Any],
) -> list[dict[str, Any]]:
    payload = []
    expected: dict[str, dict[str, Any]] = {}
    paper_type = str(fallback.get("paper_type") or "research")
    for index, section in enumerate(sections, start=1):
        section_id = str(section.get("section_id") or f"section-{index}")
        expected[section_id] = section
        payload.append({
            "section_id": section_id,
            "title": str(section.get("title") or f"Section {index}"),
            "section_role_hint": _section_role(section, paper_type),
            "start_page": section.get("start_page"),
            "end_page": section.get("end_page"),
            "text": _compact_section_for_card(
                section,
                RESEARCH_GUIDE_SECTION_TEXT_LIMIT,
                {"group_key": "research_section_guide"},
            ),
        })
    prompt = (
        "Generate novice-oriented smart-index guides for exactly the supplied sections. Return JSON only. "
        "Do not add sections and do not copy long paragraphs. Each section needs 1-3 concise, substantive cards. "
        "Use source_sections with section_id, title, and page; do not invent facts. Write Chinese content.\n"
        "Schema:\n"
        '{"section_guides": [{"section_id": "", "title": "", "section_role": "", '
        '"read_priority": "high|medium|low", "novice_summary": "", '
        '"cards": [{"card_type": "abstract_takeaway|intro_insight|problem_formulation|method_architecture|algorithm_steps|innovation_detail|experiment_dataset|experiment_design|result_interpretation|limitation_reflection|reading_route", '
        '"title": "", "content": {"core_message": "", "why_it_matters": "", "key_points": [], "connections": [], "next_reading": ""}, "source_sections": []}], '
        '"main_content": "", "core_idea": "", "technical_route": "", "implementation_plan": "", '
        '"datasets": [], "baselines": [], "metrics": [], "novice_focus": "", "source_page": null}]}\n\n'
        f"Paper title: {paper.get('title', '')}\n"
        f"Abstract: {str(paper.get('abstract') or '')[:1200]}\n"
        f"Sections:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    response = _reading_map_json_chat(
        model,
        [
            {"role": "system", "content": "Return only valid JSON for research section guides."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=RESEARCH_GUIDE_MAX_TOKENS,
        timeout=RESEARCH_GUIDE_REQUEST_TIMEOUT_SECONDS,
    )
    parsed = _reading_map_response_json(
        response,
        label="章节导读",
        max_tokens=RESEARCH_GUIDE_MAX_TOKENS,
    )
    raw_guides = parsed.get("section_guides")
    if not isinstance(raw_guides, list):
        raw_guides = [parsed] if parsed.get("section_id") else []
    normalized: list[dict[str, Any]] = []
    for guide in raw_guides:
        if not isinstance(guide, dict):
            continue
        section_id = str(guide.get("section_id") or "")
        section = expected.get(section_id)
        if section is None:
            continue
        guide = dict(guide)
        guide["title"] = guide.get("title") or section.get("title", "")
        guide["section_role"] = guide.get("section_role") or _section_role(section, paper_type)
        guide["read_priority"] = guide.get("read_priority") or "medium"
        guide["source_page"] = guide.get("source_page") or section.get("start_page")
        source_ref = _source_ref(section)
        if not guide.get("source_sections"):
            guide["source_sections"] = [source_ref]
        for card in guide.get("cards") if isinstance(guide.get("cards"), list) else []:
            if isinstance(card, dict) and not card.get("source_sections"):
                card["source_sections"] = [source_ref]
        normalized.append(guide)
    normalized = _normalize_section_guides(normalized)
    if not normalized:
        raise ValueError("章节导读 JSON 中没有匹配输入章节的内容")
    return normalized


def _generate_reading_map_for_paper(
    *,
    paper: dict[str, Any],
    fallback: dict[str, Any],
    model: Any | None,
    skill_registry: Any | None,
    storage: Any | None = None,
    paper_id: str = "",
    generation_id: str = "",
) -> dict[str, Any]:
    if fallback.get("paper_type") == "survey" or fallback.get("map_variant") == "survey":
        return _build_survey_plan_card_reading_map(
            paper=paper,
            fallback=fallback,
            model=model,
            skill_registry=skill_registry,
            storage=storage,
            paper_id=paper_id or str(paper.get("paper_id") or ""),
            generation_id=generation_id,
        )
    return _build_llm_reading_map(
        paper=paper,
        fallback=fallback,
        model=model,
        skill_registry=skill_registry,
        storage=storage,
        paper_id=paper_id or str(paper.get("paper_id") or ""),
        generation_id=generation_id,
    )


def _build_survey_plan_card_reading_map(
    *,
    paper: dict[str, Any],
    fallback: dict[str, Any],
    model: Any | None,
    skill_registry: Any | None,
    storage: Any | None,
    paper_id: str,
    generation_id: str,
) -> dict[str, Any]:
    if model is None:
        return _failed_reading_map("LLM 模型未初始化，无法生成综述导读地图。")
    manifest = _survey_section_manifest(paper)
    if not manifest:
        return _failed_reading_map("未找到可用于综述导读地图生成的章节正文。")

    skill_instructions = _survey_skill_instructions(skill_registry)
    skill_hash = hashlib.sha1(skill_instructions.encode("utf-8")).hexdigest() if skill_instructions else ""
    artifacts = paper.get("reading_map_artifacts") if isinstance(paper.get("reading_map_artifacts"), dict) else {}
    cached_results = artifacts.get("survey_card_results") if isinstance(artifacts.get("survey_card_results"), dict) else {}

    if not _save_reading_map_phase(
        storage,
        paper_id,
        generation_id,
        phase="planning_sections",
        progress=5,
        artifacts={
            "survey_section_manifest": manifest,
            "survey_card_progress": _survey_card_progress(0, 0, 0, "规划综述卡片"),
            "survey_skill_hash": skill_hash,
        },
    ):
        return _failed_reading_map("生成任务已被新一轮请求替换。")

    try:
        raw_plan = _plan_survey_cards(model, paper, manifest, skill_instructions)
        plan = _normalize_survey_card_plan(raw_plan, manifest)
        plan = _ensure_survey_prerequisite_task(plan, manifest)
    except Exception as error:
        return _failed_reading_map(f"综述卡片规划失败：{error}")
    tasks = plan.get("tasks", [])
    if not tasks:
        return _failed_reading_map("综述卡片规划结果为空，请重新生成。")

    card_results = dict(cached_results) if isinstance(cached_results, dict) else {}
    if not _save_reading_map_phase(
        storage,
        paper_id,
        generation_id,
        phase="generating_cards",
        progress=10,
        artifacts={
            "survey_section_manifest": manifest,
            "survey_card_plan": plan,
            "survey_card_results": card_results,
            "survey_card_progress": _survey_card_progress(0, len(tasks), 0, ""),
            "survey_skill_hash": skill_hash,
        },
    ):
        return _failed_reading_map("生成任务已被新一轮请求替换。")

    completed = 0
    failed = 0
    pending_tasks: list[tuple[dict[str, Any], str]] = []
    for task in tasks:
        task_id = str(task.get("task_id") or "")
        section_text_hash = _survey_task_section_text_hash(task, paper)
        cached = card_results.get(task_id) if isinstance(card_results, dict) else None
        if (
            isinstance(cached, dict)
            and cached.get("status") == "ok"
            and cached.get("section_text_hash") == section_text_hash
            and cached.get("skill_hash", "") == skill_hash
            and cached.get("task_hash") == task.get("task_hash")
            and isinstance(cached.get("result"), dict)
        ):
            completed += 1
        else:
            pending_tasks.append((task, section_text_hash))

    if not _generation_is_current(storage, paper_id, generation_id):
        return _failed_reading_map("生成任务已被新一轮请求替换。")
    executor = ThreadPoolExecutor(max_workers=min(SURVEY_CARD_MAX_WORKERS, max(1, len(pending_tasks))))
    stop_without_waiting = False
    try:
        future_to_task = {
            executor.submit(_generate_survey_card, model, paper, task, skill_instructions): (task, section_text_hash)
            for task, section_text_hash in pending_tasks
        }
        for future in as_completed(future_to_task):
            if not _generation_is_current(storage, paper_id, generation_id):
                stop_without_waiting = True
                for pending_future in future_to_task:
                    pending_future.cancel()
                return _failed_reading_map("生成任务已被新一轮请求替换。")
            task, section_text_hash = future_to_task[future]
            task_id = str(task.get("task_id") or "")
            current_title = str(task.get("title") or task_id)
            try:
                result = future.result()
                card_results[task_id] = {
                    "status": "ok",
                    "section_text_hash": section_text_hash,
                    "skill_hash": skill_hash,
                    "task_hash": task.get("task_hash"),
                    "task": task,
                    "result": result,
                }
                completed += 1
            except Exception as error:
                failed += 1
                card_results[task_id] = {
                    "status": "failed",
                    "section_text_hash": section_text_hash,
                    "skill_hash": skill_hash,
                    "task_hash": task.get("task_hash"),
                    "task": task,
                    "error": str(error),
                }

            partial_map = _build_survey_reading_map_from_card_results(
                paper=paper,
                fallback=fallback,
                plan=plan,
                card_results=card_results,
                status="llm_running",
                partial=True,
            )
            if not _save_survey_partial_reading_map(
                storage,
                paper_id,
                generation_id,
                reading_map=partial_map,
                phase="generating_cards",
                progress=_survey_card_generation_progress(completed + failed, len(tasks)),
                artifacts={
                    "survey_section_manifest": manifest,
                    "survey_card_plan": plan,
                    "survey_card_results": card_results,
                    "survey_card_progress": _survey_card_progress(completed, len(tasks), failed, current_title),
                    "survey_skill_hash": skill_hash,
                },
            ):
                stop_without_waiting = True
                for pending_future in future_to_task:
                    pending_future.cancel()
                return _failed_reading_map("生成任务已被新一轮请求替换。")
    finally:
        executor.shutdown(wait=not stop_without_waiting, cancel_futures=stop_without_waiting)

    if not _save_reading_map_phase(storage, paper_id, generation_id, phase="finalizing_map", progress=95):
        return _failed_reading_map("生成任务已被新一轮请求替换。")

    final_map = _build_survey_reading_map_from_card_results(
        paper=paper,
        fallback=fallback,
        plan=plan,
        card_results=card_results,
        status="llm_done",
        partial=False,
    )
    if _survey_partial_has_core_content(final_map):
        phase = "llm_done"
        progress = 100
        error = ""
        final_map["status"] = "llm_done"
        final_map["partial"] = False
    else:
        phase = "failed_partial"
        progress = _survey_card_generation_progress(completed + failed, len(tasks))
        error = "综述导读地图核心卡片不足，请重新生成。"
        final_map["status"] = "failed_partial"
        final_map["partial"] = True
        final_map["error"] = error
        final_map["llm_error"] = error
    final_map["paper_type"] = "survey"
    final_map["map_variant"] = "survey"
    final_map["generation_artifacts_summary"] = {
        "cards_total": len(tasks),
        "cards_completed": sum(1 for item in card_results.values() if isinstance(item, dict) and item.get("status") == "ok"),
        "cards_failed": sum(1 for item in card_results.values() if isinstance(item, dict) and item.get("status") == "failed"),
        "cards_reused": sum(
            1
            for task in tasks
            if isinstance(cached_results.get(task["task_id"]), dict)
            and cached_results.get(task["task_id"], {}).get("status") == "ok"
        ),
        "survey_skill_hash": skill_hash,
    }
    _save_survey_partial_reading_map(
        storage,
        paper_id,
        generation_id,
        reading_map=final_map,
        phase=phase,
        progress=progress,
        error=error,
        artifacts={
            "survey_section_manifest": manifest,
            "survey_card_plan": plan,
            "survey_card_results": card_results,
            "survey_card_progress": _survey_card_progress(completed, len(tasks), failed, ""),
            "survey_skill_hash": skill_hash,
        },
    )
    return final_map


def _survey_section_manifest(paper: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = []
    for index, section in enumerate(paper.get("sections", []) or [], start=1):
        if _section_role(section, "survey") == "references":
            continue
        text = " ".join(str(section.get("content") or "").split())
        if not text:
            continue
        manifest.append({
            "section_index": index,
            "section_id": str(section.get("section_id") or f"section-{index}"),
            "title": str(section.get("title") or f"Section {index}"),
            "level": section.get("level", 1),
            "start_page": section.get("start_page"),
            "end_page": section.get("end_page"),
            "section_role_hint": _section_role(section, "survey"),
            "text_length": len(text),
            "signal_terms": _survey_signal_terms(text),
            "citation_count_hint": _citation_count_hint(text),
            "named_entities_hint": _named_entities_hint(text),
            "table_figure_refs": _table_figure_refs(text),
            "summary_excerpt": _survey_section_excerpt(text),
        })
    return manifest


def _survey_section_excerpt(text: str, limit: int = 900) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.55)]
    tail = text[-int(limit * 0.30):]
    windows = _keyword_windows(
        text,
        (
            "et al.",
            "citation",
            "propose",
            "approach",
            "framework",
            "algorithm",
            "baseline",
            "compare",
            "comparison",
            "sota",
            "table",
            "figure",
            "dataset",
            "benchmark",
            "metric",
            "evaluation",
            "taxonomy",
            "challenge",
            "future",
            "agent",
            "memory",
            "rag",
        ),
        int(limit * 0.55),
        max_windows=3,
    )
    return " ... ".join(part for part in (head, *windows, tail) if part)[:limit]


def _keyword_window(text: str, needles: tuple[str, ...], limit: int) -> str:
    lower = text.lower()
    hit = min((lower.find(needle) for needle in needles if lower.find(needle) >= 0), default=-1)
    if hit < 0:
        return ""
    start = max(0, hit - limit // 2)
    end = min(len(text), start + limit)
    return text[start:end].strip()


def _keyword_windows(text: str, needles: tuple[str, ...], limit: int, *, max_windows: int = 3) -> list[str]:
    text = str(text or "")
    if not text or limit <= 0:
        return []
    lower = text.lower()
    hits: list[int] = []
    for needle in needles:
        start = 0
        lowered_needle = needle.lower()
        while lowered_needle:
            hit = lower.find(lowered_needle, start)
            if hit < 0:
                break
            if all(abs(hit - existing) > max(180, limit // max(1, max_windows)) for existing in hits):
                hits.append(hit)
            start = hit + max(1, len(lowered_needle))
            if len(hits) >= max_windows * 3:
                break
    if not hits:
        return []
    window_limit = max(180, limit // max_windows)
    windows = []
    for hit in sorted(hits)[:max_windows]:
        start = max(0, hit - window_limit // 2)
        end = min(len(text), start + window_limit)
        windows.append(text[start:end].strip())
    return windows


def _survey_signal_terms(text: str) -> list[str]:
    terms = (
        "et al.",
        "2020",
        "2021",
        "2022",
        "2023",
        "2024",
        "2025",
        "table",
        "figure",
        "benchmark",
        "dataset",
        "baseline",
        "compare",
        "comparison",
        "sota",
        "state-of-the-art",
        "framework",
        "algorithm",
        "approach",
        "method",
        "taxonomy",
        "evaluation",
        "metric",
        "challenge",
        "limitation",
        "future",
    )
    lowered = str(text or "").lower()
    hits = [term for term in terms if term in lowered]
    years = sorted(set(re.findall(r"\b20\d{2}\b", str(text or ""))))[:8]
    return list(dict.fromkeys([*hits, *years]))[:20]


def _citation_count_hint(text: str) -> int:
    raw = str(text or "")
    bracket_citations = len(re.findall(r"\[(?:\d+\s*,?\s*){1,6}\]", raw))
    author_year = len(re.findall(r"\b[A-Z][A-Za-z-]+ et al\.\s*,?\s*(?:19|20)\d{2}\b", raw))
    years = len(re.findall(r"\b(?:19|20)\d{2}\b", raw))
    return min(999, bracket_citations + author_year + years)


def _named_entities_hint(text: str, limit: int = 14) -> list[str]:
    raw = " ".join(str(text or "").split())
    candidates = re.findall(
        r"\b(?:[A-Z][A-Za-z0-9+-]{2,}|[A-Z]{2,})(?:[-\s](?:[A-Z][A-Za-z0-9+-]{2,}|[A-Z]{2,})){0,5}\b",
        raw,
    )
    stop = {"The", "This", "That", "Section", "Figure", "Table", "References", "Abstract", "Introduction"}
    entities: list[str] = []
    for candidate in candidates:
        cleaned = candidate.strip(" ,.;:()[]")
        if not cleaned or cleaned in stop or cleaned.lower() in {"agent", "agents", "model", "models", "memory"}:
            continue
        if cleaned not in entities:
            entities.append(cleaned)
        if len(entities) >= limit:
            break
    return entities


def _table_figure_refs(text: str, limit: int = 12) -> list[str]:
    refs = re.findall(r"\b(?:Table|Figure|Fig\.)\s*\d+[A-Za-z]?\b", str(text or ""), flags=re.IGNORECASE)
    unique: list[str] = []
    for ref in refs:
        normalized = " ".join(ref.split())
        if normalized not in unique:
            unique.append(normalized)
        if len(unique) >= limit:
            break
    return unique


def _plan_survey_cards(
    model: Any,
    paper: dict[str, Any],
    manifest: list[dict[str, Any]],
    skill_instructions: str,
) -> dict[str, Any]:
    skill_block = f"Skill instructions:\n{skill_instructions}\n\n" if skill_instructions else ""
    prompt = (
        f"{skill_block}"
        "You are planning a novice-oriented reading map for a survey paper. "
        "Use the section manifest to decide which sections should be read to generate each card. "
        "Do not ask for every section by default; choose the most relevant sections for each card. "
        "Return JSON only. Use section_id for binding; section_index is only a helper.\n"
        "Actively search for every core group instead of treating them as optional. "
        "field_overview is required. taxonomy and technical_routes should each have 1-3 tasks when evidence exists. "
        "If the manifest exposes citation_count_hint, years, et al., Table/Figure, benchmark, baseline, comparison, framework, algorithm, or named_entities_hint, "
        "you must plan representative_methods: either 3-8 specific method tasks or one aggregate method task that can return items[]. "
        "Plan datasets, evaluation_protocols, and open_challenges whenever the manifest has evidence; if a core group is omitted, include a concise omission reason.\n"
        "Schema:\n"
        "{\n"
        '  "map_tasks": [{"task_id": "", "group_key": "field_overview|development_timeline|pain_points|taxonomy|technical_routes|representative_methods|datasets|evaluation_protocols|applications|open_challenges", "title": "", "goal": "", "priority": "high|medium|low", "section_ids": [], "section_indices": [], "evidence_reason": "", "expected_output_fields": [], "output_hint": ""}],\n'
        '  "section_guide_tasks": [{"task_id": "", "section_id": "", "section_index": null, "title": "", "goal": "", "priority": "high|medium|low", "card_types": [], "section_ids": [], "section_indices": [], "evidence_reason": "", "expected_output_fields": []}],\n'
        '  "omissions": [{"group_key": "", "reason": ""}]\n'
        "}\n"
        "Planning requirements: include high-value map tasks for field_overview, development_timeline, pain_points, taxonomy, technical_routes, representative_methods, datasets, evaluation_protocols, applications, and open_challenges when evidence exists. "
        "For representative_methods, prioritize sections with citation/year/method/table/benchmark/comparison signals and set expected_output_fields to paper_title, year, method_name, route, problem_addressed, core_mechanism, specific_solution, improves_on, limitations, evidence, source_sections. "
        "For datasets, set expected_output_fields to name, task, content, structure, scale, metrics, used_by_methods, evidence, source_sections. "
        "Create section_guide_tasks for important non-reference sections; each should target 2-4 cards. "
        f"Keep map_tasks <= {SURVEY_MAP_TASK_LIMIT} and section_guide_tasks <= {SURVEY_SECTION_GUIDE_TASK_LIMIT}. "
        "Prefer high-value coverage over many small tasks. Write Chinese titles/goals.\n\n"
        f"Paper title: {paper.get('title', '')}\n"
        f"Abstract: {str(paper.get('abstract') or '')[:1600]}\n"
        f"Section manifest:\n{json.dumps(manifest, ensure_ascii=False)}"
    )
    response = _reading_map_json_chat(
        model,
        [
            {"role": "system", "content": "Return only valid JSON for survey card planning."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=SURVEY_PLAN_MAX_TOKENS,
        timeout=SURVEY_PLAN_REQUEST_TIMEOUT_SECONDS,
    )
    return _reading_map_response_json(
        response,
        label="综述卡片规划",
        max_tokens=SURVEY_PLAN_MAX_TOKENS,
    )


def _normalize_survey_card_plan(plan: dict[str, Any], manifest: list[dict[str, Any]]) -> dict[str, Any]:
    section_by_id = {str(item.get("section_id")): item for item in manifest}
    section_by_index = {int(item.get("section_index") or 0): item for item in manifest}
    tasks: list[dict[str, Any]] = []

    def valid_section_ids(raw_ids: Any, raw_indices: Any) -> list[str]:
        ids = []
        for value in raw_ids if isinstance(raw_ids, list) else []:
            section_id = str(value)
            if section_id in section_by_id and section_id not in ids:
                ids.append(section_id)
        for value in raw_indices if isinstance(raw_indices, list) else []:
            try:
                section = section_by_index.get(int(value))
            except (TypeError, ValueError):
                section = None
            section_id = str(section.get("section_id")) if section else ""
            if section_id and section_id not in ids:
                ids.append(section_id)
        return ids[:6]

    for index, item in enumerate(plan.get("map_tasks") if isinstance(plan.get("map_tasks"), list) else [], start=1):
        if not isinstance(item, dict):
            continue
        group_key = str(item.get("group_key") or "")
        if group_key not in SURVEY_MAP_GROUP_KEYS:
            continue
        section_ids = valid_section_ids(item.get("section_ids"), item.get("section_indices"))
        if not section_ids:
            continue
        task = {
            "task_id": str(item.get("task_id") or f"map:{group_key}:{index}"),
            "target": "survey_map",
            "group_key": group_key,
            "title": str(item.get("title") or reading_map_group_title(group_key)),
            "goal": str(item.get("goal") or item.get("output_hint") or ""),
            "priority": str(item.get("priority") or "medium"),
            "section_ids": section_ids,
            "section_indices": [section_by_id[section_id].get("section_index") for section_id in section_ids],
            "evidence_reason": str(item.get("evidence_reason") or ""),
            "expected_output_fields": [str(field) for field in item.get("expected_output_fields", []) if field],
            "output_hint": str(item.get("output_hint") or ""),
        }
        task["task_hash"] = _survey_task_hash(task)
        tasks.append(task)

    for index, item in enumerate(plan.get("section_guide_tasks") if isinstance(plan.get("section_guide_tasks"), list) else [], start=1):
        if not isinstance(item, dict):
            continue
        primary_id = str(item.get("section_id") or "")
        section_ids = valid_section_ids(item.get("section_ids") or [primary_id], item.get("section_indices") or [item.get("section_index")])
        if not section_ids:
            continue
        primary_id = primary_id if primary_id in section_by_id else section_ids[0]
        task = {
            "task_id": str(item.get("task_id") or f"guide:{primary_id}:{index}"),
            "target": "section_guide",
            "group_key": "section_guides",
            "section_id": primary_id,
            "title": str(item.get("title") or section_by_id.get(primary_id, {}).get("title") or "章节导读"),
            "goal": str(item.get("goal") or ""),
            "priority": str(item.get("priority") or "medium"),
            "card_types": [str(card_type) for card_type in item.get("card_types", []) if card_type],
            "section_ids": section_ids,
            "section_indices": [section_by_id[section_id].get("section_index") for section_id in section_ids],
            "evidence_reason": str(item.get("evidence_reason") or ""),
            "expected_output_fields": [str(field) for field in item.get("expected_output_fields", []) if field],
        }
        task["task_hash"] = _survey_task_hash(task)
        tasks.append(task)

    priority_order = {"high": 0, "medium": 1, "low": 2}

    def prioritized(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda task: priority_order.get(str(task.get("priority") or "medium"), 1),
        )[:limit]

    map_tasks = prioritized(
        [task for task in tasks if task.get("target") == "survey_map"],
        SURVEY_MAP_TASK_LIMIT,
    )
    section_guide_tasks = prioritized(
        [task for task in tasks if task.get("target") == "section_guide"],
        SURVEY_SECTION_GUIDE_TASK_LIMIT,
    )
    bounded_tasks = [*map_tasks, *section_guide_tasks]
    return {
        "version": SURVEY_CARD_PLAN_VERSION,
        "map_tasks_count": len(map_tasks),
        "section_guide_tasks_count": len(section_guide_tasks),
        "tasks": bounded_tasks,
    }


def _ensure_survey_prerequisite_task(plan: dict[str, Any], manifest: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = list(plan.get("tasks") if isinstance(plan.get("tasks"), list) else [])
    if any(task.get("target") == "prerequisite_card" for task in tasks if isinstance(task, dict)):
        return plan
    intro_items = [
        item for item in manifest
        if str(item.get("section_role_hint") or "").lower() in {"abstract", "introduction", "background", "overview", "preliminaries"}
        or re.search(r"\b(abstract|introduction|background|overview|preliminar)", str(item.get("title") or ""), flags=re.IGNORECASE)
    ]
    if not intro_items and manifest:
        intro_items = manifest[:2]
    section_ids = [str(item.get("section_id")) for item in intro_items[:4] if item.get("section_id")]
    if not section_ids:
        return plan
    task = {
        "task_id": "intro:prerequisite_card",
        "target": "prerequisite_card",
        "group_key": "prerequisite_card",
        "title": "前置知识",
        "goal": "从论文开篇提取新手阅读本综述前需要理解的概念、领域问题、阅读顺序、锚点论文和易混点。",
        "priority": "high",
        "section_ids": section_ids,
        "section_indices": [item.get("section_index") for item in intro_items[:4]],
        "card_types": ["prerequisite_concepts", "field_questions", "reading_route", "anchor_works"],
        "evidence_reason": "Intro-like sections define the field entry, author framing, questions, roadmap, and novice prerequisites.",
        "expected_output_fields": ["concepts", "field_questions", "reading_order", "anchor_works", "common_confusions"],
    }
    task["task_hash"] = _survey_task_hash(task)
    return {
        **plan,
        "prerequisite_tasks_count": 1,
        "tasks": [task, *tasks],
    }


def reading_map_group_title(group_key: str) -> str:
    return {
        "field_overview": "领域概览",
        "development_timeline": "发展历程",
        "pain_points": "难点痛点",
        "taxonomy": "分类体系",
        "technical_routes": "技术路线",
        "representative_methods": "代表论文方法",
        "datasets": "公开数据集",
        "evaluation_protocols": "评测方式",
        "applications": "应用场景",
        "open_challenges": "开放问题",
    }.get(group_key, "综述卡片")


def _survey_task_hash(task: dict[str, Any]) -> str:
    payload = {
        key: task.get(key)
        for key in (
            "target",
            "group_key",
            "section_id",
            "title",
            "goal",
            "section_ids",
            "card_types",
            "output_hint",
            "evidence_reason",
            "expected_output_fields",
        )
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _survey_task_sections(task: dict[str, Any], paper: dict[str, Any]) -> list[dict[str, Any]]:
    section_ids = [str(item) for item in task.get("section_ids", []) if item]
    by_id = {str(section.get("section_id") or ""): section for section in paper.get("sections", []) or []}
    return [by_id[section_id] for section_id in section_ids if section_id in by_id]


def _survey_task_section_text_hash(task: dict[str, Any], paper: dict[str, Any]) -> str:
    sections = _survey_task_sections(task, paper)
    intro_sections = _survey_intro_sections(paper)
    payload = [
        {
            "section_id": section.get("section_id", ""),
            "title": section.get("title", ""),
            "text": " ".join(str(section.get("content") or "").split()),
        }
        for section in sections
    ]
    if task.get("target") != "prerequisite_card":
        payload.append({
            "section_id": "__intro_context__",
            "title": "Intro context",
            "text": " ".join(" ".join(str(section.get("content") or "").split()) for section in intro_sections),
        })
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _survey_intro_sections(paper: dict[str, Any]) -> list[dict[str, Any]]:
    sections = [section for section in paper.get("sections", []) or [] if isinstance(section, dict)]
    intro_like = []
    for section in sections:
        role = _section_role(section, "survey")
        title = str(section.get("title") or "")
        if role in {"abstract", "introduction", "background"} or re.search(
            r"\b(abstract|introduction|background|overview|preliminar)", title, flags=re.IGNORECASE
        ):
            intro_like.append(section)
    if intro_like:
        return intro_like[:4]
    return [section for section in sections if _section_role(section, "survey") != "references"][:2]


def _survey_intro_context(paper: dict[str, Any], limit: int = SURVEY_INTRO_CONTEXT_LIMIT) -> str:
    remaining = limit
    blocks = []
    for section in _survey_intro_sections(paper):
        if remaining <= 0:
            break
        compact = _compact_intro_section(section, min(SURVEY_CARD_SECTION_TEXT_LIMIT, remaining))
        remaining -= len(compact)
        blocks.append(
            "INTRO_SECTION "
            + json.dumps({
                "section_id": section.get("section_id", ""),
                "title": section.get("title", ""),
                "page": section.get("start_page"),
                "truncated": len(" ".join(str(section.get("content") or "").split())) > len(compact),
            }, ensure_ascii=False)
            + "\n"
            + compact
        )
    return "\n\n".join(blocks)


def _compact_intro_section(section: dict[str, Any], limit: int) -> str:
    text = " ".join(str(section.get("content") or "").split())
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.55)]
    windows = _keyword_windows(
        text,
        (
            "motivation",
            "challenge",
            "question",
            "insight",
            "roadmap",
            "contribution",
            "taxonomy",
            "survey",
            "overview",
            "development",
            "future",
            "open problem",
            "baseline",
            "benchmark",
        ),
        int(limit * 0.30),
        max_windows=2,
    )
    tail = text[-int(limit * 0.15):]
    return "\n...\n".join(part for part in (head, *windows, tail) if part)[:limit]


def _survey_card_output_schema(task: dict[str, Any]) -> str:
    target = str(task.get("target") or "")
    group_key = str(task.get("group_key") or "")
    if target == "prerequisite_card":
        schema = {
            "prerequisite_card": {
                "concepts": [{"name": "", "why_needed": "", "learn_first": [], "difficulty": "easy|medium|hard", "evidence": "", "source_sections": []}],
                "field_questions": [{"question": "", "why_it_matters": "", "intro_evidence": "", "source_sections": []}],
                "reading_order": [{"step": "", "read": "", "why": "", "source_sections": []}],
                "anchor_works": [{"title": "", "year": "", "relationship": "", "why_read": "", "url": "", "evidence": "", "source_sections": []}],
                "common_confusions": [{"pair": "", "difference": "", "why_confusing": "", "evidence": "", "source_sections": []}],
            }
        }
    elif target == "section_guide":
        schema = {
            "section_id": "",
            "title": "",
            "section_role": "",
            "read_priority": "high|medium|low",
            "novice_summary": "",
            "cards": [{
                "card_type": "reading_route|field_timeline|taxonomy_node|route_comparison|paper_method_table|dataset_catalog|benchmark_protocol|challenge_card|application_landscape|future_direction",
                "title": "",
                "content": {
                    "core_message": "",
                    "why_it_matters": "",
                    "key_points": [],
                    "connections": "",
                    "next_reading": "",
                },
                "source_sections": [],
            }],
        }
    elif group_key == "representative_methods":
        schema = {"items": [{"paper_title": "", "year": "", "method_name": "", "route": "", "problem_addressed": "", "core_mechanism": "", "specific_solution": "", "improves_on": "", "limitations": "", "evidence": "", "source_sections": []}]}
    elif group_key == "datasets":
        schema = {"items": [{"name": "", "task": "", "content": "", "structure": "", "scale": "", "metrics": "", "used_by_methods": [], "evidence": "", "source_sections": []}]}
    elif group_key == "technical_routes":
        schema = {"items": [{"route_name": "", "core_mechanism": "", "typical_flow": "", "strengths": "", "limitations": "", "representative_methods": [], "evidence": "", "source_sections": []}]}
    elif group_key == "taxonomy":
        schema = {"items": [{"category": "", "basis": "", "typical_methods": [], "problem_fit": "", "limitations": "", "evidence": "", "source_sections": []}]}
    elif group_key == "open_challenges":
        schema = {"items": [{"challenge": "", "why_hard": "", "impact": "", "existing_attempts": "", "possible_directions": "", "evidence": "", "source_sections": []}]}
    elif group_key == "development_timeline":
        schema = {"items": [{"stage": "", "time_range": "", "key_change": "", "representative_work": "", "why_important": "", "evidence": "", "source_sections": []}]}
    elif group_key == "field_overview":
        schema = {"item": {"title": "", "field_scope": "", "core_tasks": [], "why_now": "", "novice_takeaway": "", "common_misunderstanding": "", "evidence": "", "source_sections": []}}
    else:
        schema = {"items": [{"title": "", "summary": "", "why_it_matters": "", "specific_points": [], "limitations": "", "evidence": "", "source_sections": []}]}
    return json.dumps(schema, ensure_ascii=False, indent=2)


def _generate_survey_card(
    model: Any,
    paper: dict[str, Any],
    task: dict[str, Any],
    skill_instructions: str,
) -> dict[str, Any]:
    sections = _survey_task_sections(task, paper)
    if not sections:
        raise ValueError(f"No valid sections for {task.get('task_id')}")
    selected_context = _survey_task_context(sections, SURVEY_CARD_CONTEXT_LIMIT, task)
    intro_context = selected_context if task.get("target") == "prerequisite_card" else _survey_intro_context(paper)
    skill_block = f"Skill instructions:\n{skill_instructions}\n\n" if skill_instructions else ""
    target = str(task.get("target") or "")
    group_key = str(task.get("group_key") or "")
    output_schema = _survey_card_output_schema(task)
    prompt = (
        f"{skill_block}"
        "Generate exactly one structured survey reading card task. Return JSON only. "
        "Use Intro context for field framing, novice prerequisites, research questions, and author roadmap. "
        "Use Selected section text as the primary evidence for the requested card. "
        "Do not invent paper titles, dataset names, URLs, years, or claims. "
        "Every card/item must include source_sections and concise evidence. Each formal item should contain at least 4-6 useful fields; "
        "if evidence is insufficient, return insufficient_evidence: true with a short reason. "
        "Avoid Item 1, Point 1, Front., Comput., only one-sentence summaries, or section-title-only content. "
        "Write Chinese content for novice researchers.\n\n"
        f"Task:\n{json.dumps(task, ensure_ascii=False)}\n"
        f"Target group: {group_key}\n"
        f"Output schema:\n{output_schema}\n\n"
        f"Paper title: {paper.get('title', '')}\n"
        f"Abstract: {str(paper.get('abstract') or '')[:1000]}\n"
        f"Intro context:\n{intro_context}\n\n"
        f"Selected section text:\n{selected_context}"
    )
    response = _reading_map_json_chat(
        model,
        [
            {"role": "system", "content": "Return only valid JSON for one survey card task."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=SURVEY_CARD_MAX_TOKENS,
        timeout=SURVEY_CARD_REQUEST_TIMEOUT_SECONDS,
    )
    parsed = _reading_map_response_json(
        response,
        label=f"综述卡片 {task.get('task_id')}",
        max_tokens=SURVEY_CARD_MAX_TOKENS,
    )
    return _normalize_survey_card_result(parsed, task, sections)


def _survey_task_context(sections: list[dict[str, Any]], limit: int, task: dict[str, Any] | None = None) -> str:
    remaining = limit
    blocks = []
    for section in sections:
        if remaining <= 0:
            break
        compact = _compact_section_for_card(section, min(SURVEY_CARD_SECTION_TEXT_LIMIT, remaining), task or {})
        remaining -= len(compact)
        blocks.append(
            "SECTION "
            + json.dumps({
                "section_id": section.get("section_id", ""),
                "title": section.get("title", ""),
                "page": section.get("start_page"),
                "truncated": len(" ".join(str(section.get("content") or "").split())) > len(compact),
            }, ensure_ascii=False)
            + "\n"
            + compact
        )
    return "\n\n".join(blocks)


def _compact_section_for_card(section: dict[str, Any], limit: int, task: dict[str, Any] | None = None) -> str:
    text = " ".join(str(section.get("content") or "").split())
    if len(text) <= limit:
        return text
    group_key = str((task or {}).get("group_key") or "")
    if group_key == "representative_methods":
        needles = (
            "et al.",
            "propose",
            "proposed",
            "approach",
            "method",
            "algorithm",
            "framework",
            "baseline",
            "compare",
            "comparison",
            "sota",
            "table",
            "figure",
            "benchmark",
            "state-of-the-art",
        )
    elif group_key == "datasets":
        needles = ("dataset", "benchmark", "corpus", "task", "metric", "evaluation", "table", "leaderboard", "annotation")
    elif group_key == "evaluation_protocols":
        needles = ("evaluation", "metric", "benchmark", "protocol", "baseline", "compare", "table", "setting", "split")
    elif group_key == "open_challenges":
        needles = ("challenge", "limitation", "future", "open problem", "unsolved", "bottleneck", "difficult", "risk")
    elif group_key in {"taxonomy", "technical_routes"}:
        needles = ("taxonomy", "categor", "route", "paradigm", "framework", "method", "approach", "mechanism", "architecture")
    else:
        needles = ("dataset", "benchmark", "method", "model", "taxonomy", "challenge", "future", "evaluation", "paper", "propose")
    head = text[: int(limit * 0.45)]
    windows = _keyword_windows(text, needles, int(limit * 0.35), max_windows=3)
    year_window = _keyword_windows(text, tuple(sorted(set(re.findall(r"\b20\d{2}\b", text)))[:8]), int(limit * 0.20), max_windows=1)
    tail = text[-int(limit * 0.20):]
    return "\n...\n".join(part for part in (head, *windows, *year_window, tail) if part)[:limit]


def _normalize_survey_card_result(
    parsed: dict[str, Any],
    task: dict[str, Any],
    sections: list[dict[str, Any]],
) -> dict[str, Any]:
    source_sections = [_source_ref(section) for section in sections]
    target = str(task.get("target") or "")
    if target == "prerequisite_card":
        card = parsed.get("prerequisite_card") if isinstance(parsed.get("prerequisite_card"), dict) else parsed
        card = dict(card) if isinstance(card, dict) else {}
        _ensure_fact_sources(card, source_sections[0] if source_sections else {})
        for key in ("concepts", "field_questions", "reading_order", "anchor_works", "common_confusions"):
            if key not in card or not isinstance(card.get(key), list):
                card[key] = []
        return {"target": "prerequisite_card", "prerequisite_card": card, "source_sections": source_sections}

    if target == "survey_map":
        raw_items: list[Any]
        if isinstance(parsed.get("items"), list) and parsed["items"]:
            raw_items = parsed["items"]
        elif isinstance(parsed.get("item"), dict):
            raw_items = [parsed["item"]]
        else:
            raw_items = [parsed]
        normalized_items = []
        for raw_item in raw_items[:12]:
            item = dict(raw_item) if isinstance(raw_item, dict) else {}
            if not item:
                continue
            if not item.get("source_sections"):
                item["source_sections"] = source_sections
            if not item.get("evidence"):
                item["evidence"] = item.get("summary") or item.get("why_it_matters") or item.get("core_mechanism") or ""
            _ensure_fact_sources(item, source_sections[0] if source_sections else {})
            normalized_items.append(item)
        if not normalized_items:
            raise ValueError(f"No survey map items for {task.get('task_id')}")
        return {"target": "survey_map", "group_key": task.get("group_key"), "items": normalized_items}

    cards = parsed.get("cards") if isinstance(parsed.get("cards"), list) else []
    normalized_cards = []
    for card in cards[:6]:
        if not isinstance(card, dict):
            continue
        card = dict(card)
        if not card.get("source_sections"):
            card["source_sections"] = source_sections
        if not card.get("card_type"):
            card["card_type"] = (task.get("card_types") or ["reading_route"])[0]
        normalized_cards.append(card)
    if not normalized_cards:
        raise ValueError(f"No cards for {task.get('task_id')}")
    primary = sections[0] if sections else {}
    return {
        "target": "section_guide",
        "section_id": str(task.get("section_id") or primary.get("section_id") or ""),
        "title": parsed.get("title") or task.get("title") or primary.get("title", ""),
        "section_role": parsed.get("section_role") or _section_role(primary, "survey"),
        "read_priority": parsed.get("read_priority") or task.get("priority") or "medium",
        "novice_summary": parsed.get("novice_summary") or "",
        "cards": normalized_cards,
        "source_sections": source_sections,
    }


def _build_survey_reading_map_from_card_results(
    *,
    paper: dict[str, Any],
    fallback: dict[str, Any],
    plan: dict[str, Any],
    card_results: dict[str, Any],
    status: str,
    partial: bool,
) -> dict[str, Any]:
    base = _llm_visible_base(fallback)
    survey_map = _empty_survey_map()
    prerequisite_card: dict[str, Any] = {}
    section_guides_by_id: dict[str, dict[str, Any]] = {}
    for task in plan.get("tasks", []):
        cached = card_results.get(task.get("task_id")) if isinstance(card_results, dict) else None
        if not isinstance(cached, dict) or cached.get("status") != "ok" or not isinstance(cached.get("result"), dict):
            continue
        result = cached["result"]
        if result.get("target") == "prerequisite_card":
            raw_card = result.get("prerequisite_card")
            if isinstance(raw_card, dict):
                prerequisite_card = raw_card
        elif result.get("target") == "survey_map":
            group_key = str(result.get("group_key") or task.get("group_key") or "")
            items = result.get("items") if isinstance(result.get("items"), list) else [result.get("item")]
            for item in items:
                _insert_survey_map_item(survey_map, group_key, item)
        elif result.get("target") == "section_guide":
            section_id = str(result.get("section_id") or task.get("section_id") or "")
            if not section_id:
                continue
            existing = section_guides_by_id.get(section_id)
            if not existing:
                section_guides_by_id[section_id] = {
                    "section_id": section_id,
                    "title": result.get("title") or task.get("title") or "",
                    "section_role": result.get("section_role") or "general",
                    "read_priority": result.get("read_priority") or task.get("priority") or "medium",
                    "novice_summary": result.get("novice_summary") or "",
                    "cards": [],
                }
            section_guides_by_id[section_id]["cards"].extend(result.get("cards") or [])

    section_guides = _normalize_section_guides(list(section_guides_by_id.values()))
    reading_map = {
        "version": READING_MAP_VERSION,
        "status": status,
        "partial": partial,
        "paper_type": "survey",
        "map_variant": "survey",
        "prerequisite_card": prerequisite_card,
        "research_map": base.get("research_map", {}),
        "survey_map": survey_map,
        "research_problem": base.get("research_problem", {}),
        "core_method": base.get("core_method", {}),
        "method_steps": base.get("method_steps", []),
        "experimental_support": base.get("experimental_support", []),
        "limitations_and_questions": base.get("limitations_and_questions", []),
        "section_guides": section_guides,
        "survey_card_plan_summary": {
            "map_tasks_count": plan.get("map_tasks_count", 0),
            "section_guide_tasks_count": plan.get("section_guide_tasks_count", 0),
            "tasks_total": len(plan.get("tasks", [])),
        },
    }
    _clean_survey_map_items(reading_map["survey_map"], paper.get("sections", []) or [])
    return reading_map


def _empty_survey_map() -> dict[str, Any]:
    return {
        "field_overview": {},
        "development_timeline": [],
        "pain_points": [],
        "taxonomy": [],
        "technical_routes": [],
        "representative_methods": [],
        "datasets": [],
        "evaluation_protocols": [],
        "applications": [],
        "open_challenges": [],
        "reading_strategy": [],
    }


def _insert_survey_map_item(survey_map: dict[str, Any], group_key: str, item: Any) -> None:
    if group_key not in SURVEY_MAP_GROUP_KEYS or not isinstance(item, dict):
        return
    if _is_low_quality_survey_item(group_key, item):
        return
    if group_key == "field_overview":
        survey_map["field_overview"] = _dict_with_fallback(item, survey_map.get("field_overview", {}))
        return
    survey_map.setdefault(group_key, [])
    survey_map[group_key].append(item)


def _is_low_quality_survey_item(group_key: str, item: dict[str, Any]) -> bool:
    if item.get("insufficient_evidence") is True:
        return True
    meaningful = {
        key: value
        for key, value in item.items()
        if key not in {"source_sections", "url", "link"} and value not in ("", None, [], {})
    }
    if len(meaningful) < 3:
        return True
    evidence = str(item.get("evidence") or item.get("intro_evidence") or "").strip()
    if group_key != "field_overview" and not evidence and not item.get("source_sections"):
        return True
    text_values = [str(value).strip() for value in meaningful.values() if isinstance(value, str)]
    if len(text_values) <= 2 and all(len(value) < 36 for value in text_values):
        return True
    return False


def _save_survey_partial_reading_map(
    storage: Any | None,
    paper_id: str,
    generation_id: str,
    *,
    reading_map: dict[str, Any],
    phase: str,
    progress: int,
    error: str = "",
    artifacts: dict[str, Any] | None = None,
) -> bool:
    if storage is None or not paper_id:
        return True
    paper = _load_paper_data(storage, paper_id)
    if not paper:
        return False
    if generation_id and paper.get("reading_map_generation_id") != generation_id:
        return False
    paper["reading_map"] = reading_map
    paper["reading_map_status"] = reading_map.get("status", "llm_running")
    paper["reading_map_phase"] = phase
    paper["reading_map_progress"] = int(progress)
    paper["reading_map_error"] = error
    heartbeat_at = _utc_now_iso()
    paper["reading_map_heartbeat_at"] = heartbeat_at
    if phase in {"failed", "failed_partial", "llm_done"}:
        paper["reading_map_completed_at"] = heartbeat_at
    if artifacts:
        existing = paper.get("reading_map_artifacts") if isinstance(paper.get("reading_map_artifacts"), dict) else {}
        existing.update(artifacts)
        paper["reading_map_artifacts"] = existing
    storage.save_paper(paper_id, paper)
    return True


def _survey_card_progress(completed: int, total: int, failed: int, current_title: str) -> dict[str, Any]:
    return {
        "total": int(total),
        "completed": int(completed),
        "failed": int(failed),
        "current_title": current_title,
    }


def _survey_card_generation_progress(done: int, total: int) -> int:
    if total <= 0:
        return 10
    return min(94, max(10, int(10 + done / total * 84)))


def _survey_partial_has_core_content(reading_map: dict[str, Any]) -> bool:
    survey = reading_map.get("survey_map") if isinstance(reading_map.get("survey_map"), dict) else {}
    if not survey:
        return False
    has_overview = bool(survey.get("field_overview"))
    core_groups = sum(
        1
        for key in ("taxonomy", "technical_routes", "representative_methods", "datasets", "open_challenges")
        if survey.get(key)
    )
    return has_overview and core_groups >= 2 and bool(reading_map.get("section_guides"))


def _build_survey_fulltext_reading_map(
    *,
    paper: dict[str, Any],
    fallback: dict[str, Any],
    model: Any | None,
    skill_registry: Any | None,
    storage: Any | None,
    paper_id: str,
    generation_id: str,
) -> dict[str, Any]:
    if model is None:
        return _failed_reading_map("LLM 模型未初始化，无法生成综述全文导读地图。")
    chunks = _survey_text_chunks(paper, max_chars=SURVEY_CHUNK_MAX_CHARS)
    if not chunks:
        return _failed_reading_map("未找到可用于综述导读地图生成的章节正文。")
    survey_skill_instructions = _survey_skill_instructions(skill_registry)
    survey_skill_hash = hashlib.sha1(survey_skill_instructions.encode("utf-8")).hexdigest() if survey_skill_instructions else ""
    artifacts = paper.get("reading_map_artifacts") if isinstance(paper.get("reading_map_artifacts"), dict) else {}
    cached = artifacts.get("survey_fact_chunks") if isinstance(artifacts.get("survey_fact_chunks"), dict) else {}
    facts_by_chunk: dict[str, dict[str, Any]] = {}
    missing_chunks = []
    for chunk in chunks:
        cached_item = cached.get(chunk["chunk_id"]) if isinstance(cached, dict) else None
        if (
            isinstance(cached_item, dict)
            and cached_item.get("status") == "ok"
            and cached_item.get("text_hash") == chunk["text_hash"]
            and cached_item.get("skill_hash", "") == survey_skill_hash
            and isinstance(cached_item.get("facts"), dict)
        ):
            facts_by_chunk[chunk["chunk_id"]] = cached_item["facts"]
        else:
            missing_chunks.append(chunk)

    if not _save_reading_map_phase(
        storage,
        paper_id,
        generation_id,
        phase="extracting_sections",
        progress=_phase_progress(len(facts_by_chunk), len(chunks)),
    ):
        return _failed_reading_map("生成任务已被新一轮请求替换。")

    def extract_one(chunk: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        return chunk, _extract_survey_chunk_facts(model, paper, chunk, survey_skill_instructions)

    new_cache = dict(cached) if isinstance(cached, dict) else {}
    completed = len(facts_by_chunk)
    try:
        for batch in _batched(missing_chunks, SURVEY_CHUNK_BATCH_SIZE):
            if not _generation_is_current(storage, paper_id, generation_id):
                return _failed_reading_map("生成任务已被新一轮请求替换。")
            executor = ThreadPoolExecutor(max_workers=min(SURVEY_CHUNK_MAX_WORKERS, len(batch)))
            future_to_chunk = {executor.submit(extract_one, chunk): chunk for chunk in batch}
            should_wait = True
            try:
                for future in as_completed(future_to_chunk, timeout=SURVEY_CHUNK_BATCH_TIMEOUT_SECONDS):
                    chunk, facts = future.result()
                    facts_by_chunk[chunk["chunk_id"]] = facts
                    new_cache[chunk["chunk_id"]] = {
                        "status": "ok",
                        "text_hash": chunk["text_hash"],
                        "skill_hash": survey_skill_hash,
                        "section_id": chunk["section_id"],
                        "title": chunk["title"],
                        "facts": facts,
                    }
                    completed += 1
            except FutureTimeoutError as error:
                should_wait = False
                for future in future_to_chunk:
                    future.cancel()
                raise TimeoutError(
                    f"survey chunk batch timed out after {SURVEY_CHUNK_BATCH_TIMEOUT_SECONDS} seconds"
                ) from error
            except Exception:
                should_wait = False
                for future in future_to_chunk:
                    future.cancel()
                raise
            finally:
                executor.shutdown(wait=should_wait, cancel_futures=not should_wait)

            if not _save_reading_map_phase(
                storage,
                paper_id,
                generation_id,
                phase="extracting_sections",
                progress=_phase_progress(completed, len(chunks)),
                artifacts={"survey_fact_chunks": new_cache},
            ):
                return _failed_reading_map("生成任务已被新一轮请求替换。")
    except Exception as error:
        return _failed_reading_map(f"综述章节事实抽取失败：{error}")

    ordered_facts = [facts_by_chunk[chunk["chunk_id"]] for chunk in chunks if chunk["chunk_id"] in facts_by_chunk]
    if not ordered_facts:
        return _failed_reading_map("综述章节事实抽取结果为空。")

    if not _save_reading_map_phase(storage, paper_id, generation_id, phase="merging_facts", progress=72):
        return _failed_reading_map("生成任务已被新一轮请求替换。")
    try:
        merged_facts = _merge_survey_facts(model, paper, ordered_facts, survey_skill_instructions)
    except Exception as error:
        return _failed_reading_map(f"综述事实聚合失败：{error}")
    if not isinstance(merged_facts, dict) or not merged_facts:
        return _failed_reading_map("综述事实聚合结果为空。")
    if not _save_reading_map_phase(
        storage,
        paper_id,
        generation_id,
        phase="merging_facts",
        progress=86,
        artifacts={"survey_fact_chunks": new_cache, "survey_merged_facts": merged_facts, "survey_skill_hash": survey_skill_hash},
    ):
        return _failed_reading_map("生成任务已被新一轮请求替换。")

    if not _save_reading_map_phase(storage, paper_id, generation_id, phase="finalizing_map", progress=90):
        return _failed_reading_map("生成任务已被新一轮请求替换。")
    try:
        final_map = _build_survey_reading_map_from_merged_facts(paper, fallback, merged_facts)
    except Exception as error:
        return _failed_reading_map(f"综述导读地图组装失败：{error}")
    final_map["status"] = "llm_done"
    final_map["paper_type"] = "survey"
    final_map["map_variant"] = "survey"
    if not _validate_survey_reading_map(final_map):
        logger.warning(
            "Survey reading map validation warning for paper %s: generated map is sparse but will be returned",
            paper_id,
        )
    final_map["generation_artifacts_summary"] = {
        "chunks_total": len(chunks),
        "chunks_reused": len(chunks) - len(missing_chunks),
        "chunks_extracted": len(missing_chunks),
        "survey_skill_hash": survey_skill_hash,
    }
    _save_reading_map_phase(
        storage,
        paper_id,
        generation_id,
        phase="llm_done",
        progress=100,
        artifacts={"survey_fact_chunks": new_cache, "survey_merged_facts": merged_facts, "survey_skill_hash": survey_skill_hash},
    )
    return final_map


def _survey_text_chunks(paper: dict[str, Any], max_chars: int = 7000) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for section in paper.get("sections", []) or []:
        title = str(section.get("title") or "")
        if _section_role(section, "survey") == "references":
            continue
        text = " ".join(str(section.get("content") or "").split())
        if not text:
            continue
        start = 0
        chunk_index = 1
        while start < len(text):
            end = min(len(text), start + max_chars)
            if end < len(text):
                boundary = text.rfind(". ", start + int(max_chars * 0.72), end)
                if boundary > start:
                    end = boundary + 1
            chunk_text = text[start:end].strip()
            if chunk_text:
                section_id = str(section.get("section_id") or f"section-{len(chunks) + 1}")
                chunk_id = f"{section_id}::survey_chunk:{chunk_index}"
                chunks.append({
                    "chunk_id": chunk_id,
                    "section_id": section_id,
                    "title": title,
                    "level": section.get("level", 1),
                    "start_page": section.get("start_page"),
                    "end_page": section.get("end_page"),
                    "chunk_index": chunk_index,
                    "text": chunk_text,
                    "text_hash": hashlib.sha1(chunk_text.encode("utf-8")).hexdigest(),
                    "section_role_hint": _section_role(section, "survey"),
                })
            start = max(end, start + 1)
            chunk_index += 1
    return chunks


def _phase_progress(done: int, total: int) -> int:
    if total <= 0:
        return 0
    return min(70, max(5, int(done / total * 70)))


def _batched(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if size <= 0:
        return [items]
    return [items[index:index + size] for index in range(0, len(items), size)]


def _generation_is_current(storage: Any | None, paper_id: str, generation_id: str) -> bool:
    if storage is None or not paper_id or not generation_id:
        return True
    try:
        paper = storage.load_paper(paper_id)
    except Exception as error:
        logger.warning("Failed to check reading map generation id for %s: %s", paper_id, error)
        return False
    return bool(paper and paper.get("reading_map_generation_id") == generation_id)


def _save_reading_map_phase(
    storage: Any | None,
    paper_id: str,
    generation_id: str,
    *,
    phase: str,
    progress: int,
    error: str = "",
    artifacts: dict[str, Any] | None = None,
) -> bool:
    if storage is None or not paper_id:
        return True
    paper = _load_paper_data(storage, paper_id)
    if not paper:
        return False
    if generation_id and paper.get("reading_map_generation_id") != generation_id:
        return False
    paper["reading_map_status"] = phase if phase in {"failed", "failed_partial", "llm_done"} else "llm_running"
    paper["reading_map_phase"] = phase
    paper["reading_map_progress"] = int(progress)
    paper["reading_map_error"] = error
    heartbeat_at = _utc_now_iso()
    paper["reading_map_heartbeat_at"] = heartbeat_at
    if phase in {"failed", "failed_partial", "llm_done"}:
        paper["reading_map_completed_at"] = heartbeat_at
    if artifacts:
        existing = paper.get("reading_map_artifacts") if isinstance(paper.get("reading_map_artifacts"), dict) else {}
        existing.update(artifacts)
        paper["reading_map_artifacts"] = existing
    storage.save_paper(paper_id, paper)
    return True


def _persist_failed_reading_map(
    storage: Any | None,
    paper_id: str,
    message: str,
    *,
    generation_id: str = "",
) -> None:
    if storage is None or not paper_id:
        return
    paper = _load_paper_data(storage, paper_id)
    if not paper:
        return
    if generation_id and paper.get("reading_map_generation_id") != generation_id:
        return
    failed = _failed_reading_map(message)
    paper["reading_map"] = failed
    paper["reading_map_status"] = "failed"
    paper["reading_map_phase"] = "failed"
    paper["reading_map_progress"] = int(paper.get("reading_map_progress") or 0)
    paper["reading_map_error"] = message
    completed_at = _utc_now_iso()
    paper["reading_map_heartbeat_at"] = completed_at
    paper["reading_map_completed_at"] = completed_at
    storage.save_paper(paper_id, paper)


def _extract_survey_chunk_facts(
    model: Any,
    paper: dict[str, Any],
    chunk: dict[str, Any],
    skill_instructions: str = "",
) -> dict[str, Any]:
    skill_block = f"Skill instructions for survey extraction:\n{skill_instructions}\n\n" if skill_instructions else ""
    prompt = (
        f"{skill_block}"
        "You are extracting factual evidence from one chunk of a survey paper for novice-oriented field onboarding. "
        "Return JSON only. Do not invent paper titles, dataset names, years, URLs, or claims not supported by this chunk. "
        "Classify the section by content, not only by title. Extract explanatory field facts, not sentence fragments.\n"
        "Schema:\n"
        "{\n"
        '  "section_role": "field_overview|timeline|taxonomy|technical_route|representative_methods|datasets|evaluation|applications|challenges|general",\n'
        '  "field_overview": [{"field": "", "core_task": "", "why_now": "", "novice_takeaway": "", "evidence": "", "source_sections": []}],\n'
        '  "development_timeline": [{"stage": "", "time_range": "", "key_change": "", "representative_works": [], "why_it_matters": "", "evidence": "", "source_sections": []}],\n'
        '  "pain_points": [{"problem": "", "why_hard": "", "impact": "", "existing_attempts": [], "unresolved_part": "", "evidence": "", "source_sections": []}],\n'
        '  "taxonomy": [{"category": "", "basis": "", "typical_methods": [], "solved_problems": "", "limitations": "", "evidence": "", "source_sections": []}],\n'
        '  "technical_routes": [{"name": "", "core_mechanism": "", "typical_pipeline": [], "strengths": [], "weaknesses": [], "representative_method_ids": [], "evidence": "", "source_sections": []}],\n'
        '  "representative_methods": [{"paper_title": "", "year": "", "method_name": "", "route": "", "method_summary": "", "specific_solution": "", "improves_on": "", "remaining_limits": "", "url": "", "evidence": "", "source_sections": []}],\n'
        '  "datasets": [{"name": "", "task": "", "content": "", "structure": "", "scale": "", "metrics": [], "url": "", "evidence": "", "source_sections": []}],\n'
        '  "evaluation_protocols": [{"protocol": "", "task": "", "metrics": [], "setting": "", "what_it_tests": "", "evidence": "", "source_sections": []}],\n'
        '  "applications": [{"application": "", "scenario": "", "why_suitable": "", "typical_methods": [], "constraints": "", "evidence": "", "source_sections": []}],\n'
        '  "open_challenges": [{"challenge": "", "why_it_matters": "", "current_bottleneck": "", "future_direction": "", "evidence": "", "source_sections": []}],\n'
        '  "section_guide_candidates": [{"section_id": "", "title": "", "section_role": "", "read_priority": "high|medium|low", "novice_summary": "", "cards": [{"card_type": "reading_route|field_timeline|taxonomy_node|route_comparison|paper_method_table|dataset_catalog|benchmark_protocol|challenge_card|application_landscape|future_direction", "title": "", "content": {"core_message": "", "why_it_matters": "", "key_points": [], "connections": [], "next_reading": ""}, "source_sections": []}]}]\n'
        "}\n"
        "Every non-empty item must include source_sections with section_id,title,page and an evidence string copied or tightly paraphrased from the chunk. "
        "Limit each list field to at most 5 high-value items, section_guide_candidates to at most 4 items, and each evidence string to at most 160 Chinese characters. "
        "Do not output generic titles such as Item 1 or Point 1. Do not output fragment-only values such as Front. or Comput. "
        "Do not treat a section title as a representative method. Omit weak facts instead of padding lists. "
        "Write Chinese content.\n\n"
        f"Paper title: {paper.get('title', '')}\n"
        f"Abstract: {str(paper.get('abstract') or '')[:1200]}\n"
        f"Chunk metadata: {json.dumps({k: chunk.get(k) for k in ('chunk_id','section_id','title','start_page','end_page','section_role_hint')}, ensure_ascii=False)}\n"
        f"Chunk text:\n{chunk.get('text', '')}"
    )
    response = _reading_map_json_chat(
        model,
        [
            {"role": "system", "content": "Return only valid JSON for survey fact extraction."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=SURVEY_FACT_MAX_TOKENS,
        timeout=SURVEY_FACT_REQUEST_TIMEOUT_SECONDS,
    )
    parsed = _reading_map_response_json(
        response,
        label=f"综述事实 {chunk.get('chunk_id')}",
        max_tokens=SURVEY_FACT_MAX_TOKENS,
    )
    source = _chunk_source_ref(chunk)
    parsed["chunk_id"] = chunk.get("chunk_id", "")
    parsed["section_id"] = chunk.get("section_id", "")
    parsed["title"] = chunk.get("title", "")
    parsed["source_sections"] = [source]
    _ensure_fact_sources(parsed, source)
    return parsed


def _merge_survey_facts(
    model: Any,
    paper: dict[str, Any],
    facts: list[dict[str, Any]],
    skill_instructions: str = "",
) -> dict[str, Any]:
    compact = _compact_survey_facts_for_prompt(facts, limit=SURVEY_MERGE_PROMPT_LIMIT)
    skill_block = f"Skill instructions for survey merging and display:\n{skill_instructions}\n\n" if skill_instructions else ""
    prompt = (
        f"{skill_block}"
        "Merge extracted facts from a full survey paper. Return JSON only. "
        "Deduplicate repeated routes, datasets, papers, and challenges while preserving source_sections and evidence. "
        "Do not add facts that are not present in the extracted facts. Keep explanations concrete and novice-oriented.\n"
        "Schema keys: field_overview, development_timeline, pain_points, taxonomy, technical_routes, representative_methods, "
        "datasets, evaluation_protocols, applications, open_challenges, reading_strategy, section_guides_seed.\n"
        "Required item shapes:\n"
        "- field_overview: one object with field, core_task, why_now, novice_takeaway, source_sections, evidence.\n"
        "- development_timeline: stage, time_range, key_change, representative_works, why_it_matters, source_sections, evidence.\n"
        "- taxonomy: category, basis, typical_methods, solved_problems, limitations, source_sections, evidence.\n"
        "- technical_routes: name, core_mechanism, typical_pipeline, strengths, weaknesses, representative_method_ids, source_sections, evidence.\n"
        "- representative_methods: paper_title, year, method_name, route, method_summary, specific_solution, improves_on, remaining_limits, url, source_sections, evidence.\n"
        "- datasets: name, task, content, structure, scale, metrics, url, source_sections, evidence.\n"
        "- section_guides_seed: one item per important section with 2-4 cards. Each card content should include core_message, why_it_matters, key_points, connections, next_reading.\n"
        "For representative_methods, keep concrete paper title/year/method when available; otherwise omit that item. "
        "For datasets, keep concrete dataset or benchmark names when available; otherwise omit that item. "
        "Do not output Item 1, Point 1, Front., Comput., or isolated sentence fragments. Do not replace specific facts with generic summaries. "
        "Keep no more than 12 items per list except representative_methods up to 24 and section_guides_seed up to 80 compact items. "
        "Keep evidence concise, at most 160 Chinese characters. Write Chinese.\n\n"
        f"Paper: {paper.get('title', '')}\n"
        f"Extracted facts:\n{compact}"
    )
    response = _reading_map_json_chat(
        model,
        [
            {"role": "system", "content": "Return only valid JSON for survey fact merging."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=SURVEY_MERGE_MAX_TOKENS,
        timeout=SURVEY_MERGE_REQUEST_TIMEOUT_SECONDS,
    )
    return _reading_map_response_json(
        response,
        label="综述事实合并",
        max_tokens=SURVEY_MERGE_MAX_TOKENS,
    )


def _build_survey_reading_map_from_merged_facts(
    paper: dict[str, Any],
    fallback: dict[str, Any],
    merged_facts: dict[str, Any],
) -> dict[str, Any]:
    base = _llm_visible_base(fallback)
    survey_map = _normalize_survey_map(merged_facts, {})
    survey_map = _repair_sparse_survey_map(survey_map, paper, base)
    section_guides = _build_survey_section_guides_from_facts(paper, merged_facts, base)
    parsed = {
        "version": READING_MAP_VERSION,
        "status": "llm_done",
        "paper_type": "survey",
        "map_variant": "survey",
        "prerequisite_card": base.get("prerequisite_card", {}),
        "research_map": base.get("research_map", {}),
        "survey_map": survey_map,
        "research_problem": base.get("research_problem", {}),
        "core_method": base.get("core_method", {}),
        "method_steps": base.get("method_steps", []),
        "experimental_support": base.get("experimental_support", []),
        "limitations_and_questions": base.get("limitations_and_questions", []),
        "section_guides": section_guides,
    }
    normalized = _normalize_reading_map(parsed, base)
    normalized["survey_map"] = survey_map
    normalized["section_guides"] = section_guides
    return normalized


def _repair_sparse_survey_map(
    survey_map: dict[str, Any],
    paper: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    repaired = dict(survey_map)
    sections = [
        section for section in (paper.get("sections", []) or [])
        if _section_role(section, "survey") != "references"
    ]
    if not repaired.get("field_overview"):
        overview_source = next((section for section in sections if _section_role(section, "survey") in {"abstract", "introduction"}), sections[0] if sections else {})
        repaired["field_overview"] = {
            "field": paper.get("title", ""),
            "core_task": _first_sentence(str(overview_source.get("content") or "")) or str(paper.get("abstract") or "")[:240],
            "why_now": _second_sentence(str(overview_source.get("content") or "")),
            "novice_takeaway": "先用分类体系建立领域坐标，再沿技术路线、数据集和开放问题继续精读。",
            "source_sections": [_source_ref(overview_source)] if overview_source else [],
            "fallback_generated": True,
        }

    if not repaired.get("taxonomy") and repaired.get("technical_routes"):
        repaired["taxonomy"] = [
            {
                "category": item.get("name") or item.get("route") or item.get("title") or f"技术类别 {index}",
                "summary": item.get("core_idea") or item.get("summary") or item.get("evidence", ""),
                "source_sections": item.get("source_sections", []),
            }
            for index, item in enumerate(repaired.get("technical_routes", [])[:8], start=1)
            if isinstance(item, dict)
        ]
    if not repaired.get("technical_routes") and repaired.get("taxonomy"):
        repaired["technical_routes"] = [
            {
                "name": item.get("category") or item.get("name") or item.get("title") or f"技术路线 {index}",
                "core_idea": item.get("summary") or item.get("evidence", ""),
                "source_sections": item.get("source_sections", []),
            }
            for index, item in enumerate(repaired.get("taxonomy", [])[:8], start=1)
            if isinstance(item, dict)
        ]
    if not repaired.get("open_challenges") and repaired.get("pain_points"):
        repaired["open_challenges"] = [
            {
                "challenge": item.get("problem") or item.get("summary") or item.get("title") or f"开放问题 {index}",
                "why_it_matters": item.get("summary") or item.get("evidence", ""),
                "source_sections": item.get("source_sections", []),
            }
            for index, item in enumerate(repaired.get("pain_points", [])[:8], start=1)
            if isinstance(item, dict)
        ]
    if not repaired.get("reading_strategy"):
        repaired["reading_strategy"] = [{
            "title": "综述阅读路线",
            "summary": "先读领域概览和分类体系，再按技术路线追踪代表方法，最后查看数据集、评测协议和开放问题。",
            "fallback_generated": True,
        }]
    _clean_survey_map_items(repaired, sections)
    return repaired


def _build_survey_section_guides_from_facts(
    paper: dict[str, Any],
    merged_facts: dict[str, Any],
    fallback: dict[str, Any],
) -> list[dict[str, Any]]:
    seeds = merged_facts.get("section_guides_seed")
    seeds_by_section: dict[str, dict[str, Any]] = {}
    if isinstance(seeds, list):
        for seed in seeds:
            if isinstance(seed, dict) and seed.get("section_id"):
                seeds_by_section[str(seed.get("section_id"))] = seed

    fallback_guides = {
        str(guide.get("section_id")): guide
        for guide in (fallback.get("section_guides") or [])
        if isinstance(guide, dict) and guide.get("section_id")
    }
    guides = []
    for section in (paper.get("sections", []) or [])[:120]:
        if _section_role(section, "survey") == "references":
            continue
        section_id = str(section.get("section_id") or "")
        text = " ".join(str(section.get("content") or "").split())
        seed = seeds_by_section.get(section_id) or {}
        seed_has_cards = isinstance(seed.get("cards"), list) and bool(seed.get("cards"))
        fallback_guide = fallback_guides.get(section_id) or {}
        guide = _dict_with_fallback(seed, fallback_guide)
        if not seed_has_cards:
            guide.pop("cards", None)
        guide["section_id"] = section_id
        guide["title"] = guide.get("title") or section.get("title", "")
        guide["section_role"] = guide.get("section_role") or _section_role(section, "survey")
        guide["read_priority"] = guide.get("read_priority") or (
            "high" if guide["section_role"] in {"introduction", "taxonomy", "technical_route", "dataset", "challenge"} else "medium"
        )
        guide["novice_summary"] = guide.get("novice_summary") or guide.get("summary") or _first_sentence(text)
        if not isinstance(guide.get("cards"), list) or not guide.get("cards"):
            guide["cards"] = _guide_cards_for_section(guide, section, text, "survey")
            guide["fallback_generated"] = True
        guides.append(guide)
    return _normalize_section_guides(guides)


def _compact_survey_facts_for_prompt(facts: list[dict[str, Any]], limit: int = 90000) -> str:
    compact_items = []
    for item in facts:
        compact_items.append({
            key: item.get(key)
            for key in (
                "chunk_id",
                "section_id",
                "title",
                "section_role",
                "field_overview",
                "development_timeline",
                "pain_points",
                "taxonomy",
                "technical_routes",
                "representative_methods",
                "datasets",
                "evaluation_protocols",
                "applications",
                "open_challenges",
                "section_guide_candidates",
                "source_sections",
            )
            if item.get(key)
        })
    text = json.dumps(compact_items, ensure_ascii=False)
    return text[:limit]


def _chunk_source_ref(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_id": chunk.get("section_id", ""),
        "title": chunk.get("title", ""),
        "page": chunk.get("start_page"),
    }


def _ensure_fact_sources(value: Any, source: dict[str, Any]) -> None:
    if isinstance(value, dict):
        is_source_ref = (
            set(value.keys()).issubset({"section_id", "title", "page", "start_page", "end_page"})
            and bool(value.get("section_id") or value.get("page"))
        )
        if (
            not is_source_ref
            and any(key in value for key in ("summary", "name", "title", "category", "route", "problem", "challenge", "paper_title", "method_name"))
        ):
            if not value.get("source_sections"):
                value["source_sections"] = [dict(source)]
        for key, item in value.items():
            if key == "source_sections":
                continue
            _ensure_fact_sources(item, source)
    elif isinstance(value, list):
        for item in value:
            _ensure_fact_sources(item, source)


def _validate_survey_reading_map(reading_map: dict[str, Any]) -> bool:
    if reading_map.get("map_variant") != "survey":
        return False
    survey = reading_map.get("survey_map") if isinstance(reading_map.get("survey_map"), dict) else {}
    content_keys = (
        "field_overview",
        "development_timeline",
        "pain_points",
        "taxonomy",
        "technical_routes",
        "representative_methods",
        "datasets",
        "evaluation_protocols",
        "applications",
        "open_challenges",
    )
    visible_content_count = sum(1 for key in content_keys if survey.get(key))
    if visible_content_count < 3:
        return False
    if not reading_map.get("section_guides"):
        return False
    method_titles = [
        str(item.get("paper_title") or item.get("method_name") or "")
        for item in survey.get("representative_methods", [])
        if isinstance(item, dict)
    ]
    if method_titles and all(_looks_like_section_title(title) for title in method_titles):
        return False
    dataset_names = [
        str(item.get("name") or "")
        for item in survey.get("datasets", [])
        if isinstance(item, dict)
    ]
    if dataset_names and all(name.lower() in {"dataset", "benchmark", "datasets", "benchmarks", "数据集", "基准"} for name in dataset_names):
        return False
    return _survey_items_have_sources(survey)


def _looks_like_section_title(value: str) -> bool:
    return bool(re.match(r"^\s*(\d+(\.\d+)*|[ivx]+)\s+|^(abstract|introduction|conclusion|references)\b", value.lower()))


def _looks_like_fragment(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if len(text) <= 8 and re.fullmatch(r"[A-Za-z]+\.", text):
        return True
    if text.lower() in {"item 1", "item 2", "point 1", "point 2", "front.", "comput.", "method", "model"}:
        return True
    return False


def _clean_survey_map_items(survey_map: dict[str, Any], sections: list[dict[str, Any]]) -> None:
    section_titles = {
        str(section.get("title") or "").strip().lower()
        for section in sections
        if str(section.get("title") or "").strip()
    }

    def is_section_title(text: Any) -> bool:
        normalized = str(text or "").strip().lower()
        return bool(normalized and (normalized in section_titles or _looks_like_section_title(normalized)))

    cleaned_methods = []
    for item in survey_map.get("representative_methods", []) or []:
        if not isinstance(item, dict):
            continue
        paper_title = item.get("paper_title")
        method_name = item.get("method_name") or item.get("name") or item.get("title")
        has_concrete_title = bool(paper_title) and not _looks_like_fragment(paper_title) and not is_section_title(paper_title)
        has_concrete_method = bool(method_name) and not _looks_like_fragment(method_name) and not is_section_title(method_name)
        has_detail = any(item.get(key) for key in (
            "method_summary",
            "core_mechanism",
            "specific_solution",
            "problem_addressed",
            "improves_on",
            "limitations",
            "evidence",
            "year",
            "url",
        ))
        if (has_concrete_title or has_concrete_method) and has_detail:
            cleaned_methods.append(item)
    survey_map["representative_methods"] = cleaned_methods

    generic_dataset_names = {"dataset", "datasets", "benchmark", "benchmarks", "corpus", "data", "数据集", "基准", "公开数据集"}
    cleaned_datasets = []
    for item in survey_map.get("datasets", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("dataset") or item.get("title") or "").strip()
        if _looks_like_fragment(name) or name.lower() in generic_dataset_names or is_section_title(name):
            continue
        if any(item.get(key) for key in ("task", "content", "structure", "scale", "metrics", "used_by_methods", "evidence", "url")):
            cleaned_datasets.append(item)
    survey_map["datasets"] = cleaned_datasets


def _survey_items_have_sources(survey: dict[str, Any]) -> bool:
    checked = 0
    with_source = 0
    for key in ("taxonomy", "technical_routes", "representative_methods", "datasets", "open_challenges"):
        for item in survey.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            checked += 1
            if item.get("source_sections"):
                with_source += 1
    if checked == 0:
        return True
    return with_source / checked >= 0.35


def _reading_map_skill_instructions(skill_registry: Any | None) -> str:
    if skill_registry is None:
        return ""
    try:
        return str(skill_registry.get_instructions(READING_MAP_SKILL_ID) or "")
    except Exception as error:
        logger.debug("Unable to load %s instructions: %s", READING_MAP_SKILL_ID, error)
        return ""


def _survey_skill_instructions(skill_registry: Any | None) -> str:
    instructions = _reading_map_skill_instructions(skill_registry)
    if not instructions:
        return ""
    match = re.search(
        r"<!--\s*survey_map_skill:start\s*-->(.*?)<!--\s*survey_map_skill:end\s*-->",
        instructions,
        re.S,
    )
    selected = match.group(1).strip() if match else instructions
    return selected[:8000]


def _build_reading_map_prompt(
    paper: dict[str, Any],
    fallback: dict[str, Any],
    skill_registry: Any | None,
) -> str:
    skill_instructions = _reading_map_skill_instructions(skill_registry)

    sections_payload = []
    source_sections = [
        section
        for section in (paper.get("sections", []) or [])
        if _section_role(section, fallback.get("paper_type", "unknown")) != "references"
    ]
    for section in source_sections[:28]:
        text = " ".join(str(section.get("content") or "").split())
        sections_payload.append({
            "section_id": section.get("section_id", ""),
            "title": section.get("title", ""),
            "level": section.get("level", 1),
            "start_page": section.get("start_page"),
            "end_page": section.get("end_page"),
            "section_role_hint": _section_role(section, fallback.get("paper_type", "unknown")),
            "text": text[:560],
        })

    prompt_payload = {
        "paper": {
            "title": paper.get("title", ""),
            "abstract": str(paper.get("abstract") or "")[:1800],
            "paper_type_hint": fallback.get("paper_type", "unknown"),
        },
        "sections": sections_payload,
        "heuristic_seed": {
            "paper_type": fallback.get("paper_type", "unknown"),
            "map_variant": fallback.get("map_variant", "research"),
            "research_problem": fallback.get("research_problem", {}),
            "core_method": fallback.get("core_method", {}),
            "input_section_count": len(paper.get("sections", []) or []),
        },
    }
    return (
        f"{skill_instructions}\n\n"
        "Build a deep novice-oriented reading_map overview for this non-survey paper. "
        "Decide paper_type as research, theory, or system; keep map_variant as research.\n"
        "The output must be JSON only. Use this schema exactly, keeping legacy research fields for compatibility:\n"
        "{\n"
        '  "paper_type": "research|theory|system",\n'
        '  "map_variant": "research",\n'
        '  "prerequisite_card": {"concepts": [{"name": "", "why_needed": "", "learn_first": [], "difficulty": "easy|medium|hard"}], "baseline_papers": [{"title": "", "url": "", "relationship": "direct_baseline|strongest_compared_baseline|foundational_work|survey_anchor|dataset_or_benchmark_paper", "why_read": ""}], "reading_order": []},\n'
        '  "research_map": {},\n'
        '  "survey_map": {},\n'
        '  "research_problem": {"title": "", "one_sentence": "", "why_it_matters": "", "novice_takeaway": "", "source_sections": []},\n'
        '  "core_method": {"name": "", "one_sentence": "", "main_idea": "", "technical_route": "", "source_sections": []},\n'
        '  "method_steps": [{"name": "", "goal": "", "input": "", "operation": "", "output": "", "why_needed": "", "source_sections": []}],\n'
        '  "experimental_support": [{"claim": "", "evidence": "", "datasets": [], "dataset_format": "", "experiment_setting": "", "baselines": [], "metrics": [], "protocol": "", "figures_or_tables": [], "source_sections": []}],\n'
        '  "limitations_and_questions": [{"limitation": "", "why_it_matters": "", "novice_question": "", "source_sections": []}],\n'
        '  "section_guides": []\n'
        "}\n"
        "Rules: keep each field compact but substantive; do not copy long paragraphs; do not duplicate the top-level research fields inside research_map; "
        "for research papers, prioritize problem, insight, method structure, experiments, datasets, baselines, metrics, limitations; "
        "output 3-6 prerequisite concepts, at most 5 baseline papers, at most 6 reading-order items, 3-6 method_steps, 3-6 experimental_support items, and 3-5 limitations_and_questions; "
        "keep each explanatory string within 180 Chinese characters, each nested name/metric/dataset/baseline list within 8 items, and each source_sections list within 3 items; "
        "leave section_guides empty because section guides are generated by separate bounded requests; "
        "source_sections entries should be objects with section_id, title, page when possible; "
        "paper links may be empty if unavailable; do not invent URLs; "
        "write Chinese content for readers.\n\n"
        f"INPUT:\n{json.dumps(prompt_payload, ensure_ascii=False)}"
    )


def _normalize_reading_map(parsed: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    paper_type = str(parsed.get("paper_type") or fallback.get("paper_type") or "research")
    if paper_type not in {"research", "survey", "theory", "system"}:
        paper_type = "research"
    map_variant = str(parsed.get("map_variant") or fallback.get("map_variant") or ("survey" if paper_type == "survey" else "research"))
    if map_variant not in {"research", "survey"}:
        map_variant = "survey" if paper_type == "survey" else "research"
    research_map_source = parsed.get("research_map") if isinstance(parsed.get("research_map"), dict) else fallback.get("research_map", {})
    if not isinstance(research_map_source, dict):
        research_map_source = {}
    survey_map = parsed.get("survey_map") if isinstance(parsed.get("survey_map"), dict) else fallback.get("survey_map", {})
    research_problem = parsed.get("research_problem") or research_map_source.get("research_problem") or fallback.get("research_problem", {})
    core_method = parsed.get("core_method") or research_map_source.get("core_method") or fallback.get("core_method", {})
    method_steps = _list_or_fallback(parsed.get("method_steps") or research_map_source.get("method_steps"), fallback.get("method_steps", []), 6)
    experimental_support = _list_or_fallback(parsed.get("experimental_support") or research_map_source.get("experimental_support"), fallback.get("experimental_support", []), 6)
    limitations_and_questions = _list_or_fallback(parsed.get("limitations_and_questions") or research_map_source.get("limitations_and_questions"), fallback.get("limitations_and_questions", []), 5)
    research_map = dict(research_map_source) if isinstance(research_map_source, dict) else {}
    research_map.update({
        "research_problem": research_problem,
        "core_method": core_method,
        "method_steps": method_steps,
        "experimental_support": experimental_support,
        "limitations_and_questions": limitations_and_questions,
    })
    normalized = {
        "version": READING_MAP_VERSION,
        "status": "llm_done",
        "paper_type": paper_type,
        "map_variant": map_variant,
        "prerequisite_card": parsed.get("prerequisite_card") or fallback.get("prerequisite_card", {}),
        "research_map": research_map,
        "survey_map": _normalize_survey_map(survey_map, fallback.get("survey_map", {})),
        "research_problem": research_problem,
        "core_method": core_method,
        "method_steps": method_steps,
        "experimental_support": experimental_support,
        "limitations_and_questions": limitations_and_questions,
        "section_guides": _normalize_section_guides(
            _list_or_fallback(parsed.get("section_guides"), fallback.get("section_guides", []), 120)
        ),
    }
    return normalized


def _list_or_fallback(value: Any, fallback: Any, limit: int) -> list[Any]:
    items = value if isinstance(value, list) and value else fallback
    return list(items or [])[:limit]


def _dict_with_fallback(value: Any, fallback: Any) -> dict[str, Any]:
    merged = dict(fallback) if isinstance(fallback, dict) else {}
    if not isinstance(value, dict):
        return merged
    for key, item in value.items():
        if item in (None, "", [], {}):
            continue
        merged[key] = item
    return merged


def _normalize_survey_map(value: Any, fallback: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    fallback_source = fallback if isinstance(fallback, dict) else {}
    normalized = {
        "field_overview": _dict_with_fallback(source.get("field_overview"), fallback_source.get("field_overview", {})),
        "development_timeline": _list_or_fallback(source.get("development_timeline"), fallback_source.get("development_timeline", []), 20),
        "pain_points": _list_or_fallback(source.get("pain_points"), fallback_source.get("pain_points", []), 20),
        "taxonomy": _list_or_fallback(source.get("taxonomy"), fallback_source.get("taxonomy", []), 24),
        "technical_routes": _list_or_fallback(source.get("technical_routes"), fallback_source.get("technical_routes", []), 24),
        "representative_methods": _list_or_fallback(source.get("representative_methods"), fallback_source.get("representative_methods", []), 40),
        "datasets": _list_or_fallback(source.get("datasets"), fallback_source.get("datasets", []), 30),
        "evaluation_protocols": _list_or_fallback(source.get("evaluation_protocols"), fallback_source.get("evaluation_protocols", []), 20),
        "applications": _list_or_fallback(source.get("applications"), fallback_source.get("applications", []), 20),
        "open_challenges": _list_or_fallback(source.get("open_challenges"), fallback_source.get("open_challenges", []), 24),
        "reading_strategy": _list_or_fallback(source.get("reading_strategy"), fallback_source.get("reading_strategy", []), 12),
    }
    return normalized


def _normalize_section_guides(guides: list[Any]) -> list[dict[str, Any]]:
    normalized = []
    for guide in guides:
        if not isinstance(guide, dict):
            continue
        cards = guide.get("cards")
        if not isinstance(cards, list) or not cards:
            cards = _legacy_guide_cards(guide)
        guide = dict(guide)
        guide["cards"] = [card for card in cards if isinstance(card, dict)][:6]
        normalized.append(guide)
    return normalized


def _build_heuristic_section_guides(sections: list[dict[str, Any]], paper_type: str = "research") -> list[dict[str, Any]]:
    guides = []
    for section in sections[:120]:
        title = str(section.get("title") or "")
        content = str(section.get("content") or "")
        text = " ".join(content.split())
        role = _section_role(section, paper_type)
        is_experiment = role in {"experiment", "dataset", "evaluation"}
        is_method = role in {"method", "technical_route", "taxonomy"}
        guide = {
            "section_id": section.get("section_id", ""),
            "title": title,
            "section_role": role,
            "read_priority": "high" if role in {"abstract", "introduction", "method", "technical_route", "taxonomy", "dataset", "challenge"} else "medium",
            "novice_summary": _first_sentence(text) or "这一节是论文主线中的一个阅读锚点。",
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
        guide["cards"] = _guide_cards_for_section(guide, section, text, paper_type)
        guides.append(guide)
    return guides


def _infer_paper_type(paper: dict[str, Any], sections: list[dict[str, Any]]) -> str:
    title = str(paper.get("title") or "").lower()
    abstract = str(paper.get("abstract") or "").lower()
    section_titles = " ".join(str(section.get("title") or "") for section in sections).lower()
    combined = f"{title}\n{abstract[:2400]}\n{section_titles}"
    survey_tokens = (
        "survey",
        "review",
        "overview",
        "tutorial",
        "taxonomy",
        "landscape",
        "roadmap",
        "综述",
        "调研",
        "回顾",
        "分类",
        "全景",
    )
    survey_section_tokens = (
        "taxonomy",
        "challenge",
        "future direction",
        "open problem",
        "dataset",
        "benchmark",
        "application",
        "evaluation protocol",
        "分类",
        "挑战",
        "未来方向",
        "开放问题",
        "数据集",
        "基准",
        "应用",
    )
    if any(token in title for token in survey_tokens):
        return "survey"
    score = sum(1 for token in survey_tokens if token in combined)
    score += sum(1 for token in survey_section_tokens if token in section_titles)
    has_method_experiment = bool(re.search(r"\b(method|approach|experiment|evaluation|results?)\b|方法|实验|结果", section_titles))
    if score >= 3 and (len(sections) >= 8 or not has_method_experiment):
        return "survey"
    if any(token in title for token in ("system", "benchmark", "dataset", "平台", "系统", "基准", "数据集")):
        return "system"
    if any(token in title for token in ("theory", "theoretical", "analysis", "定理", "理论", "分析")) and not has_method_experiment:
        return "theory"
    return "research"


def _section_role(section: dict[str, Any], paper_type: str = "research") -> str:
    title = str(section.get("title") or "").lower()
    if "abstract" in title or "摘要" in title:
        return "abstract"
    if "reference" in title or "bibliography" in title or "参考文献" in title:
        return "references"
    if any(token in title for token in ("intro", "background", "引言", "背景")):
        return "introduction"
    if any(token in title for token in ("related work", "prior work", "literature", "相关工作")):
        return "related_work"
    if paper_type == "survey":
        if any(token in title for token in ("preliminar", "overview", "contents", "scope")):
            return "introduction"
        if any(token in title for token in ("form", "function", "carries", "why agents need", "type", "types", "component")):
            return "taxonomy"
        if any(token in title for token in ("dynamic", "operate", "evolve", "formation", "evolution", "retrieval", "updating", "forgetting", "consolidation", "distillation")):
            return "technical_route"
        if any(token in title for token in ("resource", "framework", "benchmark", "dataset", "corpus", "open-source", "open source")):
            return "dataset"
        if any(token in title for token in ("position", "frontier", "challenge", "limitation", "open problem", "future", "discussion")):
            return "challenge"
    if any(token in title for token in ("taxonomy", "categor", "classification", "分类", "体系")):
        return "taxonomy"
    if any(token in title for token in ("dataset", "benchmark", "corpus", "数据集", "基准")):
        return "dataset"
    if any(token in title for token in ("evaluation", "metric", "protocol", "评测", "评价", "指标")):
        return "evaluation"
    if any(token in title for token in ("application", "case stud", "应用", "案例")):
        return "application"
    if any(token in title for token in ("challenge", "limitation", "open problem", "future", "discussion", "挑战", "局限", "开放问题", "未来", "讨论")):
        return "challenge" if paper_type == "survey" else "conclusion"
    if any(token in title for token in ("method", "approach", "model", "framework", "algorithm", "architecture", "方法", "模型", "算法", "框架")):
        return "technical_route" if paper_type == "survey" else "method"
    if any(token in title for token in ("experiment", "result", "analysis", "实验", "结果")):
        return "experiment"
    if any(token in title for token in ("conclusion", "结论", "总结")):
        return "conclusion"
    return "general"


def _build_heuristic_prerequisite_card(
    paper: dict[str, Any],
    sections: list[dict[str, Any]],
    paper_type: str,
) -> dict[str, Any]:
    combined = " ".join(
        [
            str(paper.get("title") or ""),
            str(paper.get("abstract") or ""),
            " ".join(str(section.get("title") or "") for section in sections[:30]),
        ]
    )
    concept_needles = (
        "Transformer",
        "attention",
        "LLM",
        "RAG",
        "reinforcement learning",
        "diffusion",
        "graph neural network",
        "benchmark",
        "dataset",
        "pretraining",
        "fine-tuning",
    )
    concepts = []
    for term in concept_needles:
        if term.lower() in combined.lower():
            concepts.append({
                "name": term,
                "why_needed": f"论文多处围绕 {term} 展开，先理解它有助于读懂后续章节。",
                "learn_first": [],
                "difficulty": "medium",
            })
    if not concepts:
        concepts.append({
            "name": "论文所在方向的基本任务定义",
            "why_needed": "先弄清输入、输出、评价目标，后续方法或综述分类才有坐标。",
            "learn_first": ["任务目标", "常用评价指标", "代表性 baseline"],
            "difficulty": "medium",
        })
    reading_order = (
        ["先读领域概览和分类体系", "再读技术路线与代表论文", "最后看数据集、评测协议和开放问题"]
        if paper_type == "survey"
        else ["先读摘要和引言", "再读方法结构", "最后用实验表格验证 claim"]
    )
    return {
        "concepts": concepts[:6],
        "baseline_papers": [],
        "reading_order": reading_order,
    }


def _build_heuristic_survey_map_legacy(
    paper: dict[str, Any],
    sections: list[dict[str, Any]],
    by_stage: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    title = str(paper.get("title") or "Survey")
    abstract = str(paper.get("abstract") or "")
    overview_sections = by_stage.get("abstract", []) + by_stage.get("introduction", [])
    taxonomy_sections = by_stage.get("taxonomy", []) + by_stage.get("technical_route", [])
    dataset_sections = by_stage.get("dataset", []) + by_stage.get("evaluation", [])
    challenge_sections = by_stage.get("challenge", []) + by_stage.get("conclusion", [])
    application_sections = by_stage.get("application", [])
    field_text = _compact_section_text(overview_sections, 1800) or abstract

    def section_items(source_sections: list[dict[str, Any]], key: str, label: str, limit: int = 10) -> list[dict[str, Any]]:
        items = []
        for index, section in enumerate(source_sections[:limit], start=1):
            text = " ".join(str(section.get("content") or "").split())
            title_value = str(section.get("title") or f"{label} {index}")
            items.append({
                key: title_value,
                "summary": _first_sentence(text) or f"阅读 {title_value}，理解综述在这里整理的{label}。",
                "source_sections": [_source_ref(section)],
            })
        return items

    technical_routes = []
    for index, section in enumerate(taxonomy_sections[:16], start=1):
        text = " ".join(str(section.get("content") or "").split())
        route_id = f"route_{index}"
        technical_routes.append({
            "route_id": route_id,
            "name": str(section.get("title") or f"技术路线 {index}"),
            "core_idea": _first_sentence(text) or "该路线需要通过 LLM 深化抽取。",
            "typical_pipeline": _second_sentence(text),
            "strengths": [],
            "weaknesses": [],
            "representative_method_ids": [],
            "source_sections": [_source_ref(section)],
        })

    datasets = []
    for section in dataset_sections[:20]:
        text = " ".join(str(section.get("content") or "").split())
        names = _find_terms(text, ("dataset", "benchmark", "corpus", "imagenet", "cifar", "glue", "hotpot", "wiki", "数据集", "基准"))
        datasets.append({
            "name": str(section.get("title") or "Dataset section"),
            "task": "",
            "content": _first_sentence(text),
            "structure": "",
            "scale": "",
            "metrics": _find_terms(text, ("accuracy", "f1", "precision", "recall", "bleu", "rouge", "pass@", "指标")),
            "mentioned_terms": names,
            "url": "",
            "source_sections": [_source_ref(section)],
        })

    return {
        "field_overview": {
            "field": title,
            "core_task": _first_sentence(field_text) or "这篇综述试图整理一个研究方向的核心任务和技术谱系。",
            "why_now": _second_sentence(field_text),
            "novice_takeaway": "先把综述的分类体系当作阅读地图，再进入每条技术路线下的代表论文。",
            "source_sections": [_source_ref(section) for section in overview_sections[:3]],
        },
        "development_timeline": section_items(by_stage.get("related_work", []) + overview_sections, "stage", "发展阶段", 8),
        "pain_points": section_items(challenge_sections, "problem", "领域痛点", 12),
        "taxonomy": section_items(taxonomy_sections, "category", "分类体系", 16),
        "technical_routes": technical_routes,
        "representative_methods": [],
        "datasets": datasets,
        "evaluation_protocols": section_items(by_stage.get("evaluation", []), "protocol", "评测方式", 12),
        "applications": section_items(application_sections, "application", "应用场景", 12),
        "open_challenges": section_items(challenge_sections, "challenge", "开放问题", 16),
        "reading_strategy": ["先读摘要和引言确认综述范围", "沿 taxonomy/technical route 章节建立分类框架", "把数据集、评测指标和开放问题作为后续精读入口"],
    }


def _build_heuristic_survey_map(
    paper: dict[str, Any],
    sections: list[dict[str, Any]],
    by_stage: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    title = str(paper.get("title") or "Survey")
    abstract = str(paper.get("abstract") or "")
    overview_sections = by_stage.get("abstract", []) + by_stage.get("introduction", [])
    if not overview_sections:
        overview_sections = [section for section in sections[:4] if _section_role(section, "survey") != "references"]

    taxonomy_sections = by_stage.get("taxonomy", [])
    route_sections = by_stage.get("technical_route", [])
    dataset_sections = by_stage.get("dataset", []) + by_stage.get("evaluation", [])
    challenge_sections = by_stage.get("challenge", []) + by_stage.get("conclusion", [])
    if not challenge_sections:
        challenge_sections = [section for section in sections[-4:] if _section_role(section, "survey") != "references"]
    application_sections = by_stage.get("application", [])
    field_text = _compact_section_text(overview_sections, 1800) or abstract

    def section_items(source_sections: list[dict[str, Any]], key: str, label: str, limit: int = 10) -> list[dict[str, Any]]:
        items = []
        for index, section in enumerate(source_sections[:limit], start=1):
            text = " ".join(str(section.get("content") or "").split())
            title_value = str(section.get("title") or f"{label} {index}")
            items.append({
                key: title_value,
                "summary": _first_sentence(text) or f"Read {title_value} to understand this survey dimension.",
                "source_sections": [_source_ref(section)],
            })
        return items

    technical_routes = []
    for index, section in enumerate((route_sections or taxonomy_sections)[:16], start=1):
        text = " ".join(str(section.get("content") or "").split())
        route_id = f"route_{index}"
        technical_routes.append({
            "route_id": route_id,
            "name": str(section.get("title") or f"Technical route {index}"),
            "core_idea": _first_sentence(text) or "Use this section to identify the route's core mechanism.",
            "typical_pipeline": _second_sentence(text),
            "strengths": [],
            "weaknesses": [],
            "representative_method_ids": [],
            "source_sections": [_source_ref(section)],
        })

    representative_methods = []
    method_source_sections = (route_sections + taxonomy_sections + dataset_sections)[:20]
    for index, section in enumerate(method_source_sections, start=1):
        text = " ".join(str(section.get("content") or "").split())
        section_title = str(section.get("title") or f"Representative method group {index}")
        representative_methods.append({
            "method_name": section_title,
            "paper_title": "",
            "year": "",
            "route": section_title,
            "method_summary": _first_sentence(text) or "This source section groups representative methods discussed by the survey.",
            "specific_solution": _second_sentence(text) or "Ask the Agent to expand this card into concrete cited papers and methods.",
            "url": "",
            "source_sections": [_source_ref(section)],
        })

    datasets = []
    for section in dataset_sections[:20]:
        text = " ".join(str(section.get("content") or "").split())
        datasets.append({
            "name": str(section.get("title") or "Dataset section"),
            "task": "",
            "content": _first_sentence(text),
            "structure": "",
            "scale": "",
            "metrics": _find_terms(text, ("accuracy", "f1", "precision", "recall", "bleu", "rouge", "pass@", "metric", "指标")),
            "mentioned_terms": _find_terms(text, ("dataset", "benchmark", "corpus", "imagenet", "cifar", "glue", "hotpot", "wiki", "数据集", "基准")),
            "url": "",
            "source_sections": [_source_ref(section)],
        })

    timeline_sections = by_stage.get("related_work", []) + overview_sections + taxonomy_sections[:4] + route_sections[:4]
    evaluation_sections = by_stage.get("evaluation", []) or dataset_sections

    return {
        "field_overview": {
            "field": title,
            "core_task": _first_sentence(field_text) or "This survey organizes the core task, taxonomy, methods, resources, and open problems of a research field.",
            "why_now": _second_sentence(field_text),
            "novice_takeaway": "Read the taxonomy first, then follow each technical route to its datasets, representative methods, and open problems.",
            "source_sections": [_source_ref(section) for section in overview_sections[:3]],
        },
        "development_timeline": section_items(timeline_sections, "stage", "Development stage", 12),
        "pain_points": section_items(challenge_sections, "problem", "Pain point", 12),
        "taxonomy": section_items(taxonomy_sections or route_sections, "category", "Taxonomy category", 16),
        "technical_routes": technical_routes,
        "representative_methods": representative_methods,
        "datasets": datasets,
        "evaluation_protocols": section_items(evaluation_sections, "protocol", "Evaluation protocol", 12),
        "applications": section_items(application_sections, "application", "Application", 12),
        "open_challenges": section_items(challenge_sections, "challenge", "Open challenge", 16),
        "reading_strategy": [
            "Start with the overview and taxonomy to fix the survey scope.",
            "Follow the technical-route cards to connect mechanisms with representative methods.",
            "Use datasets, evaluation protocols, and open challenges as anchors for deep reading.",
        ],
    }


def _legacy_guide_cards(guide: dict[str, Any]) -> list[dict[str, Any]]:
    source = {
        "section_id": guide.get("section_id", ""),
        "title": guide.get("title", ""),
        "page": guide.get("source_page"),
    }
    cards = []
    if guide.get("main_content") or guide.get("novice_summary"):
        cards.append({
            "card_type": "reading_route",
            "title": "本节怎么读",
            "content": {
                "summary": guide.get("novice_summary") or guide.get("main_content", ""),
                "focus": guide.get("novice_focus", ""),
            },
            "source_sections": [source],
        })
    if guide.get("technical_route") or guide.get("implementation_plan"):
        cards.append({
            "card_type": "method_architecture",
            "title": "技术路线",
            "content": {
                "technical_route": guide.get("technical_route", ""),
                "implementation_plan": guide.get("implementation_plan", ""),
            },
            "source_sections": [source],
        })
    if guide.get("datasets") or guide.get("baselines") or guide.get("metrics"):
        cards.append({
            "card_type": "experiment_dataset",
            "title": "实验与数据",
            "content": {
                "datasets": guide.get("datasets", []),
                "baselines": guide.get("baselines", []),
                "metrics": guide.get("metrics", []),
                "dataset_format": guide.get("dataset_format", ""),
                "experiment_protocol": guide.get("experiment_protocol", ""),
            },
            "source_sections": [source],
        })
    return cards


def _guide_cards_for_section(
    guide: dict[str, Any],
    section: dict[str, Any],
    text: str,
    paper_type: str,
) -> list[dict[str, Any]]:
    source = [_source_ref(section)]
    role = guide.get("section_role", "general")
    summary = guide.get("novice_summary") or _first_sentence(text)
    cards = [{
        "card_type": "reading_route",
        "title": "基础阅读提示",
        "fallback_generated": True,
        "content": {
            "summary": summary,
            "focus": guide.get("novice_focus", ""),
            "read_priority": guide.get("read_priority", "medium"),
            "quality_note": "低信息兜底：本节未获得 LLM 结构化卡片，建议重新生成以获取正式分析。",
        },
        "source_sections": source,
    }]
    if paper_type == "survey":
        survey_card_by_role = {
            "taxonomy": "taxonomy_node",
            "technical_route": "route_comparison",
            "dataset": "dataset_catalog",
            "evaluation": "benchmark_protocol",
            "application": "application_landscape",
            "challenge": "challenge_card",
            "related_work": "field_timeline",
            "introduction": "field_timeline",
        }
        card_type = survey_card_by_role.get(role)
        if card_type:
            cards.append({
                "card_type": card_type,
                "title": f"基础{_card_title_for_type(card_type)}",
                "fallback_generated": True,
                "content": {
                    "main_point": summary,
                    "details": _second_sentence(text),
                    "terms": _find_terms(text, ("dataset", "benchmark", "method", "model", "challenge", "数据集", "基准", "方法", "模型", "挑战")),
                    "quality_note": "低信息兜底：本节未获得 LLM 结构化卡片，建议重新生成以获取正式分析。",
                },
                "source_sections": source,
            })
    else:
        research_card_by_role = {
            "abstract": "abstract_takeaway",
            "introduction": "intro_insight",
            "method": "method_architecture",
            "experiment": "experiment_design",
            "dataset": "experiment_dataset",
            "evaluation": "experiment_design",
            "conclusion": "limitation_reflection",
        }
        card_type = research_card_by_role.get(role)
        if card_type:
            cards.append({
                "card_type": card_type,
                "title": _card_title_for_type(card_type),
                "fallback_generated": True,
                "content": {
                    "main_point": summary,
                    "details": _second_sentence(text),
                    "datasets": guide.get("datasets", []),
                    "baselines": guide.get("baselines", []),
                    "metrics": guide.get("metrics", []),
                },
                "source_sections": source,
            })
    return cards


def _card_title_for_type(card_type: str) -> str:
    labels = {
        "abstract_takeaway": "摘要速读",
        "intro_insight": "引言洞察",
        "problem_formulation": "问题定义",
        "method_architecture": "方法结构",
        "algorithm_steps": "算法步骤",
        "innovation_detail": "改进细节",
        "experiment_dataset": "数据集信息",
        "experiment_design": "实验设计",
        "result_interpretation": "结果解读",
        "limitation_reflection": "局限反思",
        "field_timeline": "发展脉络",
        "taxonomy_node": "分类体系",
        "route_comparison": "技术路线",
        "paper_method_table": "代表论文方法",
        "dataset_catalog": "数据集目录",
        "benchmark_protocol": "评测协议",
        "challenge_card": "难点痛点",
        "application_landscape": "应用场景",
        "future_direction": "未来方向",
        "reading_route": "阅读路线",
    }
    return labels.get(card_type, "阅读卡片")


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


def _section_extraction_info(paper: dict[str, Any]) -> dict[str, Any]:
    source = str(paper.get("section_extraction_source") or "")
    status = str(paper.get("section_extraction_status") or "")
    message = str(paper.get("section_extraction_message") or "")
    outline_entries_count = int(paper.get("outline_entries_count") or 0)
    if not source and paper.get("parse_status") == "done":
        source = "heuristic"
        status = "legacy_heuristic"
        message = "该论文解析记录缺少 PDF 内置目录来源标记，按旧版启发式章节识别结果展示；索引可能不等于论文真实目录。"
    return {
        "source": source,
        "status": status,
        "message": message,
        "outline_entries_count": outline_entries_count,
    }


def _section_index_for_prompt(sections: list[dict[str, Any]]) -> str:
    lines = []
    for section in sections[:120]:
        indent = "  " * max(int(section.get("level", 1)) - 1, 0)
        lines.append(f"{indent}- {section.get('section_id', '')}: {section.get('title', '')}")
    return "\n".join(lines) or "(无章节索引)"


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

    section_info = _section_extraction_info(paper)
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
        "section_extraction_source": section_info["source"],
        "section_extraction_status": section_info["status"],
        "section_extraction_message": section_info["message"],
        "outline_entries_count": section_info["outline_entries_count"],
        "parse_status": paper.get("parse_status", ""),
        "parse_error": paper.get("parse_error", ""),
        "page_count": paper.get("page_count", 0),
        "reading_map": paper.get("reading_map") or _empty_reading_map(paper.get("parse_status", "pending")),
        "reading_map_status": paper.get("reading_map_status", ""),
        "reading_map_phase": paper.get("reading_map_phase", ""),
        "reading_map_progress": paper.get("reading_map_progress", 0),
        "reading_map_error": paper.get("reading_map_error", ""),
        "reading_map_card_progress": (
            paper.get("reading_map_artifacts", {}).get("survey_card_progress", {})
            if isinstance(paper.get("reading_map_artifacts"), dict)
            else {}
        ),
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
