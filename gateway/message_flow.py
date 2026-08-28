"""提供 gateway 中 channel message 的统一处理流程。"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from threading import Event, Lock
from typing import Any, Callable

from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from bus.events import OUTBOUND
from channels.base import BaseChannel, ChannelMessage
from storage.message_recorder import record_inbound, record_outbound

MessageHandler = Callable[[ChannelMessage, Any], Any]
logger = logging.getLogger(__name__)

# Keep detached generations alive after their browser SSE connection closes.
# asyncio only keeps weak references to tasks; this set is also useful for
# observing failures that can no longer be delivered to the disconnected page.
_BACKGROUND_STREAM_TASKS: set[asyncio.Task[Any]] = set()
_STREAM_CANCEL_EVENTS: dict[str, Event] = {}
_STREAM_CANCEL_LOCK = Lock()
_STREAM_GENERATIONS: dict[str, dict[str, Any]] = {}
_STREAM_GENERATIONS_LOCK = Lock()
_STREAM_GENERATION_TTL_SECONDS = 60 * 60
_STREAM_GENERATION_LIMIT = 128


def _prune_stream_generations(now: float) -> None:
    expired = [
        generation_id
        for generation_id, item in _STREAM_GENERATIONS.items()
        if item.get("status") != "running"
        and now - float(item.get("updated_monotonic") or now) > _STREAM_GENERATION_TTL_SECONDS
    ]
    for generation_id in expired:
        _STREAM_GENERATIONS.pop(generation_id, None)
    if len(_STREAM_GENERATIONS) <= _STREAM_GENERATION_LIMIT:
        return
    completed = sorted(
        (
            (float(item.get("updated_monotonic") or 0), generation_id)
            for generation_id, item in _STREAM_GENERATIONS.items()
            if item.get("status") != "running"
        )
    )
    for _, generation_id in completed[: max(0, len(_STREAM_GENERATIONS) - _STREAM_GENERATION_LIMIT)]:
        _STREAM_GENERATIONS.pop(generation_id, None)


def _start_stream_generation(generation_id: str, message: ChannelMessage) -> None:
    if not generation_id:
        return
    now = time.monotonic()
    with _STREAM_GENERATIONS_LOCK:
        _prune_stream_generations(now)
        _STREAM_GENERATIONS[generation_id] = {
            "generation_id": generation_id,
            "session_id": message.session_id,
            "mode": message.mode,
            "status": "running",
            "text": "",
            "reasoning": "",
            "result": None,
            "error": "",
            "updated_monotonic": now,
        }


def _append_stream_generation(generation_id: str, field: str, delta: str) -> None:
    if not generation_id or not delta:
        return
    with _STREAM_GENERATIONS_LOCK:
        item = _STREAM_GENERATIONS.get(generation_id)
        if item is None:
            return
        item[field] = str(item.get(field) or "") + delta
        item["updated_monotonic"] = time.monotonic()


def _finish_stream_generation(
    generation_id: str,
    task: asyncio.Task[Any],
    cancel_event: Event,
) -> None:
    if not generation_id:
        return
    try:
        result = task.result()
        result_content = result.content
        status = "interrupted" if cancel_event.is_set() else "completed"
        error = ""
    except asyncio.CancelledError:
        result_content = None
        status = "interrupted"
        error = ""
    except Exception as exc:
        result_content = None
        status = "failed"
        error = str(exc)
    with _STREAM_GENERATIONS_LOCK:
        item = _STREAM_GENERATIONS.get(generation_id)
        if item is None:
            return
        item.update({
            "status": status,
            "result": result_content,
            "error": error,
            "updated_monotonic": time.monotonic(),
        })


def get_stream_generation(
    generation_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """Return a reconnectable snapshot for a browser that left mid-stream."""
    normalized = str(generation_id or "").strip()
    if not normalized:
        return None
    now = time.monotonic()
    with _STREAM_GENERATIONS_LOCK:
        _prune_stream_generations(now)
        item = _STREAM_GENERATIONS.get(normalized)
        if item is None:
            return None
        if session_id and str(item.get("session_id") or "") != str(session_id):
            return None
        snapshot = copy.deepcopy(item)
    snapshot.pop("updated_monotonic", None)
    return snapshot


def cancel_stream_generation(generation_id: str) -> bool:
    """Explicitly cancel a generation without conflating it with navigation."""
    with _STREAM_CANCEL_LOCK:
        event = _STREAM_CANCEL_EVENTS.get(str(generation_id or "").strip())
    if event is None:
        return False
    event.set()
    return True


def _track_stream_task(task: asyncio.Task[Any]) -> None:
    _BACKGROUND_STREAM_TASKS.add(task)

    def finished(completed: asyncio.Task[Any]) -> None:
        _BACKGROUND_STREAM_TASKS.discard(completed)
        try:
            completed.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Detached channel generation failed")

    task.add_done_callback(finished)


# 将 handler 输出包装成 outbound message。
def build_channel_output(message: ChannelMessage, content: Any) -> ChannelMessage:
    outbound = ChannelMessage(
        session_id=message.session_id,
        channel=message.channel,
        direction=OUTBOUND,
        mode=message.mode,
        content=content,
        user_id=message.user_id,
    )
    outbound_id = str(message.metadata.get("_outbound_message_id") or "").strip()
    if outbound_id:
        outbound.message_id = outbound_id
        outbound.metadata["_upsert_outbound"] = True
    return outbound


def _install_external_stream_persistence(message: ChannelMessage, app_state: Any) -> None:
    """Expose external-channel deltas to the web UI through one live DB row."""
    if (
        message.mode != "chat"
        or message.channel == "web"
        or callable(message.metadata.get("_stream_text_delta"))
    ):
        return
    store = getattr(app_state, "research_storage", None)
    if store is None or not callable(getattr(store, "upsert_message", None)):
        return

    outbound_id = f"{message.message_id}:assistant"
    message.metadata["_outbound_message_id"] = outbound_id
    text_parts: list[str] = []
    write_lock = Lock()
    last_write = 0.0

    def persist_delta(delta: str) -> None:
        nonlocal last_write
        if not delta:
            return
        with write_lock:
            text_parts.append(str(delta))
            now = time.monotonic()
            # Persist the first visible fragment immediately, then limit SQLite
            # writes while still keeping the browser update cadence responsive.
            if last_write and now - last_write < 0.12:
                return
            visible_text = "".join(text_parts)
            last_write = now
        try:
            store.upsert_message(
                message.session_id,
                role="assistant",
                content=visible_text,
                mode=message.mode,
                channel=message.channel,
                message_id=outbound_id,
            )
        except Exception:
            logger.exception("Failed to persist live %s response", message.channel)

    message.metadata["_stream_text_delta"] = persist_delta


# 执行 channel inbound、handler 处理、channel outbound 的标准流程。
def process_channel_message(
    channel: BaseChannel,
    message: ChannelMessage,
    handler: MessageHandler,
    app_state: Any,
) -> ChannelMessage:
    channel.publish_inbound(message)
    record_inbound(app_state, message)
    _install_external_stream_persistence(message, app_state)
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
        delivery_closed = Event()
        cancel_event = Event()
        generation_id = str(inbound_message.metadata.get("generation_id") or "").strip()[:100]
        _start_stream_generation(generation_id, inbound_message)
        if generation_id:
            with _STREAM_CANCEL_LOCK:
                _STREAM_CANCEL_EVENTS[generation_id] = cancel_event

        def emit_text(delta: str) -> None:
            if delta:
                _append_stream_generation(generation_id, "text", delta)
            if delta and not delivery_closed.is_set():
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, _sse("delta", {"text": delta}))
                except RuntimeError:
                    delivery_closed.set()

        def emit_reasoning(delta: str) -> None:
            if delta:
                _append_stream_generation(generation_id, "reasoning", delta)
            if delta and not delivery_closed.is_set():
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, _sse("reasoning", {"text": delta}))
                except RuntimeError:
                    delivery_closed.set()

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
        _track_stream_task(task)
        if generation_id:
            task.add_done_callback(
                lambda completed: _finish_stream_generation(generation_id, completed, cancel_event)
            )
        if generation_id:
            def clear_cancel_handle(_completed: asyncio.Task[Any]) -> None:
                with _STREAM_CANCEL_LOCK:
                    if _STREAM_CANCEL_EVENTS.get(generation_id) is cancel_event:
                        _STREAM_CANCEL_EVENTS.pop(generation_id, None)

            task.add_done_callback(clear_cancel_handle)
        disconnected = False
        try:
            yield _sse("ready", {"mode": mode})
            while not task.done():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=0.15)
                except asyncio.TimeoutError:
                    if await source.is_disconnected():
                        disconnected = True
                        # Navigation closes only the delivery channel. Generation
                        # must continue so its normal answer is persisted and can
                        # be restored when the user returns to this conversation.
                        delivery_closed.set()
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
            delivery_closed.set()
            raise
        finally:
            delivery_closed.set()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
