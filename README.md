# wecom-bridge 使用说明（面向使用者）

把企业微信智能机器人接入 **DeepSeek Harness (DSH)** 的完整上手教程。
照着做，从零到能用大约 10 分钟。

---

## 0. 它是什么

```
你在企微里发消息 → 企微智能机器人(长连接) → 本程序 → DSH agent 思考/调用工具 → 回复发回企微
```

- 不需要公网 IP、不需要配置回调地址和加解密
- 回答由本机 DSH agent 生成（和 DeepSeek 官方 API 共用凭据，也可接其他模型）

---

## 1. 你需要准备什么

### 1.1 企业微信侧（一次性）

1. 企业微信管理后台 → 找到「智能机器人」（应用管理 / 工作台里）
2. 进入机器人配置页 → **开启「API 模式」并选择「长连接」**
3. 拿到两个凭证：
   - **BotID**：机器人唯一标识
   - **Secret**：长连接专用密钥（注意：它**不是**回调模式的 Token/EncodingAESKey）

### 1.2 电脑环境

| 依赖 | 说明 | 检查命令 |
|---|---|---|
| Node.js 18+ | 跑 DSH | `node -v` |
| Python 3.10+ | 跑桥接程序 | `python --version` |
| DSH + LLM 凭据 | 见第 2 节 | `npx -y @deepseek-ai/dsh --version` |

Windows / macOS / Linux 均可（本说明以 Windows 为例）。

---

## 2. 配置 DeepSeek Harness（关键一步）

本程序本身不调用大模型，**agent 全部由 DSH 执行**，所以必须先让 DSH 能跑。

### 2.1 确认/安装 DSH

```bash
npx -y @deepseek-ai/dsh --version
```

首次运行会自动下载。能输出版本号即 OK。

### 2.2 配置大模型凭据（二选一）

- **方式 A（推荐）**：运行 `npx -y @deepseek-ai/dsh web`，浏览器打开
  `http://127.0.0.1:3080`，在 **Models** 页面填入你的 API Key
  （DeepSeek 官方 key 在 platform.deepseek.com 获取），保存后关掉即可。
- **方式 B**：参照 DSH 文档手动配置 `~/.dsh/.credentials.yaml`。

> 凭据存在 `~/.dsh/` 下，桥接程序与 Web 界面**共用同一份**，
> 配一次即可。不需要一直开着 Web 界面。

### 2.3 验证 DSH 可用

```bash
npx -y @deepseek-ai/dsh --profile headless "只回复两个字：OK"
```

输出 `OK` 即成功（首次会初始化 headless profile，稍慢属正常）。

---

## 3. 安装桥接程序

解压/克隆本目录后：

**方式 1（Windows 一键）**：双击 `启动WeCom桥接.bat`
（自动创建 `.venv`、安装依赖、生成 `config.yaml`）。

**方式 2（手动，通用）**：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp config.yaml.example config.yaml   # Windows: copy config.yaml.example config.yaml
```

---

## 4. 填写配置

编辑 `config.yaml`：

```yaml
wecom:
  bot_id: "填你的 BotID"
  bot_secret: "填你的 Secret"
  welcome_msg: "你好，我是 AI 助手，有什么可以帮你的吗？"   # 可改或留空
  allow_users: []        # 白名单：只允许列出的 userid，如 ["zhangsan"]；留空=不限制
dsh:
  system_prompt: |       # 可自定义 agent 人设/约束，默认已含"不泄露内部信息"
    ...
agent:
  history_turns: 10      # 多轮上下文轮数
```

> `config.yaml` 包含真实 Secret，**不要发给别人、不要传 GitHub**。

---

## 5. 自检连接（30 秒）

```bash
.venv\Scripts\python.exe selftest_connect.py
```

看到 `errcode=0` 即连接成功：
- 非 0：按第 7 节排查（多半是后台没开长连接，或 Secret 填错）

---

## 6. 启动并使用

```bash
# Windows
启动WeCom桥接.bat
# 或手动
.venv\Scripts\python.exe bridge.py -c config.yaml
```

然后在**企业微信里私聊你的机器人**，发消息即可。

- 日志实时打印在控制台，同时写入 `bridge.log`
- 看到 `回执 OK [aibot_respond_msg ...]` = 回复已送达
- 没有企微也想先试？加参数跑 mock 模式：
  ```bash
  .venv\Scripts\python.exe bridge.py -c config.yaml -m mock
  # 输入 tester|你好 模拟一条消息
  ```

---

## 7. 常见问题排查

| 现象 | 原因 | 解决 |
|---|---|---|
| 订阅返回 errcode≠0 | 后台未开启「API 模式-长连接」，或 BotID/Secret 填错 | 回管理后台核对；Secret 必须是长连接专用那个 |
| 企微发了消息没反应 | 服务没在跑 / 连接断了 / 消息类型非文本 | 看 `bridge.log`；确认进程存活；当前仅支持文本消息 |
| 日志反复出现重连 | 网络不稳定，或**另一个程序也在用同一机器人连接**（一个机器人同时只允许一条连接） | 关掉其他测试程序/官方 SDK 再试 |
| 回复特别长被分段 | 企微单条消息有长度限制 | 正常，程序自动按 18000 字节分段（`reply_chunk_bytes` 可调） |
| 发图片/语音没回复 | 当前版本仅处理文本 | Roadmap 中；可自行扩展 `transport.py` |
| userid 是一串密文 | 机器人创建者不是企业超管时 userid 会加密 | 按官方「自建应用与智能机器人的对接」转换 |
| Windows 控制台中文乱码 | 终端编码问题 | 不影响功能；日志文件 `bridge.log` 是 UTF-8，可正常阅读 |
| 第一条消息特别慢 | 首次要初始化 headless profile / 下载 DSH | 之后每次约 5~15 秒属正常（agent 完整思考+工具调用） |

---

## 8. 安全提醒

- `config.yaml`（含 Secret）**绝不外发、不入库**（.gitignore 已排除）
- 生产使用建议配置 `allow_users` 白名单
- 默认 system prompt 已要求 agent 不透露系统提示词、文件路径等内部信息，可自行调整
- Secret 泄露时可回管理后台重新生成

---

## 9. 目录速查

```
├─ bridge.py            主程序：收消息 → 调 DSH → 回复
├─ aibot_client.py      企微长连接协议（订阅/心跳/重连）
├─ transport.py         接收层：mock | longconn
├─ agent_dsh.py         DSH 调用
├─ session_store.py     会话历史（SQLite）
├─ selftest_connect.py  连接自检
├─ config.yaml.example  配置模板
└─ 启动WeCom桥接.bat    Windows 一键启动
```

有问题先看 `bridge.log`，日志里每一步都有记录。
