from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from channels.base import ChannelMessage
from gateway.message_flow import (
    _BACKGROUND_STREAM_TASKS,
    cancel_stream_generation,
    process_channel_stream,
)
from storage.local_store import LocalResearchStore


class _DisconnectedRequest:
    async def is_disconnected(self) -> bool:
        return True


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


class _Channel:
    def __init__(self, generation_id: str = "") -> None:
        self.generation_id = generation_id

    async def receive_message(self, _source, mode: str) -> ChannelMessage:
        return ChannelMessage(
            session_id="detached-chat",
            channel="web",
            direction="inbound",
            mode=mode,
            content="生成一个完整回答",
            metadata={"generation_id": self.generation_id} if self.generation_id else {},
        )

    def publish_inbound(self, _message) -> None:
        return None

    def send_outbound(self, _message) -> None:
        return None


class MessageStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_stop_still_cancels_generation(self) -> None:
        generation_id = "cancel-me"
        state = SimpleNamespace(
            research_storage=None,
            default_channel_name="web",
            channels={"web": _Channel(generation_id)},
        )

        def handler(message, _state):
            cancel_event = message.metadata["_stream_cancel_event"]
            for _ in range(100):
                if cancel_event.is_set():
                    return {"text": "", "interrupted": True, "status": "ok"}
                time.sleep(0.01)
            return {"text": "未取消", "status": "ok"}

        response = await process_channel_stream(_ConnectedRequest(), "chat", handler, state)
        events = response.body_iterator
        self.assertIn("event: ready", await anext(events))
        self.assertTrue(cancel_stream_generation(generation_id))
        self.assertIn("event: result", await asyncio.wait_for(anext(events), timeout=2))
        await asyncio.sleep(0)
        self.assertFalse(cancel_stream_generation(generation_id))

    async def test_disconnect_detaches_delivery_but_generation_finishes_and_persists(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "research.sqlite3"
            store = LocalResearchStore(database)
            store.initialize()
            state = SimpleNamespace(
                research_storage=store,
                default_channel_name="web",
                channels={"web": _Channel()},
            )

            def handler(_message, _state):
                time.sleep(0.25)
                return {"text": "这是后台完成的正常回复。", "status": "ok"}

            response = await process_channel_stream(
                _DisconnectedRequest(), "chat", handler, state
            )
            events = response.body_iterator
            self.assertIn("event: ready", await anext(events))
            with self.assertRaises(StopAsyncIteration):
                await anext(events)

            for _ in range(20):
                await asyncio.sleep(0.05)
                with closing(sqlite3.connect(database)) as connection:
                    rows = connection.execute(
                        "SELECT role, content FROM messages ORDER BY sequence_number"
                    ).fetchall()
                if len(rows) == 2:
                    break

            for _ in range(20):
                if not _BACKGROUND_STREAM_TASKS:
                    break
                await asyncio.sleep(0.05)

            self.assertEqual(
                rows,
                [
                    ("user", "生成一个完整回答"),
                    ("assistant", "这是后台完成的正常回复。"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
