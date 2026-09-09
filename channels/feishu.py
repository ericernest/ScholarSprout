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
    GetMessageResourceRequest,
)

from bus.events import INBOUND
from gateway.feishu_router import (
    handle_feishu_mode_message,
    mode_switch_reply,
    parse_mode_switch,
)
from gateway.message_flow import process_channel_message

from .base import BaseChannel, ChannelMessage


logger = logging.getLogger(__name__)


class FeishuChannel(BaseChannel):
    """负责飞书消息进入 ScholarSprout，以及将结果发送回飞书。"""

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

        # 防止飞书 WebSocket 重复投递同一条消息。
        self._processed_message_ids: set[str] = set()
        self._processed_message_lock = Lock()

        # 飞书 chat_id -> 当前业务模式。
        # 目前为进程内状态；服务重启后恢复为 chat。
        self._session_modes: dict[str, str] = {}
        self._session_modes_lock = Lock()

        self._client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )

        self._event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(
                self._on_message
            )
            .build()
        )

        self._ws_client = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=self._event_handler,
            log_level=lark.LogLevel.INFO,
        )

    def start(self) -> None:
        """启动飞书 WebSocket。"""

        if (
            self._thread is not None
            and self._thread.is_alive()
        ):
            return

        self._thread = Thread(
            target=self._run_ws,
            name="feishu-websocket",
            daemon=True,
        )
        self._thread.start()

        logger.info("FeishuChannel started.")

    def stop(self) -> None:
        """停止飞书 Channel。"""

        logger.info("FeishuChannel stopping.")

    def _run_ws(self) -> None:
        try:
            self._ws_client.start()
        except Exception:
            logger.exception(
                "Feishu WebSocket stopped unexpectedly."
            )

    async def receive_message(
        self,
        source: Any,
        mode: str = "chat",
    ) -> ChannelMessage:
        return self._create_inbound_message(
            source,
            mode,
        )

    def _create_inbound_message(
        self,
        event: Any,
        mode: str = "chat",
    ) -> ChannelMessage:
        """将飞书事件转换为统一 ChannelMessage。"""

        message = event.event.message
        sender = event.event.sender

        if message.message_type == "text":
            content: Any = self._extract_text(
                message.content
            )

        elif message.message_type == "file":
            content = self._extract_file(
                message.content
            )

        else:
            content = ""

        chat_id = str(
            message.chat_id or ""
        )

        message_id = str(
            message.message_id or ""
        )

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

    def publish_inbound(
        self,
        message: ChannelMessage,
    ) -> None:
        self.bus.publish_message(message)

    def send_outbound(
        self,
        message: ChannelMessage,
    ) -> None:
        """把 ScholarSprout 输出发送回飞书。"""
        chat_id = str(
            message.metadata.get("chat_id")
            or message.session_id
        )

        text = self._format_output(
            message.content
        )

        if (
            message.mode == "domain_onboarding"
            and "http://127.0.0.1:8000/library" not in text
        ):
            text = (
                f"{text}\n\n"
                "详细「入门路线」请在网页端查看：\n"
                "http://127.0.0.1:8000/library"
            )

        self._send_text_message(
            chat_id,
            text,
        )

    def _send_text_message(
        self,
        chat_id: str,
        text: str,
    ) -> None:
        """底层飞书文本发送方法。"""

        content = json.dumps(
            {
                "text": text,
            },
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

        response = self._client.im.v1.message.create(
            request
        )

        if not response.success():
            logger.error(
                "Failed to send Feishu message: "
                "code=%s msg=%s",
                response.code,
                response.msg,
            )

    def _send_status_message(
        self,
        chat_id: str,
        text: str,
    ) -> None:
        """
        发送处理中状态提示。

        状态提示不进入 process_channel_message，
        因此不会作为业务回复写入网页聊天历史。
        """
        try:
            self._send_text_message(
                chat_id,
                text,
            )
        except Exception:
            # 状态提示失败不能影响正式业务处理。
            logger.exception(
                "Failed to send Feishu status message."
            )

    def _on_message(
        self,
        event,
    ) -> None:
        """飞书 WebSocket 消息事件入口。"""

        try:
            message = event.event.message

            if message.message_type not in {
                "text",
                "file",
            }:
                logger.info(
                    "Ignore unsupported Feishu "
                    "message type: %s",
                    message.message_type,
                )
                return

            message_id = str(
                message.message_id or ""
            )

            # 去重。
            if message_id:
                with self._processed_message_lock:
                    if (
                        message_id
                        in self._processed_message_ids
                    ):
                        logger.info(
                            "Ignore duplicated Feishu "
                            "message: %s",
                            message_id,
                        )
                        return

                    self._processed_message_ids.add(
                        message_id
                    )

                    # 避免集合无限增长。
                    if (
                        len(self._processed_message_ids)
                        > 1000
                    ):
                        self._processed_message_ids.clear()
                        self._processed_message_ids.add(
                            message_id
                        )

            logger.warning(
                "===== FEISHU MESSAGE RECEIVED: %s =====",
                message_id,
            )

            inbound_message = (
                self._create_inbound_message(
                    event,
                    mode="chat",
                )
            )

            # 不阻塞飞书 WebSocket 回调线程。
            Thread(
                target=self._process_inbound_message,
                args=(inbound_message,),
                name=(
                    f"feishu-message-{message_id}"
                ),
                daemon=True,
            ).start()

        except Exception:
            logger.exception(
                "Failed to receive Feishu message."
            )

    def _process_inbound_message(
        self,
        inbound_message: ChannelMessage,
    ) -> None:
        """在后台线程中执行 ScholarSprout Agent。"""
        try:
            session_id = inbound_message.session_id

            with self._session_modes_lock:
                current_mode = self._session_modes.get(
                    session_id,
                    "chat",
                )

            switched_mode: str | None = None
            remaining_content = ""

            # 只有文本消息参与自然语言模式切换。
            if isinstance(
                inbound_message.content,
                str,
            ):
                (
                    switched_mode,
                    remaining_content,
                ) = parse_mode_switch(
                    inbound_message.content
                )

            # -------------------------------------------------
            # 模式切换
            # -------------------------------------------------

            if switched_mode:
                with self._session_modes_lock:
                    self._session_modes[
                        session_id
                    ] = switched_mode

                current_mode = switched_mode
                inbound_message.mode = switched_mode

                # 纯模式切换：
                #
                #   切换到入门模式
                #
                # 只返回模式切换提示，不调用模型。
                if not remaining_content:
                    reply_text = mode_switch_reply(
                        switched_mode
                    )

                    def switch_handler(
                        message: ChannelMessage,
                        app_state: Any,
                    ) -> dict[str, str]:
                        return {
                            "text": reply_text,
                            "status": "ok",
                        }

                    process_channel_message(
                        channel=self,
                        message=inbound_message,
                        handler=switch_handler,
                        app_state=self.app_state,
                    )
                    return

                # 模式切换 + 正文：
                #
                #   切换到入门模式，
                #   大模型与多智能体系统
                #
                # 切换完成后，把正文直接交给新模式 handler。
                inbound_message.content = (
                    remaining_content
                )

            inbound_message.mode = current_mode

            # -------------------------------------------------
            # 飞书处理状态提示
            # -------------------------------------------------

            if current_mode == "domain_onboarding":
                self._send_status_message(
                    session_id,
                    "正在思考中，请稍候...",
                )

            elif current_mode == "paper_reading":
                # PDF 或论文链接会明显耗时，
                # 此时显示处理状态。
                should_show_paper_status = False

                if isinstance(
                    inbound_message.content,
                    dict,
                ):
                    should_show_paper_status = True

                elif isinstance(
                    inbound_message.content,
                    str,
                ):
                    text = (
                        inbound_message.content
                        .lower()
                    )

                    if (
                        "http://" in text
                        or "https://" in text
                    ):
                        should_show_paper_status = True

                if should_show_paper_status:
                    self._send_status_message(
                        session_id,
                        "正在读取并解析论文，请稍候...",
                    )

            # -------------------------------------------------
            # 正式业务路由
            # -------------------------------------------------

            def routed_handler(
                message: ChannelMessage,
                app_state: Any,
            ) -> dict[str, Any]:
                return handle_feishu_mode_message(
                    message,
                    app_state,
                    mode=current_mode,
                    download_feishu_file=(
                        self._download_message_file
                    ),
                )

            process_channel_message(
                channel=self,
                message=inbound_message,
                handler=routed_handler,
                app_state=self.app_state,
            )

        except Exception:
            logger.exception(
                "Failed to process Feishu message: %s",
                inbound_message.metadata.get(
                    "feishu_message_id"
                ),
            )

    @staticmethod
    def _extract_text(
        raw_content: str,
    ) -> str:
        """解析飞书 text 消息中的 JSON content。"""

        try:
            payload = json.loads(
                raw_content or "{}"
            )
        except json.JSONDecodeError:
            return str(
                raw_content or ""
            )

        return str(
            payload.get("text") or ""
        ).strip()

    @staticmethod
    def _extract_file(
        raw_content: str,
    ) -> dict[str, str]:
        """解析飞书 file 消息。"""

        try:
            payload = json.loads(
                raw_content or "{}"
            )
        except json.JSONDecodeError:
            return {}

        return {
            "file_key": str(
                payload.get("file_key") or ""
            ),
            "file_name": str(
                payload.get("file_name") or ""
            ),
        }

    def _download_message_file(
        self,
        message_id: str,
        file_key: str,
    ) -> bytes:
        """通过飞书消息资源接口下载用户发送的文件。"""

        if not message_id:
            raise RuntimeError(
                "缺少飞书 message_id，无法下载文件。"
            )

        if not file_key:
            raise RuntimeError(
                "缺少飞书 file_key，无法下载文件。"
            )

        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(file_key)
            .type("file")
            .build()
        )

        response = (
            self._client
            .im.v1.message_resource
            .get(request)
        )

        if not response.success():
            raise RuntimeError(
                "飞书文件下载失败："
                f"code={response.code} "
                f"msg={response.msg}"
            )

        if response.file is None:
            raise RuntimeError(
                "飞书文件下载成功，"
                "但响应中没有文件内容。"
            )

        file_data = response.file.read()

        if not isinstance(
            file_data,
            (bytes, bytearray),
        ):
            raise RuntimeError(
                "飞书返回的文件内容格式异常。"
            )

        return bytes(file_data)

    @staticmethod
    def _format_output(
        content: Any,
    ) -> str:
        """将统一 Channel 输出转换成飞书文本。"""

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
