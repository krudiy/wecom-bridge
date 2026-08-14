# wecom-bridge：企业微信智能机器人 ⇄ DSH Agent 桥接

把**企业微信智能机器人**（官方 **API 模式 · 长连接 / WebSocket**，BotID + Secret）
收到的消息，交给 [DeepSeek Harness (DSH)](https://github.com/deepseek-ai/deepseek-harness)
的 headless agent 处理，再把回答发回企微。全程走 WebSocket 长连接，**无需公网 IP、
无需消息加解密**，内网机器也能直接跑。

```
企微用户 ──私聊消息──▶ 企微智能机器人
                          │  WebSocket 长连接（wss://openws.work.weixin.qq.com）
                          │  aibot_subscribe → aibot_msg_callback / aibot_event_callback
                          ▼
                   bridge.py
                    · 会话历史(SQLite)     · 拼 prompt（含最近 N 轮上下文）
                    · 调 DSH headless      · aibot_respond_msg 回复（透传 req_id）
                          │
                          ▼
              dsh --profile headless "<job>"   （复用本机 DSH 环境：工具/文件系统/subagent）
```

## 特性

- ✅ 官方 WebSocket 长连接协议实现（订阅 / 30s 心跳 / 指数退避重连 / 死连接检测）
- ✅ 与官方 SDK 交叉验证的协议细节（[`wecom-aibot-python-sdk`](https://pypi.org/project/wecom-aibot-python-sdk/)）
- ✅ 每用户多轮上下文（SQLite 环形窗口）
- ✅ 长回复按字节分段（企微 markdown 上限 20480 字节），规避 30 条/分钟频控
- ✅ `enter_chat` 欢迎语、用户白名单、system prompt 保密约束
- ✅ 无公网依赖：长连接模式不需要回调 URL 与 Token/AESKey
- ✅ 自带 `selftest_connect.py` 联调自检

## 快速开始

前置：**Node.js**（跑 DSH，与 `dsh web` 共用 `~/.dsh` 凭据）、**Python 3.10+**。

### 1. 管理后台配置（一次性）

企业微信管理后台 → 智能机器人 → **开启「API 模式」并选择「长连接」**，获取：
- **BotID**：智能机器人唯一标识
- **Secret**：长连接专用密钥（**不同于**回调模式的 Token/EncodingAESKey，注意保管）

### 2. 配置与自检

```bat
启动WeCom桥接.bat        :: 自动建 .venv、装依赖、生成 config.yaml
```

把 BotID/Secret 填入 `config.yaml`（`config.yaml` 已在 .gitignore 中，不会入库），然后：

```bat
.venv\Scripts\python.exe selftest_connect.py
```

订阅响应 `errcode=0` 即连接成功（非 0 时 errmsg 会说明原因）。

### 3. 运行

```bat
启动WeCom桥接.bat
```

在企微里私聊机器人。日志（控制台 + `bridge.log`）会显示：收到消息 → 调 agent →
回复，并打印每条回复的服务端回执（`回执 OK [aibot_respond_msg ...]`）。

本地无企微时可用 mock 模式验证整条链路：

```bat
.venv\Scripts\python.exe bridge.py -c config.yaml -m mock
:: 输入 tester|你好 模拟一条消息
```

## 配置说明（config.yaml.example）

| 字段 | 说明 |
|---|---|
| `wecom.mode` | `longconn`（生产）或 `mock`（本地调试） |
| `wecom.bot_id` / `bot_secret` | 长连接凭据 |
| `wecom.welcome_msg` | 用户当天首次进入会话的欢迎语，留空则不发送 |
| `wecom.allow_users` | 用户 userid 白名单，留空 `[]` 不限制 |
| `dsh.cmd` | dsh 调用前缀；留空自动查找 npx 缓存中已安装的 DSH 入口，可显式指定 |
| `dsh.workdir` | agent 工作目录（文件类工具作用范围，建议独立空目录） |
| `dsh.system_prompt` | 注入 agent 的行为约束（默认含保密约束） |
| `agent.history_turns` | 拼入 prompt 的最近历史轮数 |

## 协议要点

依据官方文档 [智能机器人长连接](https://developer.work.weixin.qq.com/document/path/101463)：

- 连接：`wss://openws.work.weixin.qq.com` → `aibot_subscribe`{bot_id, secret}。
  **一个机器人同一时间只允许一个有效连接**（新连接会踢旧连接）。
- 心跳：每 30s 发应用层 `{"cmd":"ping"}`。**不要启用 websockets 内建 ping/pong**
  （服务端 WS 层 pong 有掩码缺陷，会触发 `1002 incorrect masking` 断连）。
- 收消息：`aibot_msg_callback`（`body.chattype`=single/group、`body.from.userid`、
  `body.msgtype`、`body.text.content`）；事件 `aibot_event_callback`
  （`enter_chat` 可 5s 内回欢迎语）。
- 回复：`aibot_respond_msg` **透传回调的 req_id**；主动推送 `aibot_send_msg`
  （chatid + chat_type，需用户先发过消息）。
- 频控：每会话 30 条/分钟、1000 条/小时（回复 + 主动推送合计）。

## 安全提示

- `config.yaml` 含真实 Secret，已在 `.gitignore`，**请勿提交/截图外发**。
- 建议在管理后台开启用户/群白名单，并保持 `system_prompt` 中的保密约束。
- 若 Secret 曾在不安全渠道出现过，可在管理后台重新生成。

## 目录结构

```
├─ bridge.py           入口：消息分发、历史、调 agent、回复
├─ aibot_client.py     企微长连接 WebSocket 客户端（订阅/心跳/重连/回执）
├─ transport.py        接收层：mock | longconn（含分段回复）
├─ agent_dsh.py        调 DSH headless 子进程（自动找已装 dsh 入口）
├─ session_store.py    每用户最近对话轮次（SQLite）
├─ selftest_connect.py 联调自检：验证凭据与后台长连接配置
├─ config.yaml.example 配置模板（复制为 config.yaml 后填写）
└─ 启动WeCom桥接.bat   Windows 一键启动
```

## 已知限制 / Roadmap

- [ ] 仅处理文本消息；image/voice/file 需下载媒体并解密（AES-256-CBC，aeskey 前 16 字节为 IV）
- [ ] 多用户并发回复未做 per-user 串行化（消息已抛线程处理）
- [ ] 非超管创建者场景下 `from.userid` 为加密 userid，需按官方「自建应用与智能机器人的对接」转换
- [ ] 模板卡片 / 流式消息 / 定时主动推送未接入
- [ ] 看门狗自动重启（Windows 任务计划程序 / supervisor）

## License

[MIT](./LICENSE)
