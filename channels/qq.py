from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from threading import Lock
from typing import Any, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import botpy
from botpy.message import C2CMessage

from bus.events import INBOUND
from gateway.message_flow import process_channel_message
from gateway.qq_router import handle_qq_mode_message

from .base import BaseChannel, ChannelMessage, format_plain_text


QQ_PDF_MAX_BYTES = int(os.getenv("QQ_PDF_MAX_BYTES", str(30 * 1024 * 1024)))
QQ_RECONNECT_INITIAL_SECONDS = max(
    1.0,
    float(os.getenv("QQ_RECONNECT_INITIAL_SECONDS", "5")),
)
QQ_RECONNECT_MAX_SECONDS = max(
    QQ_RECONNECT_INITIAL_SECONDS,
    float(os.getenv("QQ_RECONNECT_MAX_SECONDS", "60")),
)
logger = logging.getLogger(__name__)


class QQBotClient(botpy.Client):
    """QQ 官方机器人客户端。"""

    def __init__(self, qq_channel, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.qq_channel = qq_channel

    async def on_ready(self):
        self.qq_channel._qq_loop = asyncio.get_running_loop()
        print(f"[QQ] connected: {self.robot.name}")

    async def on_c2c_message_create(
        self,
        message: C2CMessage,
    ):
        self.qq_channel._qq_loop = asyncio.get_running_loop()
        await self.qq_channel.handle_c2c_message(
            message
        )


class QQChannel(BaseChannel):
    """
    QQ 官方机器人 Channel Adapter。

    功能：
    1. QQ 对话进入 NoviceSynapse 正式消息链路
    2. 对话可进入网页资料库
    3. 普通聊天
    4. 领域入门
    5. 论文精读
    6. 论文 URL
    7. QQ PDF 附件
    """

    name = "qq"

    def __init__(
        self,
        bus,
        app_state,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
    ):
        self.bus = bus
        self.app_state = app_state

        self.app_id = (
            app_id
            or os.getenv("QQ_APP_ID")
        )

        self.app_secret = (
            app_secret
            or os.getenv("QQ_APP_SECRET")
        )

        if not self.app_id or not self.app_secret:
            raise RuntimeError(
                "QQ_APP_ID / QQ_APP_SECRET 未配置"
            )

        # 每个 QQ 用户单独保存当前模式
        self._modes: dict[str, str] = {}

        # 保存当前正在回复的 QQ 原始消息
        self._reply_messages: dict[
            str,
            C2CMessage,
        ] = {}
        self._reply_messages_lock = Lock()

        # QQ Gateway 可能重复投递事件，避免重复生成和重复入库。
        self._processed_message_ids: set[str] = set()
        self._processed_message_lock = Lock()

        self.client = None
        self._thread = None
        self._stop_event = threading.Event()

    # =========================================================
    # Session
    # =========================================================

    def _session_id(
        self,
        user_id: str,
    ) -> str:
        """
        QQ 私聊 session_id。

        这个 ID 会跟随 ChannelMessage
        进入资料库存储。
        """
        return f"qq_private_{user_id}"

    # =========================================================
    # Mode
    # =========================================================

    def _get_mode(
        self,
        session_id: str,
    ) -> str:
        return self._modes.get(
            session_id,
            "chat",
        )

    def _set_mode(
        self,
        session_id: str,
        mode: str,
    ) -> None:
        self._modes[session_id] = mode

    def _mode_name(
        self,
        mode: str,
    ) -> str:
        names = {
            "chat": "日常聊天",
            "paper_reading": "论文精读",
            "domain_onboarding": "领域入门",
        }

        return names.get(
            mode,
            mode,
        )

    def _parse_mode_switch(
        self,
        text: str,
    ) -> tuple[Optional[str], str]:
        """识别纯模式命令，以及“模式命令 + 本次正文”。"""

        raw_text = str(text or "").strip()
        lowered = raw_text.lower()
        compact = re.sub(r"\s+", "", lowered)

        command_groups = {
            "chat": {
                "聊天", "聊天模式", "日常聊天", "日常聊天模式",
                "普通聊天", "普通聊天模式", "切换聊天", "切换到聊天",
                "切换日常聊天", "切换到日常聊天", "切换到日常聊天模式",
                "切换普通聊天",
                "切换到普通聊天", "进入聊天", "进入日常聊天",
                "进入普通聊天", "/聊天", "/日常聊天", "/chat", "chat",
            },
            "paper_reading": {
                "论文精读", "论文精读模式", "论文阅读", "切换论文精读",
                "切换到论文精读", "切换论文阅读", "切换到论文阅读",
                "进入论文精读", "进入论文阅读", "/论文", "/精读",
                "/paper", "paper", "paperreading",
            },
            "domain_onboarding": {
                "入门", "入门模式", "领域入门", "领域入门模式",
                "切换入门", "切换到入门模式", "切换领域入门",
                "切换到领域入门", "切换到领域入门模式", "进入领域入门",
                "/入门", "/领域入门", "/onboarding", "domainonboarding",
            },
        }

        # 保留原有的宽松纯命令匹配，例如“切换 到 领域入门”。
        for mode, commands in command_groups.items():
            if compact in {re.sub(r"\s+", "", item.lower()) for item in commands}:
                return mode, ""

        # 同一条消息可写成“领域入门 大模型”或“/入门：大模型”。
        # 必须有明确分隔符，避免把普通句子误判成模式切换。
        separator = re.compile(r"^[\s,，:：;；、\-—]+")
        for mode, commands in command_groups.items():
            for command in sorted(commands, key=len, reverse=True):
                normalized_command = command.lower()
                if not lowered.startswith(normalized_command):
                    continue
                tail = raw_text[len(command):]
                match = separator.match(tail)
                if match:
                    return mode, tail[match.end():].strip()

        return None, ""

    def _detect_mode_switch(
        self,
        text: str,
    ) -> Optional[str]:
        """兼容旧调用方；新流程使用 ``_parse_mode_switch``。"""

        mode, _ = self._parse_mode_switch(text)
        return mode

    def _is_mode_query(
        self,
        text: str,
    ) -> bool:

        text = str(text or "").strip()

        return text in {
            "现在是什么模式",
            "当前是什么模式",
            "当前模式",
            "现在什么模式",
            "什么模式",
            "我现在是什么模式",
        }

    # =========================================================
    # Result formatting
    # =========================================================

    def _extract_reply_text(
        self,
        result,
    ) -> str:
        """
        将 Handler 返回值统一转换成文本。
        """

        if result is None:
            return "暂时没有生成回复。"

        if isinstance(result, str):
            return result.strip()

        if isinstance(result, dict):
            for key in (
                "text",
                "content",
                "answer",
                "message",
            ):
                value = result.get(key)

                if value:
                    return str(value).strip()

            return "暂时没有生成有效回复。"

        for attr in (
            "text",
            "content",
            "answer",
            "message",
        ):
            value = getattr(
                result,
                attr,
                None,
            )

            if value:
                return str(value).strip()

        return str(result).strip()

    def _normalize_reply(
        self,
        mode: str,
        text: str,
    ) -> str:
        """
        对不同模式最终输出做 Channel 层处理。
        """

        text = str(text or "").strip()

        # -----------------------------
        # 论文精读错误提示
        # -----------------------------
        if (
            mode == "paper_reading"
            and "请求解析失败" in text
        ):
            return (
                "当前处于「论文精读」模式。\n\n"
                "请发送论文链接，例如：\n"
                "https://arxiv.org/abs/xxxx.xxxxx\n\n"
                "也可以直接发送 PDF 文件。"
            )

        # 领域入门直接展示正文，不追加网页入口。兼容旧 Handler 或历史
        # 路由仍返回“前往网页端查看”的情况，把整段引导一并清除。
        if mode == "domain_onboarding":
            text = self._strip_domain_web_prompt(text)

        return format_plain_text(text)

    @staticmethod
    def _strip_domain_web_prompt(text: str) -> str:
        """删除领域入门回复中的网页跳转引导，保留实际正文。"""

        cleaned = str(text or "")
        prompt_patterns = (
            r"(?:[ \t]{2,}|\n+)?详细\s*[「『]?入门路线[」』]?\s*"
            r"请(?:在|前往)?网页端查看\s*[：:]?\s*(?:https?://\S+)?",
            r"(?:[ \t]{2,}|\n+)?请(?:点击|前往|在)\s*"
            r"(?:网页端|Web\s*端)\s*(?:查看|访问|打开)\s*[：:]?\s*"
            r"(?:https?://\S+)?",
        )
        for pattern in prompt_patterns:
            cleaned = re.sub(
                pattern,
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    # =========================================================
    # Channel contract / attachment helpers
    # =========================================================

    def publish_inbound(self, message: ChannelMessage) -> None:
        self.bus.publish_message(message)

    async def receive_message(
        self,
        source: Any,
        mode: str = "chat",
    ) -> ChannelMessage:
        """将 QQ C2C 原始消息转换为统一 ChannelMessage。"""

        return self._create_inbound_message(source, mode)

    @staticmethod
    def _attachment_field(attachment: Any, name: str) -> str:
        if isinstance(attachment, dict):
            value = attachment.get(name)
        else:
            value = getattr(attachment, name, None)
        return str(value or "").strip()

    def _extract_pdf_attachment(self, qq_message: C2CMessage) -> dict[str, str] | None:
        for attachment in list(getattr(qq_message, "attachments", None) or []):
            file_url = self._attachment_field(attachment, "url")
            file_name = (
                self._attachment_field(attachment, "filename")
                or self._attachment_field(attachment, "file_name")
                or "paper.pdf"
            )
            file_name = file_name.replace("\\", "/").rsplit("/", 1)[-1] or "paper.pdf"
            content_type = (
                self._attachment_field(attachment, "content_type")
                .lower()
                .split(";", 1)[0]
                .strip()
            )
            if not file_url:
                continue
            if file_name.lower().endswith(".pdf") or content_type == "application/pdf":
                return {
                    "file_url": file_url,
                    "file_name": file_name,
                    "content_type": content_type or "application/pdf",
                }
        return None

    def _create_inbound_message(
        self,
        qq_message: C2CMessage,
        mode: str,
    ) -> ChannelMessage:
        user_id = str(qq_message.author.user_openid)
        session_id = self._session_id(user_id)
        text = str(qq_message.content or "").strip()
        pdf_attachment = self._extract_pdf_attachment(qq_message)
        content: Any = pdf_attachment if pdf_attachment is not None else text

        return ChannelMessage(
            session_id=session_id,
            channel=self.name,
            direction=INBOUND,
            mode=mode,
            content=content,
            user_id=user_id,
            metadata={
                "qq_message_id": str(qq_message.id or ""),
                "qq_openid": user_id,
                "message_type": "c2c",
                "has_pdf_attachment": pdf_attachment is not None,
            },
        )

    @staticmethod
    def _normalize_attachment_url(file_url: str) -> str:
        normalized = str(file_url or "").strip()
        if normalized.startswith("//"):
            normalized = f"https:{normalized}"
        elif "://" not in normalized:
            normalized = f"https://{normalized.lstrip('/')}"
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("QQ 附件地址无效。")
        return normalized

    def _download_qq_file(self, file_url: str) -> bytes:
        """下载 QQ 消息附件，并限制体积与文件类型。"""

        normalized_url = self._normalize_attachment_url(file_url)
        request = Request(
            normalized_url,
            headers={"User-Agent": "NoviceSynapse-QQ/1.0"},
        )
        with urlopen(request, timeout=30) as response:
            declared_size = response.headers.get("Content-Length")
            try:
                if declared_size and int(declared_size) > QQ_PDF_MAX_BYTES:
                    raise RuntimeError("PDF 文件过大，无法处理。")
            except ValueError:
                # Some CDNs omit or return a non-numeric Content-Length.  The
                # bounded read below remains the authoritative size check.
                pass
            file_data = response.read(QQ_PDF_MAX_BYTES + 1)

        if len(file_data) > QQ_PDF_MAX_BYTES:
            raise RuntimeError("PDF 文件过大，无法处理。")
        if not file_data.startswith(b"%PDF-"):
            raise RuntimeError("附件不是有效的 PDF 文件。")
        return file_data

    def _remember_source_message(self, qq_message: C2CMessage) -> None:
        message_id = str(qq_message.id or "")
        if not message_id:
            return
        with self._reply_messages_lock:
            self._reply_messages[message_id] = qq_message
            if len(self._reply_messages) > 1000:
                # Dict 保留插入顺序，只淘汰最早的已处理消息。
                oldest = next(iter(self._reply_messages))
                self._reply_messages.pop(oldest, None)

    def _is_duplicate(self, message_id: str) -> bool:
        if not message_id:
            return False
        with self._processed_message_lock:
            if message_id in self._processed_message_ids:
                return True
            self._processed_message_ids.add(message_id)
            if len(self._processed_message_ids) > 1000:
                self._processed_message_ids.clear()
                self._processed_message_ids.add(message_id)
        return False

    # =========================================================
    # Incoming
    # =========================================================

    async def handle_c2c_message(
        self,
        qq_message: C2CMessage,
    ):
        """
        QQ 私聊入口。
        """

        message_id = str(qq_message.id or "")
        if self._is_duplicate(message_id):
            logger.info("Ignore duplicated QQ message: %s", message_id)
            return

        text = str(qq_message.content or "").strip()
        pdf_attachment = self._extract_pdf_attachment(qq_message)

        if not text and pdf_attachment is None:
            return

        user_id = str(
            qq_message.author.user_openid
        )

        session_id = self._session_id(
            user_id
        )

        print(
            f"[QQ] inbound "
            f"user={user_id} "
            f"text={text!r}"
        )

        # 保存当前 QQ 消息，
        # send_outbound 时用于被动回复
        self._remember_source_message(qq_message)

        current_mode = self._get_mode(session_id)

        # PDF 是无歧义的论文输入，直接切到论文精读模式。
        if pdf_attachment is not None:
            current_mode = "paper_reading"
            self._set_mode(session_id, current_mode)

        message = self._create_inbound_message(
            qq_message,
            current_mode,
        )

        # -----------------------------------------------------
        # 查询模式
        # -----------------------------------------------------

        if self._is_mode_query(text):

            def query_handler(_message, _app_state):
                return (
                    "当前是"
                    f"「{self._mode_name(current_mode)}」"
                    "模式。"
                )

            await asyncio.to_thread(
                process_channel_message,
                channel=self,
                message=message,
                handler=query_handler,
                app_state=self.app_state,
            )
            return

        # -----------------------------------------------------
        # 切换模式
        # -----------------------------------------------------

        if pdf_attachment is None:
            new_mode, remaining_content = self._parse_mode_switch(text)
        else:
            new_mode, remaining_content = None, ""

        if new_mode:

            self._set_mode(
                session_id,
                new_mode,
            )

            message.mode = new_mode

            # “领域入门 大模型”等写法需要在切换后继续处理正文，不能
            # 提前返回，否则会被误认为普通聊天且无法生成入门卡片。
            if remaining_content:
                message.content = remaining_content
                current_mode = new_mode
            else:

                def switch_handler(_message, _app_state):
                    return (
                        "已切换到"
                        f"「{self._mode_name(new_mode)}」"
                        "模式。"
                    )

                await asyncio.to_thread(
                    process_channel_message,
                    channel=self,
                    message=message,
                    handler=switch_handler,
                    app_state=self.app_state,
                )
                return

        mode = current_mode

        # -----------------------------------------------------
        # 正式消息路由
        #
        # 不再直接调用 Handler，
        # 而是复用 message_flow，
        # 从而进入资料库存储。
        # -----------------------------------------------------

        def routed_handler(
            routed_message,
            app_state,
        ):
            return handle_qq_mode_message(
                routed_message,
                app_state,
                mode=mode,
                download_qq_file=self._download_qq_file,
            )

        try:
            # Handler/Pipeline 都可能耗时，
            # 放到工作线程，避免堵住 QQ WebSocket 心跳。
            await asyncio.to_thread(
                process_channel_message,
                channel=self,
                message=message,
                handler=routed_handler,
                app_state=self.app_state,
            )

        except Exception as exc:

            print(
                "[QQ] message handling failed:",
                repr(exc),
            )

            await self._reply(
                qq_message,
                "处理消息时出现错误，请稍后再试。",
            )

    # =========================================================
    # Outbound
    # =========================================================

    def send_outbound(
        self,
        message: ChannelMessage,
    ) -> None:
        """
        message_flow 最终通过这里将业务回复发回 QQ。
        """

        source_message_id = str(
            message.metadata.get("qq_message_id") or ""
        )
        with self._reply_messages_lock:
            qq_message = self._reply_messages.pop(
                source_message_id,
                None,
            )

        if qq_message is None:
            print(
                "[QQ] outbound skipped: "
                f"no source message for {source_message_id}"
            )
            return

        text = self._extract_reply_text(
            message.content
        )

        text = self._normalize_reply(
            message.mode,
            text,
        )

        if not text:
            return

        # send_outbound 是同步接口，
        # 而 botpy API 是 async。
        # 将发送任务提交到 QQ Client 所在 loop。
        loop = getattr(
            self,
            "_qq_loop",
            None,
        )

        if loop is None:
            print(
                "[QQ] outbound failed: "
                "QQ event loop not ready"
            )
            return

        future = asyncio.run_coroutine_threadsafe(
            self._reply(
                qq_message,
                text,
            ),
            loop,
        )

        def _done(f):
            try:
                f.result()
            except Exception as exc:
                print(
                    "[QQ] outbound failed:",
                    repr(exc),
                )

        future.add_done_callback(
            _done
        )

    async def _reply(
        self,
        qq_message: C2CMessage,
        text: str,
    ):
        """
        QQ C2C 被动回复。
        """

        text = str(
            text or ""
        ).strip()

        if not text:
            return

        await qq_message._api.post_c2c_message(
            openid=qq_message.author.user_openid,
            msg_type=0,
            msg_id=qq_message.id,
            content=text,
        )

    # =========================================================
    # Lifecycle
    # =========================================================

    def start(self):
        """
        独立线程启动 QQ Bot WebSocket。
        """

        def run():
            retry_delay = QQ_RECONNECT_INITIAL_SECONDS

            while not self._stop_event.is_set():
                started_at = time.monotonic()
                loop = asyncio.new_event_loop()
                try:
                    asyncio.set_event_loop(
                        loop
                    )

                    self._qq_loop = loop

                    intents = botpy.Intents(
                        public_messages=True,
                    )

                    client = QQBotClient(
                        self,
                        intents=intents,
                    )

                    self.client = client

                    print(
                        "[QQ] 正在连接 "
                        "QQ Bot WebSocket..."
                    )

                    # AppID/AppSecret 是长期配置。每次重建 Client 时由
                    # botpy 重新获取短期 token，不需要人工再次配置。
                    client.run(
                        appid=self.app_id,
                        secret=self.app_secret,
                    )

                except Exception:
                    logger.exception("QQ Bot connection stopped unexpectedly")

                finally:
                    self.client = None
                    if not loop.is_closed():
                        loop.close()

                if self._stop_event.is_set():
                    break

                # A connection that stayed alive for a while gets the short
                # retry delay again; repeated startup failures back off.
                if time.monotonic() - started_at >= 60:
                    retry_delay = QQ_RECONNECT_INITIAL_SECONDS
                logger.warning(
                    "QQ Bot reconnecting in %.1f seconds with saved credentials",
                    retry_delay,
                )
                if self._stop_event.wait(retry_delay):
                    break
                retry_delay = min(
                    retry_delay * 2,
                    QQ_RECONNECT_MAX_SECONDS,
                )

        if self._thread is not None and self._thread.is_alive():
            return self._thread

        self._stop_event.clear()
        thread = threading.Thread(
            target=run,
            daemon=True,
            name="qq-bot",
        )

        thread.start()
        self._thread = thread

        return thread

    def stop(self) -> None:
        """请求 QQ SDK 停止；不同 botpy 版本没有统一关闭接口。"""

        self._stop_event.set()
        client = self.client
        for method_name in ("close", "stop"):
            method = getattr(client, method_name, None)
            if not callable(method):
                continue
            try:
                result = method()
                if asyncio.iscoroutine(result):
                    loop = getattr(self, "_qq_loop", None)
                    if loop is not None and loop.is_running():
                        asyncio.run_coroutine_threadsafe(result, loop)
                return
            except Exception:
                logger.exception("Failed to stop QQ bot client")
                return
