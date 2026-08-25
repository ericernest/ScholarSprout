"""声明 NoviceSynapse gateway 应用与启动函数。"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from dataclasses import replace
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from pydantic import ValidationError
from fastapi.staticfiles import StaticFiles

from agents.agent import create_agent
from bus.message_bus import MessageBus
from channels.base import ChannelMessage
from channels.web import WebChannel
from config.manager import is_setup_complete, load_config, resolve_data_dir
from config.web import router as config_router
from gateway.research_library import router as research_library_router
from handlers.chat_handler import handle_chat_message
from handlers.domain_onboarding.audit import create_audit_sink_from_env
from handlers.domain_onboarding.pipeline import create_default_pipeline
from handlers.domain_onboarding.jobs import (
    TERMINAL_STATES,
    DomainOnboardingJobManager,
    JobNotRetryableError,
    JobQueueFullError,
    JobRateLimitError,
    create_job_store_from_env,
)
from handlers.domain_onboarding.schemas import DomainOnboardingRequest
from handlers.domain_onboarding_handler import handle_domain_onboarding_message
from handlers.domain_onboarding_metrics import DomainOnboardingMetrics
from handlers.paper_reading_handler import (
    handle_paper_reading_message,
    resume_pending_reading_map_generations,
)
from gateway.message_flow import process_channel_input, process_channel_stream
from models.client import OpenAIClient, SetupRequiredModel
from memory import ConversationMemoryService
from handlers.paper_reading.harness.session import SessionManager
from storage import LocalResearchStore, PaperReadingStorage
from handlers.paper_reading.harness.fork_merge import ForkMergeManager
from handlers.paper_reading.kg.engine import KnowledgeGraphEngine
from handlers.paper_reading.kg.builder import ProgressiveKGBuilder
from handlers.paper_reading.kg.query import KGQueryEngine
from handlers.paper_reading.pipeline.sources import PaperPipeline
from handlers.paper_reading.pipeline.mineru import MinerUClient
from skills.registry import create_skill_registry
from skills.selector import CapabilitySelector
from tools.registry import create_builtin_tool_registry
from tools.builtin.kg_query_tool import set_kg_engine
from tools.builtin.kg_build_tool import set_kg_builder

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="NoviceSynapse Gateway")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(config_router)
app.include_router(research_library_router)


# 返回最小健康检查结果。
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "novicesynapse-gateway"}


@app.get("/ready")
def readiness(request: Request) -> dict[str, object]:
    components = {
        "model": (
            getattr(request.app.state, "model", None) is not None
            and getattr(request.app.state, "setup_complete", True)
        ),
        "domain_onboarding_pipeline": (
            getattr(request.app.state, "domain_onboarding_pipeline", None) is not None
        ),
        "domain_onboarding_metrics": (
            getattr(request.app.state, "domain_onboarding_metrics", None) is not None
        ),
        "domain_onboarding_audit": (
            getattr(request.app.state, "domain_onboarding_audit_sink", None) is not None
        ),
        "domain_onboarding_jobs": (
            getattr(request.app.state, "domain_onboarding_job_manager", None) is not None
        ),
        "research_storage": (
            getattr(request.app.state, "research_storage", None) is not None
        ),
    }
    if not all(components.values()):
        raise HTTPException(
            status_code=503,
            detail={"status": "not_ready", "components": components},
        )
    return {"status": "ready", "components": components}


# 返回首页。
@app.get("/")
def home_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# 返回聊天页。
@app.get("/app")
def chat_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "chat.html")


# 返回首次运行与后续修改共用的配置向导。
@app.get("/settings")
def settings_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "settings" / "index.html")


# 返回会话、模式产物和论文的统一资料库。
@app.get("/library")
def library_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "library" / "index.html")


# 返回嵌入应用层级的论文精读工作台。
@app.get("/app/paper-reading")
def paper_reading_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "paper-reading" / "index.html")


# 返回领域入门工作台。
@app.get("/app/domain-onboarding")
def domain_onboarding_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "domain-onboarding" / "index.html")


# 旧入口回到聊天页的论文精读模式。
@app.get("/paper-reading", include_in_schema=False)
@app.get("/paper_reading/app", include_in_schema=False)
def legacy_paper_reading_page() -> RedirectResponse:
    return RedirectResponse(url="/app?mode=paper_reading")


@app.get("/domain-onboarding", include_in_schema=False)
@app.get("/domain_onboarding/app", include_in_schema=False)
def legacy_domain_onboarding_page() -> RedirectResponse:
    return RedirectResponse(url="/app?mode=domain_onboarding")


# 返回浏览器标签页图标。
@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


# chat 功能入口。
@app.post("/chat")
async def chat(request: Request) -> ChannelMessage:
    return await process_channel_input(
        source=request,
        mode="chat",
        handler=handle_chat_message,
        app_state=request.app.state,
    )


@app.post("/chat/stream")
async def chat_stream(request: Request) -> StreamingResponse:
    return await process_channel_stream(
        source=request,
        mode="chat",
        handler=handle_chat_message,
        app_state=request.app.state,
    )


# paper_reading 功能入口。
@app.post("/paper_reading")
async def paper_reading(request: Request) -> ChannelMessage:
    return await process_channel_input(
        source=request,
        mode="paper_reading",
        handler=handle_paper_reading_message,
        app_state=request.app.state,
    )


@app.post("/paper_reading/stream")
async def paper_reading_stream(request: Request) -> StreamingResponse:
    return await process_channel_stream(
        source=request,
        mode="paper_reading",
        handler=handle_paper_reading_message,
        app_state=request.app.state,
    )


@app.get("/paper_reading/uploads/{paper_id}.pdf")
def paper_reading_upload_pdf(paper_id: str, request: Request) -> FileResponse:
    storage = getattr(request.app.state, "paper_storage", None)
    if storage is None:
        raise HTTPException(status_code=503, detail="Paper reading storage is not initialized.")

    upload_path = storage.get_upload_path(paper_id)
    if upload_path is None or not upload_path.exists():
        raise HTTPException(status_code=404, detail="PDF upload not found.")

    return FileResponse(
        upload_path,
        media_type="application/pdf",
        filename=f"{paper_id}.pdf",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@app.get("/paper_reading/figures/{paper_id}/{asset_name}")
def paper_reading_figure(
    paper_id: str,
    asset_name: str,
    request: Request,
) -> FileResponse:
    """Serve an extracted paper figure as an inline image."""
    storage = getattr(request.app.state, "paper_storage", None)
    if storage is None:
        raise HTTPException(status_code=503, detail="Paper reading storage is not initialized.")

    figure_path = storage.get_figure_path(paper_id, asset_name)
    if figure_path is None:
        raise HTTPException(status_code=404, detail="Paper figure not found.")

    return FileResponse(
        figure_path,
        media_type=mimetypes.guess_type(asset_name)[0] or "application/octet-stream",
        filename=asset_name,
        content_disposition_type="inline",
    )


# domain_onboarding 功能入口。
@app.post("/domain_onboarding")
async def domain_onboarding(request: Request) -> ChannelMessage:
    return await process_channel_input(
        source=request,
        mode="domain_onboarding",
        handler=handle_domain_onboarding_message,
        app_state=request.app.state,
    )


@app.post("/domain_onboarding/jobs", status_code=202)
async def create_domain_onboarding_job(request: Request) -> JSONResponse:
    manager = getattr(request.app.state, "domain_onboarding_job_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Domain onboarding jobs are not initialized.")
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object.")
    query = payload.get("query", payload.get("content", ""))
    try:
        job_request = DomainOnboardingRequest(
            query=query,
            session_id=payload.get("session_id"),
            user_id=payload.get("user_id"),
            metadata=payload.get("metadata") or {},
            language=payload.get("language", "zh-CN"),
        )
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors()) from error
    try:
        job = manager.submit(
            job_request,
            client_request_id=payload.get("client_request_id"),
            owner_key=_domain_job_owner_key(request, job_request),
        )
    except (JobQueueFullError, JobRateLimitError) as error:
        return JSONResponse(
            {"detail": str(error), "retry_after_seconds": 10},
            status_code=429,
            headers={"Retry-After": "10"},
        )
    body = {
        "task_id": job["task_id"], "state": job["state"], "revision": job["revision"],
        "access_token": job["access_token"],
        "poll_url": f"/domain_onboarding/jobs/{job['task_id']}",
        "events_url": f"/domain_onboarding/jobs/{job['task_id']}/events",
        "cancel_url": f"/domain_onboarding/jobs/{job['task_id']}",
        "retry_url": f"/domain_onboarding/jobs/{job['task_id']}/retry",
    }
    return JSONResponse(body, status_code=202)


@app.get("/domain_onboarding/jobs/{task_id}")
def get_domain_onboarding_job(task_id: str, request: Request) -> dict[str, object]:
    store = getattr(request.app.state, "domain_onboarding_job_store", None)
    job = store.get(task_id) if store is not None else None
    _require_domain_job_access(task_id, request, job)
    return job


@app.delete("/domain_onboarding/jobs/{task_id}", status_code=202)
def cancel_domain_onboarding_job(task_id: str, request: Request) -> JSONResponse:
    manager = getattr(request.app.state, "domain_onboarding_job_manager", None)
    store = getattr(request.app.state, "domain_onboarding_job_store", None)
    existing = store.get(task_id) if store is not None else None
    _require_domain_job_access(task_id, request, existing)
    job = manager.cancel(task_id) if manager is not None else None
    if job is None:
        raise HTTPException(status_code=404, detail="Domain onboarding job not found.")
    return JSONResponse({"task_id": task_id, "state": job["state"]}, status_code=202)


@app.post("/domain_onboarding/jobs/{task_id}/retry", status_code=202)
def retry_domain_onboarding_job(task_id: str, request: Request) -> JSONResponse:
    manager = getattr(request.app.state, "domain_onboarding_job_manager", None)
    store = getattr(request.app.state, "domain_onboarding_job_store", None)
    existing = store.get(task_id) if store is not None else None
    _require_domain_job_access(task_id, request, existing)
    if manager is None:
        raise HTTPException(status_code=503, detail="Domain onboarding jobs are not initialized.")
    try:
        previous_request = DomainOnboardingRequest.model_validate(existing["request"])
        job = manager.retry(
            task_id,
            owner_key=_domain_job_owner_key(request, previous_request),
        )
    except JobNotRetryableError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (JobQueueFullError, JobRateLimitError) as error:
        return JSONResponse(
            {"detail": str(error), "retry_after_seconds": 10},
            status_code=429,
            headers={"Retry-After": "10"},
        )
    body = {
        "task_id": job["task_id"],
        "parent_task_id": task_id,
        "state": job["state"],
        "revision": job["revision"],
        "access_token": job["access_token"],
        "poll_url": f"/domain_onboarding/jobs/{job['task_id']}",
        "events_url": f"/domain_onboarding/jobs/{job['task_id']}/events",
        "cancel_url": f"/domain_onboarding/jobs/{job['task_id']}",
    }
    return JSONResponse(body, status_code=202)


@app.get("/domain_onboarding/jobs/{task_id}/events")
async def stream_domain_onboarding_job(task_id: str, request: Request) -> StreamingResponse:
    store = getattr(request.app.state, "domain_onboarding_job_store", None)
    job = store.get(task_id) if store is not None else None
    _require_domain_job_access(task_id, request, job)
    header_cursor = request.headers.get("last-event-id")
    query_cursor = request.query_params.get("after")
    try:
        cursor = int(header_cursor or query_cursor or 0)
    except ValueError:
        raise HTTPException(status_code=400, detail="Last-Event-ID/after must be an integer.")

    async def events():
        nonlocal cursor
        idle_ticks = 0
        while True:
            if await request.is_disconnected():
                return
            rows = store.events_after(task_id, cursor)
            for event in rows:
                cursor = int(event["id"])
                if event["event"] in TERMINAL_STATES and event["data"].get("result_available"):
                    terminal_snapshot = store.get(task_id)
                    if terminal_snapshot is not None:
                        event = {
                            **event,
                            "data": {
                                **event["data"],
                                "result": terminal_snapshot.get("result"),
                            },
                        }
                yield f"id: {cursor}\nevent: {event['event']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            snapshot = store.get(task_id)
            if snapshot is None or snapshot["state"] in TERMINAL_STATES:
                return
            idle_ticks += 1
            if idle_ticks % 30 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Referrer-Policy": "no-referrer",
        },
    )


def _domain_job_access_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.query_params.get("access_token")


def _require_domain_job_access(
    task_id: str, request: Request, job: dict[str, object] | None
) -> None:
    manager = getattr(request.app.state, "domain_onboarding_job_manager", None)
    if job is None or manager is None or not manager.authorize(
        task_id, _domain_job_access_token(request)
    ):
        # Deliberately use the same response for missing and unauthorized jobs.
        raise HTTPException(status_code=404, detail="Domain onboarding job not found.")


def _domain_job_owner_key(
    request: Request, job_request: DomainOnboardingRequest | None = None
) -> str:
    host = request.client.host if request.client is not None else "unknown"
    if job_request is None:
        return host
    return f"{host}:{job_request.user_id or ''}:{job_request.session_id or ''}"


@app.get("/metrics/domain_onboarding")
def domain_onboarding_metrics(request: Request) -> dict:
    metrics = getattr(request.app.state, "domain_onboarding_metrics", None)
    if metrics is None:
        raise HTTPException(status_code=503, detail="Domain onboarding is not initialized.")
    return metrics.snapshot()


@app.get("/metrics/domain_onboarding/prometheus", response_class=PlainTextResponse)
def domain_onboarding_prometheus_metrics(request: Request) -> str:
    metrics = getattr(request.app.state, "domain_onboarding_metrics", None)
    if metrics is None:
        raise HTTPException(status_code=503, detail="Domain onboarding is not initialized.")
    return metrics.prometheus()


@app.on_event("shutdown")
def close_domain_onboarding_resources() -> None:
    manager = getattr(app.state, "domain_onboarding_job_manager", None)
    if manager is not None:
        manager.close()
    pipeline = getattr(app.state, "domain_onboarding_pipeline", None)
    if pipeline is not None:
        pipeline.close()
    for retired in getattr(app.state, "retired_runtime_resources", []):
        resource = retired
        close = getattr(resource, "close", None)
        if callable(close):
            close()
    audit_sink = getattr(app.state, "domain_onboarding_audit_sink", None)
    if audit_sink is not None:
        audit_sink.close()


# 启动 gateway 服务。
def start_gateway_server(host: str, port: int) -> None:
    config = load_config()
    model, embedding_model, setup_complete = create_runtime_models(config)
    chat_agent = create_agent(model, "chat")
    domain_onboarding_agent = create_agent(model, "domain_onboarding")
    paper_reading_agent = create_agent(model, "paper_reading")

    data_root = resolve_data_dir(config)
    research_storage = LocalResearchStore(data_root / "research.sqlite3")
    research_storage.initialize()
    memory_service = ConversationMemoryService(research_storage, model, recent_message_limit=8)
    paper_storage = PaperReadingStorage(
        data_root / "paper_reading", research_store=research_storage
    )
    kg_engine = KnowledgeGraphEngine()
    kg_builder = ProgressiveKGBuilder(kg_engine)
    kg_query_engine = KGQueryEngine(kg_engine, model)
    session_manager = SessionManager(storage=paper_storage)
    fork_manager = ForkMergeManager(
        session_manager, kg_engine, memory_service=memory_service, research_store=research_storage
    )
    paper_pipeline = PaperPipeline()

    # 依赖注入：KG 工具需要访问 KG 引擎和构建器
    set_kg_engine(kg_engine)
    set_kg_builder(kg_builder)
    message_bus = MessageBus()
    input_channel = WebChannel(bus=message_bus)
    tool_registry = create_builtin_tool_registry()
    skill_registry = create_skill_registry()
    capability_selector = CapabilitySelector()
    input_channel.start()

    app.state.model = model
    app.state.setup_complete = setup_complete
    app.state.chat_agent = chat_agent
    app.state.domain_onboarding_agent = domain_onboarding_agent
    app.state.paper_reading_agent = paper_reading_agent
    app.state.research_storage = research_storage
    app.state.memory_service = memory_service
    configure_domain_onboarding_runtime(
        app.state,
        model,
        config.client,
        research_storage=research_storage,
        embedding_model=embedding_model,
        embedding_model_name=config.embedding.model_name,
    )
    app.state.tool_registry = tool_registry
    app.state.skill_registry = skill_registry
    app.state.capability_selector = capability_selector
    app.state.message_bus = message_bus
    app.state.default_channel_name = input_channel.name
    app.state.channels = {input_channel.name: input_channel}
    # 论文精读组件
    app.state.paper_storage = paper_storage
    app.state.kg_engine = kg_engine
    app.state.kg_builder = kg_builder
    app.state.kg_query_engine = kg_query_engine
    app.state.session_manager = session_manager
    app.state.fork_manager = fork_manager
    app.state.paper_pipeline = paper_pipeline
    app.state.mineru_client = MinerUClient(config.mineru)
    app.state.retired_runtime_resources = []
    app.state.reload_runtime_config = lambda updated: reload_runtime_config(
        app.state, updated
    )
    if setup_complete:
        resume_pending_reading_map_generations(app.state)

    uvicorn.run(app, host=host, port=port)


def create_runtime_models(config: object) -> tuple[object, object, bool]:
    setup_complete = is_setup_complete(config)
    model = OpenAIClient(config.client) if setup_complete else SetupRequiredModel(config.client)
    embedding_base_url = (
        os.getenv("DOMAIN_ONBOARDING_EMBEDDING_BASE_URL")
        or config.embedding.base_url
        or config.client.base_url
    )
    embedding_api_key = config.embedding.api_key or config.client.api_key
    embedding_model = model
    if setup_complete and (
        embedding_base_url != config.client.base_url
        or embedding_api_key != config.client.api_key
    ):
        embedding_model = OpenAIClient(
            replace(
                config.client,
                api_key=embedding_api_key,
                base_url=embedding_base_url,
                model_name=config.embedding.model_name,
            )
        )
    return model, embedding_model, setup_complete


def reload_runtime_config(app_state: object, config: object) -> dict[str, object]:
    """Apply connection changes to new requests without restarting storage."""
    model, embedding_model, setup_complete = create_runtime_models(config)
    old_model = getattr(app_state, "model", None)
    old_embedding = getattr(app_state, "embedding_model", None)
    old_pipeline = getattr(app_state, "domain_onboarding_pipeline", None)
    new_pipeline = create_default_pipeline(
        model,
        embedding_model=embedding_model,
        embedding_model_name=config.embedding.model_name,
    )
    app_state.model = model
    app_state.embedding_model = embedding_model
    app_state.setup_complete = setup_complete
    app_state.chat_agent = create_agent(model, "chat")
    app_state.domain_onboarding_agent = create_agent(model, "domain_onboarding")
    app_state.paper_reading_agent = create_agent(model, "paper_reading")
    memory_service = getattr(app_state, "memory_service", None)
    if memory_service is not None:
        memory_service.model = model
    app_state.domain_onboarding_pipeline = new_pipeline
    manager = getattr(app_state, "domain_onboarding_job_manager", None)
    if manager is not None:
        manager.pipeline = new_pipeline
    query_engine = getattr(app_state, "kg_query_engine", None)
    if query_engine is not None:
        query_engine.model = model
    app_state.mineru_client = MinerUClient(config.mineru)
    retired = getattr(app_state, "retired_runtime_resources", None)
    if retired is None:
        retired = []
        app_state.retired_runtime_resources = retired
    for resource in (old_pipeline, old_embedding, old_model):
        if resource is not None and resource not in (model, embedding_model) and resource not in retired:
            retired.append(resource)
    return {"runtime_reloaded": True}


def configure_domain_onboarding_runtime(
    app_state: object,
    model: object,
    client_config: object,
    research_storage: LocalResearchStore | None = None,
    embedding_model: object | None = None,
    embedding_model_name: str | None = None,
) -> None:
    """Wire the V1 pipeline and its observability dependencies into the gateway."""
    app_state.domain_onboarding_pipeline = create_default_pipeline(
        model,
        embedding_model=embedding_model,
        embedding_model_name=embedding_model_name,
    )
    app_state.embedding_model = embedding_model
    app_state.domain_onboarding_metrics = DomainOnboardingMetrics(
        input_cost_per_million_tokens=getattr(
            client_config, "input_cost_per_million_tokens", None
        ),
        output_cost_per_million_tokens=getattr(
            client_config, "output_cost_per_million_tokens", None
        ),
    )
    app_state.domain_onboarding_audit_sink = create_audit_sink_from_env()
    app_state.domain_onboarding_job_store = create_job_store_from_env(
        research_storage.database_path if research_storage is not None else None
    )
    app_state.domain_onboarding_job_manager = DomainOnboardingJobManager(
        app_state.domain_onboarding_pipeline,
        app_state.domain_onboarding_job_store,
        metrics=app_state.domain_onboarding_metrics,
        audit_sink=app_state.domain_onboarding_audit_sink,
        max_workers=int(os.getenv("DOMAIN_ONBOARDING_JOB_WORKERS", "2")),
        max_queue_size=int(os.getenv("DOMAIN_ONBOARDING_JOB_QUEUE_SIZE", "20")),
        per_owner_active_limit=int(
            os.getenv("DOMAIN_ONBOARDING_JOB_OWNER_ACTIVE_LIMIT", "2")
        ),
        submissions_per_minute=int(
            os.getenv("DOMAIN_ONBOARDING_JOB_SUBMISSIONS_PER_MINUTE", "10")
        ),
        retention_seconds=int(
            os.getenv("DOMAIN_ONBOARDING_JOB_RETENTION_SECONDS", "604800")
        ),
        recovery_stale_seconds=int(
            os.getenv("DOMAIN_ONBOARDING_JOB_RECOVERY_STALE_SECONDS", "900")
        ),
        result_store=research_storage,
        memory_service=getattr(app_state, "memory_service", None),
    )
