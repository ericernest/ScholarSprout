"""声明 NoviceSynapse gateway 应用与启动函数。"""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agents.agent import create_agent
from bus.message_bus import MessageBus
from channels.base import ChannelMessage
from channels.web import WebChannel
from config.manager import load_config
from handlers.chat_handler import handle_chat_message
from handlers.domain_onboarding_handler import handle_domain_onboarding_message
from handlers.domain_onboarding_metrics import DomainOnboardingMetrics
from handlers.paper_reading_handler import handle_paper_reading_message
from gateway.message_flow import process_channel_input
from models.client import OpenAIClient
from tools.registry import create_builtin_tool_registry

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


# 启动 gateway 服务。
def start_gateway_server(host: str, port: int) -> None:
    config = load_config()
    model = OpenAIClient(config.client)
    chat_agent = create_agent(model, "chat")
    domain_onboarding_agent = create_agent(model, "domain_onboarding")
    message_bus = MessageBus()
    input_channel = WebChannel(bus=message_bus)
    tool_registry = create_builtin_tool_registry()
    input_channel.start()

    app.state.model = model
    app.state.chat_agent = chat_agent
    app.state.domain_onboarding_agent = domain_onboarding_agent
    app.state.domain_onboarding_metrics = DomainOnboardingMetrics(
        input_cost_per_million_tokens=config.client.input_cost_per_million_tokens,
        output_cost_per_million_tokens=config.client.output_cost_per_million_tokens,
    )
    app.state.tool_registry = tool_registry
    app.state.message_bus = message_bus
    app.state.default_channel_name = input_channel.name
    app.state.channels = {input_channel.name: input_channel}

    uvicorn.run(app, host=host, port=port)
