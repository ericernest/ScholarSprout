"""实现飞书机器人对应的 channel 适配器。"""

from __future__ import annotations

import json
import logging
from threading import Lock, Thread
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
)

from bus.events import INBOUND
from gateway.message_flow import process_channel_message
from handlers.chat_handler import handle_chat_message

from .base import BaseChannel, ChannelMessage


logger = logging.getLogger(__name__)


class FeishuChannel(BaseChannel):
    """负责飞书消息进入 NoviceSynapse，以及将结果发送回飞书。"""

    name = "feishu"

    def __init__(
        self,
        bus,
        app_id: str,
        app_secret: str,
        app_state: Any,
    ):
        self.bus = bus
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_state = app_state
        self._thread: Thread | None = None
        self._processed_message_ids: set[str] = set()
        self._processed_message_lock = Lock()

        self._client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )

        self._event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .build()
        )

        self._ws_client = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=self._event_handler,
            log_level=lark.LogLevel.INFO,
        )

    def start(self) -> None:
        """在独立线程中启动飞书 WebSocket 长连接。"""
        if self._thread is not None and self._thread.is_alive():
            return

        self._thread = Thread(
            target=self._run_ws,
            name="feishu-websocket",
            daemon=True,
        )
        self._thread.start()

        logger.info("FeishuChannel started.")

    def stop(self) -> None:
        """停止飞书 channel。"""
        logger.info("FeishuChannel stopping.")

    def _run_ws(self) -> None:
        """WebSocket 独立线程入口。"""
        try:
            self._ws_client.start()
        except Exception:
            logger.exception("Feishu WebSocket stopped unexpectedly.")

    async def receive_message(
        self,
        source: Any,
        mode: str = "chat",
    ) -> ChannelMessage:
        """把飞书原始事件转换为统一 ChannelMessage。"""
        return self._create_inbound_message(source, mode)

    def _create_inbound_message(
        self,
        event: Any,
        mode: str = "chat",
    ) -> ChannelMessage:
        """同步地把飞书事件转换为统一 ChannelMessage。"""
        message = event.event.message
        sender = event.event.sender

        content = self._extract_text(message.content)

        chat_id = str(message.chat_id or "")
        message_id = str(message.message_id or "")

        user_id = None
        if sender and sender.sender_id:
            user_id = (
                sender.sender_id.open_id
                or sender.sender_id.user_id
                or sender.sender_id.union_id
            )

        return ChannelMessage(
            session_id=chat_id,
            channel=self.name,
            direction=INBOUND,
            mode=mode,
            content=content,
            user_id=user_id,
            metadata={
                "chat_id": chat_id,
                "feishu_message_id": message_id,
                "message_type": message.message_type,
            },
        )

    def publish_inbound(self, message: ChannelMessage) -> None:
        """将飞书 inbound message 发布到统一 MessageBus。"""
        self.bus.publish_message(message)

    def send_outbound(self, message: ChannelMessage) -> None:
        """把 NoviceSynapse 输出发送回飞书。"""
        chat_id = str(
            message.metadata.get("chat_id")
            or message.session_id
        )

        text = self._format_output(message.content)

        content = json.dumps(
            {"text": text},
            ensure_ascii=False,
        )

        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(content)
                .build()
            )
            .build()
        )

        response = self._client.im.v1.message.create(request)

        if not response.success():
            logger.error(
                "Failed to send Feishu message: code=%s msg=%s",
                response.code,
                response.msg,
            )

    def _on_message(self, event) -> None:
        """处理飞书 im.message.receive_v1 事件。"""
        try:
            message = event.event.message

            if message.message_type != "text":
                logger.info(
                    "Ignore unsupported Feishu message type: %s",
                    message.message_type,
                )
                return

            message_id = str(message.message_id or "")

            # 飞书事件可能重复投递，同一个 message_id 只处理一次。
            if message_id:
                with self._processed_message_lock:
                    if message_id in self._processed_message_ids:
                        logger.info(
                            "Ignore duplicated Feishu message: %s",
                            message_id,
                        )
                        return

                    self._processed_message_ids.add(message_id)

                    # V0.1 简单限制缓存大小，避免长期运行无限增长。
                    if len(self._processed_message_ids) > 1000:
                        self._processed_message_ids.clear()
                        self._processed_message_ids.add(message_id)

            logger.warning(
                "===== FEISHU MESSAGE RECEIVED: %s =====",
                message_id,
            )

            inbound_message = self._create_inbound_message(
                event,
                mode="chat",
            )

            # Agent/LLM 调用耗时，不阻塞飞书事件回调。
            Thread(
                target=self._process_inbound_message,
                args=(inbound_message,),
                name=f"feishu-message-{message_id}",
                daemon=True,
            ).start()

        except Exception:
            logger.exception("Failed to receive Feishu message.")

    def _process_inbound_message(
        self,
        inbound_message: ChannelMessage,) -> None:
        """在后台线程中执行 NoviceSynapse Agent。"""
        try:
            process_channel_message(
                channel=self,
                message=inbound_message,
                handler=handle_chat_message,
                app_state=self.app_state,
            )
        except Exception:
            logger.exception(
                "Failed to process Feishu message: %s",
                inbound_message.metadata.get("feishu_message_id"),
            )

    @staticmethod
    def _extract_text(raw_content: str) -> str:
        """解析飞书 text 消息中的 JSON content。"""
        try:
            payload = json.loads(raw_content or "{}")
        except json.JSONDecodeError:
            return str(raw_content or "")

        return str(payload.get("text") or "").strip()

    @staticmethod
    def _format_output(content: Any) -> str:
        """把 handler 输出转换成飞书文本消息。"""
        if isinstance(content, str):
            return content

        if isinstance(content, dict):
            for key in (
                "content",
                "text",
                "answer",
                "message",
            ):
                value = content.get(key)

                if isinstance(value, str):
                    return value

            return json.dumps(
                content,
                ensure_ascii=False,
                default=str,
            )

        return str(content)