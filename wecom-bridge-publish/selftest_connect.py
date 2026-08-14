"""联调自检：用真实凭据连长连接并订阅，打印订阅结果后退出。

用于确认：
1. 本机可以连上 wss://openws.work.weixin.qq.com
2. BotID/Secret 正确且管理后台已开启「API 模式 → 长连接」
订阅响应 errcode=0 即成功；非 0 时 errmsg 会说明原因。
注意：订阅有频率保护，仅在需要排查时运行。
"""
import asyncio
import json
import sys
import time

import websockets
import yaml

WS_URL = "wss://openws.work.weixin.qq.com"


async def main() -> None:
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))["wecom"]
    if not cfg.get("bot_id") or not cfg.get("bot_secret"):
        print("config.yaml 缺少 bot_id / bot_secret")
        sys.exit(2)
    print(f"连接 {WS_URL} ...", flush=True)
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "cmd": "aibot_subscribe",
            "headers": {"req_id": "selftest-1"},
            "body": {"bot_id": cfg["bot_id"], "secret": cfg["bot_secret"]},
        }, ensure_ascii=False))
        print("已发送订阅请求，等待响应（12s）...", flush=True)
        deadline = time.time() + 12
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
            except asyncio.TimeoutError:
                break
            msg = json.loads(raw)
            print("RECV:", json.dumps(msg, ensure_ascii=False)[:400], flush=True)
            if msg.get("cmd") == "aibot_subscribe":
                print(">>> 订阅结果 errcode=%s errmsg=%s" % (msg.get("errcode"), msg.get("errmsg")), flush=True)


asyncio.run(main())
print("自检结束", flush=True)
