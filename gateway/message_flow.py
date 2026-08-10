"""提供 gateway 中 channel message 的统一处理流程。"""

from __future__ import annotations

import asyncio
import json
from threading import Event
from typing import Any, Callable

from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from bus.events import OUTBOUND
from channels.base import BaseChannel, ChannelMessage
from storage.message_recorder import record_inbound, record_outbound

MessageHandler = Callable[[ChannelMessage, Any], Any]


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


# 执行 channel inbound、handler 处理、channel outbound 的标准流程。
def process_channel_message(
    channel: BaseChannel,
    message: ChannelMessage,
    handler: MessageHandler,
    app_state: Any,
) -> ChannelMessage:
    channel.publish_inbound(message)
    record_inbound(app_state, message)
    content = handler(message, app_state)
    outbound_message = build_channel_output(message, content)
    record_outbound(app_state, outbound_message)
    channel.send_outbound(outbound_message)

    return outbound_message


# 处理任意 channel 原始输入，具体 channel 由启动阶段注册。
async def process_channel_input(
    source: Any,
    mode: str,
    handler: MessageHandler,
    app_state: Any,
    channel_name: str | None = None,
) -> ChannelMessage:
    selected_channel_name = channel_name or app_state.default_channel_name
    channel = app_state.channels[selected_channel_name]
    inbound_message = await channel.receive_message(source, mode)

    return await run_in_threadpool(
        process_channel_message,
        channel=channel,
        message=inbound_message,
        handler=handler,
        app_state=app_state,
    )


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def process_channel_stream(
    source: Request,
    mode: str,
    handler: MessageHandler,
    app_state: Any,
    channel_name: str | None = None,
) -> StreamingResponse:
    """Run the normal channel pipeline and expose native LLM deltas as SSE."""
    selected_channel_name = channel_name or app_state.default_channel_name
    channel = app_state.channels[selected_channel_name]
    inbound_message = await channel.receive_message(source, mode)

    async def events():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str] = asyncio.Queue()
        cancel_event = Event()

        def emit_text(delta: str) -> None:
            if delta and not cancel_event.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, _sse("delta", {"text": delta}))

        def emit_reasoning(delta: str) -> None:
            if delta and not cancel_event.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, _sse("reasoning", {"text": delta}))

        inbound_message.metadata["_stream_text_delta"] = emit_text
        inbound_message.metadata["_stream_reasoning_delta"] = emit_reasoning
        inbound_message.metadata["_stream_cancel_event"] = cancel_event
        task = asyncio.create_task(
            run_in_threadpool(
                process_channel_message,
                channel=channel,
                message=inbound_message,
                handler=handler,
                app_state=app_state,
            )
        )
        disconnected = False
        try:
            yield _sse("ready", {"mode": mode})
            while not task.done():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=0.15)
                except asyncio.TimeoutError:
                    if await source.is_disconnected():
                        disconnected = True
                        cancel_event.set()
                        break
            await asyncio.sleep(0)
            while not queue.empty() and not disconnected:
                yield queue.get_nowait()
            if not disconnected:
                try:
                    outbound = await task
                    yield _sse("result", outbound.content)
                except Exception as error:
                    yield _sse("error", {"message": str(error)})
        except asyncio.CancelledError:
            cancel_event.set()
            raise
        finally:
            cancel_event.set()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
