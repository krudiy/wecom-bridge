"""接收层：mock / 企微智能机器人长连接（WebSocket）。

create_transport() 按 config.wecom.mode 选择实现。

长连接实现基于官方文档「智能机器人长连接」，协议细节见 aibot_client.py。
收到消息回调后：解析发送者 userid 与文本 → 记录 pending 回复上下文（req_id）
→ 调 on_message；回复时用 aibot_respond_msg 透传 req_id（或主动推送 aibot_send_msg）。
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from abc import ABC, abstractmethod

from aibot_client import AibotWsClient

LOG = logging.getLogger(__name__)

# markdown content 上限 20480 字节，留余量
MAX_REPLY_BYTES = 18000
# 回复 + 主动推送合计 30 条/分钟、1000 条/小时（官方频控），分段间留间隔
CHUNK_SLEEP = 1.0


class Transport(ABC):
    def __init__(self, cfg: dict, on_message):
        self.cfg = cfg
        # fn(user_id: str, text: str) —— 桥接层统一消息入口
        self.on_message = on_message

    @abstractmethod
    def start(self) -> None: ...

    def stop(self) -> None:
        pass

    @abstractmethod
    def send_text(self, user_id: str, text: str) -> None: ...


class MockTransport(Transport):
    """本地调试：控制台输入  user_id|消息  模拟一条企微消息（空行退出）。"""

    def start(self) -> None:
        print("mock 模式：输入  user_id|消息内容  模拟一条企微消息（空行退出）")
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                break
            if not line:
                break
            if "|" in line:
                user_id, text = line.split("|", 1)
            else:
                user_id, text = "tester", line
            self.on_message(user_id, text)

    def send_text(self, user_id: str, text: str) -> None:
        for chunk in self._chunk(text):
            LOG.info("[mock 发送 → %s] %s", user_id, chunk)
            time.sleep(0.2)

    @staticmethod
    def _chunk(text: str, max_bytes: int = MAX_REPLY_BYTES) -> list[str]:
        return _chunk_by_bytes(text, max_bytes)


class WeComLongConnTransport(Transport):
    """企业微信智能机器人·长连接（WebSocket）模式。"""

    def __init__(self, cfg: dict, on_message):
        super().__init__(cfg, on_message)
        self.bot_id = cfg.get("bot_id", "")
        self.secret = cfg.get("bot_secret", "")
        self.welcome_msg = cfg.get("welcome_msg", "")
        self._client: AibotWsClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pending: dict[str, dict] = {}   # user_id -> {req_id, ts}
        self._pending_lock = threading.Lock()

    # ---------- 接收 ----------
    def _on_msg(self, body: dict, req_id: str) -> None:
        from_user = (body.get("from") or {}).get("userid", "")
        if not from_user:
            LOG.warning("消息回调缺少 from.userid: %s", body)
            return
        chattype = body.get("chattype", "single")
        msgtype = body.get("msgtype", "")
        # MVP 仅处理文本；image/voice/file 等需下载媒体并解密（见 README TODO）
        if msgtype != "text":
            LOG.info("忽略非文本消息 msgtype=%s（from=%s）", msgtype, from_user)
            return
        text = (body.get("text") or {}).get("content", "")
        with self._pending_lock:
            self._pending[from_user] = {"req_id": req_id, "ts": time.time()}
        if chattype == "group":
            # 群里 @机器人 的消息 content 带 "@机器人名 " 前缀，去掉
            text = re.sub(r"^@[^\s]+\s*", "", text).strip()
        LOG.info("[长连接][%s] %s", from_user, text[:200])
        # agent 处理耗时，不能阻塞事件循环：抛到独立线程
        threading.Thread(target=self.on_message, args=(from_user, text), daemon=True).start()

    def _on_event(self, body: dict, req_id: str) -> None:
        event = body.get("event") or {}
        eventtype = event.get("eventtype", "")
        from_user = (body.get("from") or {}).get("userid", "")
        if eventtype == "enter_chat":
            LOG.info("用户 %s 首次进入会话", from_user)
            if self.welcome_msg and from_user and self._client:
                asyncio.run_coroutine_threadsafe(
                    self._client.respond_welcome(req_id, self.welcome_msg), self._loop
                )

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if not self.bot_id or not self.secret:
            raise ValueError("longconn 模式需要配置 wecom.bot_id 和 wecom.bot_secret")
        self._loop = asyncio.new_event_loop()

        def _runner() -> None:
            asyncio.set_event_loop(self._loop)
            self._client = AibotWsClient(
                self.bot_id, self.secret,
                on_msg=self._on_msg, on_event=self._on_event,
            )
            try:
                self._loop.run_until_complete(self._client.run())
            finally:
                self._loop.close()

        self._thread = threading.Thread(target=_runner, daemon=True, name="aibot-ws")
        self._thread.start()
        LOG.info("长连接线程已启动（BotID=%s）", self.bot_id)
        # 主线程保持存活，等 Ctrl+C
        try:
            while self._thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self) -> None:
        if self._client:
            self._client.stop()
        if self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception:  # noqa: BLE001
                pass

    # ---------- 发送 ----------
    def send_text(self, user_id: str, text: str) -> None:
        if self._client is None or self._loop is None:
            LOG.error("长连接未就绪，丢弃回复")
            return
        chunks = _chunk_by_bytes(text)
        for i, chunk in enumerate(chunks):
            self._send_one(user_id, chunk)
            if i < len(chunks) - 1:
                time.sleep(CHUNK_SLEEP)

    def _send_one(self, user_id: str, text: str) -> None:
        with self._pending_lock:
            ctx = self._pending.get(user_id)
        body = {"msgtype": "markdown", "markdown": {"content": text}}
        try:
            if ctx:
                # 有消息回调上下文：用 aibot_respond_msg 透传 req_id（24h 内有效）
                fut = asyncio.run_coroutine_threadsafe(
                    self._client.respond_msg(ctx["req_id"], body), self._loop)
            else:
                # 无回调上下文（如定时推送）：主动推送（需用户先发过消息）
                fut = asyncio.run_coroutine_threadsafe(
                    self._client.send_msg(user_id, body, chat_type=1), self._loop)
            fut.result(timeout=10)
        except Exception as exc:  # noqa: BLE001
            LOG.error("回复 %s 失败: %s", user_id, exc)


def _chunk_by_bytes(text: str, max_bytes: int = MAX_REPLY_BYTES) -> list[str]:
    """按 UTF-8 字节数分段（企微 markdown 上限 20480 字节）。"""
    text = text.strip()
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    parts: list[str] = []
    cur = ""
    for para in text.split("\n"):
        candidate = f"{cur}\n{para}" if cur else para
        if candidate.encode("utf-8") > max_bytes and cur:
            parts.append(cur)
            cur = para
        else:
            cur = candidate
        while len(cur.encode("utf-8")) > max_bytes:  # 超长单段硬切
            cut = max_bytes // 4
            while cut < len(cur) and len(cur[:cut].encode("utf-8")) <= max_bytes:
                cut += 1
            parts.append(cur[: cut - 1])
            cur = cur[cut - 1:]
    if cur:
        parts.append(cur)
    return parts


_TRANSPORTS = {
    "mock": MockTransport,
    "longconn": WeComLongConnTransport,
}


def create_transport(cfg: dict, on_message):
    mode = cfg.get("mode", "mock")
    cls = _TRANSPORTS.get(mode)
    if cls is None:
        raise ValueError(f"未知 mode: {mode}（可选 {list(_TRANSPORTS)}）")
    return cls(cfg, on_message)
