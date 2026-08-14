"""企业微信智能机器人 · 长连接 WebSocket 客户端（协议实现）。

依据官方文档「智能机器人长连接」（developer.work.weixin.qq.com/document/path/101463）：
- 连接地址: wss://openws.work.weixin.qq.com
- 建连后发送 aibot_subscribe {bot_id, secret} 完成订阅（有频率保护，成功后勿反复发送）
- 每 30 秒发送 ping 心跳；断线自动重连（指数退避）
- 消息回调 aibot_msg_callback / 事件回调 aibot_event_callback
- 回复消息回调用 aibot_respond_msg（透传回调 req_id）；主动推送用 aibot_send_msg

依赖: pip install websockets
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

import websockets

LOG = logging.getLogger(__name__)

WS_URL = "wss://openws.work.weixin.qq.com"
PING_INTERVAL = 30          # 秒，官方建议值
RECONNECT_BASE = 1.0        # 重连退避初始
RECONNECT_MAX = 30.0        # 重连退避上限
DEAD_AFTER = PING_INTERVAL * 2   # 超过该时长未收到任何服务端帧视为死连接


def _ws_is_open(ws) -> bool:
    """兼容 websockets 新旧版本的连接状态判断（同官方 SDK 实现）。"""
    if hasattr(ws, "open"):
        return ws.open          # websockets <= 13.x
    if hasattr(ws, "state"):
        try:
            from websockets.protocol import State
            return ws.state is State.OPEN
        except ImportError:
            return ws.state.name == "OPEN"
    return False


class AibotWsClient:
    """一个智能机器人的长连接客户端。on_msg / on_event 回调在事件循环内调用，
    耗时操作请自行抛到其它线程（见 transport.WeComLongConnTransport）。"""

    def __init__(self, bot_id: str, secret: str,
                 on_msg=None, on_event=None):
        self.bot_id = bot_id
        self.secret = secret
        self.on_msg = on_msg        # fn(body: dict, req_id: str)
        self.on_event = on_event    # fn(body: dict, req_id: str)
        self._ws = None
        self._send_lock = asyncio.Lock()
        self._stop = False
        self._last_frame_ts = 0.0   # 最近一次收到服务端帧的时间
        self._sent = {}             # req_id -> 描述（用于打印发送回执；仅事件循环线程访问）

    # ---------- 工具 ----------
    @staticmethod
    def _new_req_id() -> str:
        return uuid.uuid4().hex

    async def _send(self, cmd: str, body: dict | None = None,
                    req_id: str | None = None) -> None:
        payload = {"cmd": cmd, "headers": {"req_id": req_id or self._new_req_id()}}
        if body is not None:
            payload["body"] = body
        if self._ws is None or not _ws_is_open(self._ws):
            raise ConnectionError("WebSocket 未连接")
        if req_id:
            self._sent[req_id] = f"{cmd} 回复给 req_id={req_id}"
        async with self._send_lock:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))

    # ---------- 连接生命周期 ----------
    async def _subscribe(self) -> None:
        await self._send(
            "aibot_subscribe",
            {"bot_id": self.bot_id, "secret": self.secret},
        )

    async def _heartbeat_loop(self) -> None:
        while not self._stop:
            await asyncio.sleep(PING_INTERVAL)
            try:
                if self._ws is not None and _ws_is_open(self._ws):
                    await self._send("ping")
                    # 死连接检测：若长时间收不到任何服务端帧，主动断开触发重连
                    if self._last_frame_ts and time.time() - self._last_frame_ts > DEAD_AFTER:
                        LOG.warning("超过 %ds 未收到服务端帧，判定死连接，强制重连", DEAD_AFTER)
                        await self._ws.close()
            except Exception:  # noqa: BLE001
                LOG.warning("心跳发送失败，等待重连机制处理")

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            self._last_frame_ts = time.time()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                LOG.warning("非法 JSON 消息: %s", raw[:200])
                continue
            cmd = msg.get("cmd")
            body = msg.get("body") or {}
            req_id = (msg.get("headers") or {}).get("req_id", "")
            if cmd == "aibot_msg_callback" and self.on_msg:
                self.on_msg(body, req_id)
            elif cmd == "aibot_event_callback" and self.on_event:
                self.on_event(body, req_id)
            else:
                errcode = msg.get("errcode")
                if req_id in self._sent:
                    # 我们发出的命令（订阅/心跳/回复）的回执
                    desc = self._sent.pop(req_id)
                    if errcode == 0:
                        LOG.info("回执 OK [%s]", desc)
                    else:
                        LOG.warning("回执失败 [%s] errcode=%s errmsg=%s",
                                    desc, errcode, msg.get("errmsg"))
                elif errcode is not None and errcode != 0:
                    LOG.warning("服务端错误 cmd=%s errcode=%s errmsg=%s",
                                cmd, errcode, msg.get("errmsg"))
                else:
                    LOG.debug("收到 cmd=%s", cmd)

    async def run(self) -> None:
        """连接 + 订阅 + 接收循环；断线后指数退避重连，直到 stop()。"""
        backoff = RECONNECT_BASE
        # 心跳任务在整个生命周期只建一次
        heartbeat = asyncio.create_task(self._heartbeat_loop())
        try:
            while not self._stop:
                try:
                    # 关键：禁用 websockets 内建 ping/pong（默认 20s 一发）。
                    # 企微服务端的 WS 层 pong 帧存在掩码缺陷，会触发
                    # "1002 incorrect masking" 导致连接被关闭（官方 SDK 同样
                    # 设置 ping_interval=None，仅使用应用层 cmd=ping 心跳）。
                    self._ws = await websockets.connect(
                        WS_URL,
                        ping_interval=None,
                        ping_timeout=None,
                        close_timeout=5,
                    )
                    await self._subscribe()
                    LOG.info("已订阅 BotID=%s", self.bot_id)
                    backoff = RECONNECT_BASE
                    await self._recv_loop()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("连接异常: %s；%.0fs 后重连", exc, backoff)
                finally:
                    if self._ws is not None:
                        try:
                            await self._ws.close()
                        except Exception:  # noqa: BLE001
                            pass
                        self._ws = None
                if self._stop:
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX)
        finally:
            heartbeat.cancel()

    def stop(self) -> None:
        self._stop = True

    # ---------- 发送命令 ----------
    async def respond_welcome(self, req_id: str, text: str) -> None:
        """回复进入会话事件（enter_chat），5 秒内有效。"""
        await self._send(
            "aibot_respond_welcome_msg",
            {"msgtype": "text", "text": {"content": text}},
            req_id=req_id,
        )

    async def respond_msg(self, req_id: str, body: dict) -> None:
        """回复消息回调（透传回调中的 req_id）。body: {"msgtype": ..., ...}"""
        await self._send("aibot_respond_msg", body, req_id=req_id)

    async def send_msg(self, chatid: str, body: dict, chat_type: int = 1) -> None:
        """主动推送消息（chat_type: 1=单聊 2=群聊；需用户先发过消息）。"""
        await self._send(
            "aibot_send_msg",
            {"chatid": chatid, "chat_type": chat_type, **body},
        )
