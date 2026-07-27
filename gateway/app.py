"""声明 NoviceSynapse gateway 应用与启动函数。"""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from agents.agent import create_agent
from bus.message_bus import MessageBus
from channels.base import ChannelMessage
from channels.web import WebChannel
from config.manager import load_config
from handlers.chat_handler import handle_chat_message
from handlers.domain_onboarding.audit import create_audit_sink_from_env
from handlers.domain_onboarding_handler import handle_domain_onboarding_message
from handlers.domain_onboarding_metrics import DomainOnboardingMetrics
from handlers.paper_reading_handler import handle_paper_reading_message
from gateway.message_flow import process_channel_input
from models.client import OpenAIClient
from handlers.paper_reading.harness.session import SessionManager
from handlers.paper_reading.harness.storage import PaperReadingStorage
from handlers.paper_reading.harness.fork_merge import ForkMergeManager
from handlers.paper_reading.kg.engine import KnowledgeGraphEngine
from handlers.paper_reading.kg.builder import ProgressiveKGBuilder
from handlers.paper_reading.kg.query import KGQueryEngine
from handlers.paper_reading.pipeline.sources import PaperPipeline
from skills.registry import create_skill_registry
from skills.selector import CapabilitySelector
from tools.registry import create_builtin_tool_registry
from tools.builtin.kg_query_tool import set_kg_engine
from tools.builtin.kg_build_tool import set_kg_builder

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="NoviceSynapse Gateway")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# 返回最小健康检查结果。
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "novicesynapse-gateway"}


# 返回首页。
@app.get("/")
def home_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# 返回聊天页。
@app.get("/app")
def chat_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "chat.html")


# 返回嵌入应用层级的论文精读工作台。
@app.get("/app/paper-reading")
def paper_reading_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "paper-reading" / "index.html")


# 旧入口回到聊天页的论文精读模式。
@app.get("/paper-reading", include_in_schema=False)
@app.get("/paper_reading/app", include_in_schema=False)
def legacy_paper_reading_page() -> RedirectResponse:
    return RedirectResponse(url="/app?mode=paper_reading")


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


# paper_reading 功能入口。
@app.post("/paper_reading")
async def paper_reading(request: Request) -> ChannelMessage:
    return await process_channel_input(
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


@app.get("/metrics/domain_onboarding")
def domain_onboarding_metrics(request: Request) -> dict:
    metrics = getattr(request.app.state, "domain_onboarding_metrics", None)
    if metrics is None:
        raise HTTPException(status_code=503, detail="Domain onboarding is not initialized.")
    return metrics.snapshot()


@app.on_event("shutdown")
def close_domain_onboarding_resources() -> None:
    pipeline = getattr(app.state, "domain_onboarding_pipeline", None)
    if pipeline is not None:
        pipeline.close()
    audit_sink = getattr(app.state, "domain_onboarding_audit_sink", None)
    if audit_sink is not None:
        audit_sink.close()


# 启动 gateway 服务。
def start_gateway_server(host: str, port: int) -> None:
    config = load_config()
    model = OpenAIClient(config.client)
    chat_agent = create_agent(model, "chat")
    domain_onboarding_agent = create_agent(model, "domain_onboarding")
    paper_reading_agent = create_agent(model, "paper_reading")

    paper_storage = PaperReadingStorage()
    kg_engine = KnowledgeGraphEngine()
    kg_builder = ProgressiveKGBuilder(kg_engine)
    kg_query_engine = KGQueryEngine(kg_engine, model)
    session_manager = SessionManager(storage=paper_storage)
    fork_manager = ForkMergeManager(session_manager, kg_engine)
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
    app.state.chat_agent = chat_agent
    app.state.domain_onboarding_agent = domain_onboarding_agent
    app.state.paper_reading_agent = paper_reading_agent
    app.state.domain_onboarding_metrics = DomainOnboardingMetrics(
        input_cost_per_million_tokens=config.client.input_cost_per_million_tokens,
        output_cost_per_million_tokens=config.client.output_cost_per_million_tokens,
    )
    app.state.domain_onboarding_audit_sink = create_audit_sink_from_env()
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

    uvicorn.run(app, host=host, port=port)
