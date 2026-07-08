"""声明 NoviceSynapse gateway 应用与启动函数。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agents.agent import create_agent
from bus.events import OUTBOUND
from bus.message_bus import MessageBus
from channels.base import ChannelMessage
from channels.web import WebChannel
from config.manager import load_config
from handlers.chat_handler import handle_chat_message
from handlers.domain_onboarding_handler import handle_domain_onboarding_message
from handlers.paper_reading_handler import handle_paper_reading_message
from models.client import OpenAIClient
from tools.registry import create_builtin_tool_registry

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="NoviceSynapse Gateway")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# 从请求体中读取 session_id。
def _read_session_id(body: dict[str, Any]) -> str:
    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required.")
    return session_id


# 接收 channel 输入，并把 inbound message 写入 bus。
async def receive_channel_input(request: Request, mode: str) -> ChannelMessage:
    body = await request.json()
    metadata = body.get("metadata")
    web_channel = request.app.state.web_channel

    inbound_message = web_channel.create_inbound_message(
        session_id=_read_session_id(body),
        content=str(body.get("content") or ""),
        mode=mode,
        user_id=body.get("user_id"),
        metadata=metadata if isinstance(metadata, dict) else None,
    )
    web_channel.publish_inbound(inbound_message)

    return inbound_message


# 将 handler 输出包装成 outbound message。
def build_channel_output(message: ChannelMessage, content: Any) -> ChannelMessage:
    return ChannelMessage(
        session_id=message.session_id,
        channel=message.channel,
        direction=OUTBOUND,
        mode=message.mode,
        content=content,
        user_id=message.user_id,
    )


# 将 outbound message 写回 bus，并交给 channel 返回。
def send_channel_output(request: Request, message: ChannelMessage) -> ChannelMessage:
    request.app.state.web_channel.send_outbound(message)
    return message


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


# chat 功能入口。
@app.post("/chat")
async def chat(request: Request) -> ChannelMessage:
    inbound_message = await receive_channel_input(request, mode="chat")
    content = handle_chat_message(inbound_message, request.app.state)
    outbound_message = build_channel_output(inbound_message, content)

    return send_channel_output(request, outbound_message)


# paper_reading 功能入口。
@app.post("/paper_reading")
async def paper_reading(request: Request) -> ChannelMessage:
    inbound_message = await receive_channel_input(request, mode="paper_reading")
    content = handle_paper_reading_message(inbound_message, request.app.state)
    outbound_message = build_channel_output(inbound_message, content)

    return send_channel_output(request, outbound_message)


# domain_onboarding 功能入口。
@app.post("/domain_onboarding")
async def domain_onboarding(request: Request) -> ChannelMessage:
    inbound_message = await receive_channel_input(request, mode="domain_onboarding")
    content = handle_domain_onboarding_message(inbound_message, request.app.state)
    outbound_message = build_channel_output(inbound_message, content)

    return send_channel_output(request, outbound_message)

# 启动 gateway 服务。
def start_gateway_server(host: str, port: int) -> None:
    config = load_config()
    model = OpenAIClient(config.client)
    chat_agent = create_agent(model, "chat")
    message_bus = MessageBus()
    web_channel = WebChannel(bus=message_bus)
    tool_registry = create_builtin_tool_registry()
    web_channel.start()

    app.state.model = model
    app.state.chat_agent = chat_agent
    app.state.tool_registry = tool_registry
    app.state.message_bus = message_bus
    app.state.web_channel = web_channel
    app.state.channels = {"web": web_channel}

    uvicorn.run(app, host=host, port=port)
