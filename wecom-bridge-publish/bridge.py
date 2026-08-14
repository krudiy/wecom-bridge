"""企业微信智能机器人 → DSH agent 桥接服务。

消息流：
  企微长连接(或回调)收到消息 → transport.on_message → Bridge.handle
  → SessionStore 记录历史 → DshAgent.ask(拼好上下文的 prompt) → WeComSender 分段回复

先跑 mock 模式（不需要任何企微凭据）：
  python bridge.py -c config.yaml        # config.yaml 里 wecom.mode = mock
  然后在控制台输入：  user_id|消息内容
"""
from __future__ import annotations

import argparse
import logging
import signal

import yaml

from agent_dsh import DshAgent
from session_store import SessionStore
from transport import create_transport

LOG = logging.getLogger("bridge")


class Bridge:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.store = SessionStore(cfg["session_db"])
        self.agent = DshAgent(cfg["dsh"])
        self.history_turns = int(cfg["agent"]["history_turns"])
        self.allow_users = set(cfg.get("wecom", {}).get("allow_users", []) or [])
        self.transport = create_transport(cfg["wecom"], on_message=self.handle)

    # ---- 消息入口（transport 回调）----
    def handle(self, user_id: str, text: str) -> None:
        if self.allow_users and user_id not in self.allow_users:
            LOG.info("忽略未授权用户 %s", user_id)
            return
        text = self._clean_text(text)
        LOG.info("[%s] 收到: %s", user_id, text[:200])
        prompt = self._build_prompt(user_id, text)
        try:
            reply = self.agent.ask(user_id, prompt)
        except Exception as exc:  # noqa: BLE001
            LOG.exception("agent 调用失败")
            self.transport.send_text(user_id, f"抱歉，处理出错了：{exc}")
            return
        reply = self._clean_text(reply)
        self.store.push(user_id, "user", text)
        self.store.push(user_id, "assistant", reply)
        self.transport.send_text(user_id, reply)

    @staticmethod
    def _clean_text(s: str) -> str:
        """去掉孤立代理项等非法字符，保证任何来源的消息都能写库/发送。"""
        try:
            return s.encode("utf-8", "replace").decode("utf-8")
        except Exception:  # noqa: BLE001
            return s.encode("utf-8", "ignore").decode("utf-8", "ignore")

    def _build_prompt(self, user_id: str, text: str) -> str:
        turns = self.store.recent(user_id, self.history_turns)
        lines = ["以下是该用户与助手最近的对话（新消息在前，由 you 标记）："]
        for role, content in turns:
            tag = "用户" if role == "user" else "助手"
            lines.append(f"{tag}: {content}")
        lines.append(f"用户（user_id={user_id}）: {text}")
        lines.append("请直接给出对最新这条用户消息的回答。")
        return "\n".join(lines)

    def run(self) -> None:
        LOG.info("桥接服务启动，模式=%s", self.cfg["wecom"]["mode"])
        self.transport.start()


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("-m", "--mode", default=None,
                    help="临时覆盖 wecom.mode（mock | longconn），不改配置文件")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.mode:
        cfg["wecom"]["mode"] = args.mode
    log_level = getattr(logging, cfg.get("log_level", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("bridge.log", encoding="utf-8"),
        ],
    )

    bridge = Bridge(cfg)

    def _stop(*_):
        LOG.info("收到停止信号…")
        bridge.transport.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        bridge.run()
    except KeyboardInterrupt:
        pass
    finally:
        LOG.info("桥接服务退出")


if __name__ == "__main__":
    main()
