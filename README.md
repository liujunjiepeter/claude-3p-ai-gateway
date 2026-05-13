# AI Gateway

> 通用模型网关：让 Claude 桌面应用接入**任意 OpenAI 兼容模型**（GPT-4o、Gemini、Qwen、DeepSeek、MiMo 等），支持流式传输、工具调用、多模态。

## 这是什么？

Claude 桌面应用（包括 Claude-3p）默认只能使用 Anthropic 自家的模型。本项目通过两层代理，让你在 Claude 界面右下角自由切换**任何 OpenAI 兼容模型**，**无需重启**，并完整保留流式对话、工具调用、图片理解等能力。

```
┌──────────────────────────────────────────────────────────┐
│  Claude-3p 桌面应用                                        │
│  ┌────────────────────────────────────────────────────┐  │
│  │  模型选择:  [gpt-4o ▼] [gemini-flash ▼] [qwen ▼]   │  │
│  └────────────────────────────────────────────────────┘  │
│  用户: 列出当前目录的文件                                    │
│  Claude: [执行 bash ls] → proxy.py docker-compose.yml     │
└──────────────────────┬───────────────────────────────────┘
                       │  Anthropic Messages API
                       ▼
┌──────────────────────────────────────────────────────────┐
│  anth-openai-proxy (port 9999)                            │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Anthropic ↔ OpenAI 协议翻译                         │  │
│  │  · 图片: base64 → image_url                         │  │
│  │  · 工具: tool_use ↔ tool_calls                      │  │
│  │  · 推理: thinking ↔ reasoning_content               │  │
│  │  · 流式: SSE 事件顺序严格匹配 Anthropic 规范            │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────┬───────────────────────────────────┘
                       │  OpenAI Chat Completions API
                       ▼
┌──────────────────────────────────────────────────────────┐
│  One-API (port 3000)                                      │
│  ┌────────────────────────────────────────────────────┐  │
│  │  模型网关                                             │  │
│  │  · 渠道管理: 添加任意 OpenAI 兼容供应商                  │  │
│  │  · 令牌系统: 统一 API Key 鉴权                         │  │
│  │  · 额度控制: 使用量统计 / 限流 / 负载均衡                 │  │
│  └────────────────────────────────────────────────────┘  │
└──────┬──────────┬──────────┬──────────┬──────────────────┘
       │          │          │          │
       ▼          ▼          ▼          ▼
   OpenAI     Google     Alibaba    DeepSeek   ...任意 OpenAI
   GPT-4o     Gemini     Qwen       V4        兼容模型
```

## 功能特性

### 协议翻译覆盖

proxy.py 完整实现了 Anthropic Messages API 与 OpenAI Chat Completions API 的双向翻译：

**请求方向（Anthropic → OpenAI）**
- 文本消息：保持原样透传
- 图片块：`{type: "image", source: {...}}` → `{type: "image_url", image_url: {...}}`
- 工具定义：Anthropic `tools` 数组 → OpenAI `functions` 数组
- 工具选择：`tool_choice: {type: "any"}` → `tool_choice: "auto"`
- 推理内容保留：thinking 块 → `reasoning_content` 字段
- 工具结果：`tool_result` → OpenAI `role: "tool"` 消息
- 空结果防护：空工具结果自动填充占位文本（避免 API 400）
- 系统提示词：Anthropic `system` 参数 → OpenAI `role: "system"` 消息

**响应方向（OpenAI → Anthropic）**
- 文本内容：`content` → `{type: "text", text: ...}`
- 推理内容：`reasoning_content` → `{type: "thinking", thinking: ...}`
- 工具调用：`tool_calls` → `{type: "tool_use", id: ..., name: ..., input: ...}`
- 流式事件顺序：严格匹配 Anthropic 规范

**流式 SSE 事件序列**

```
message_start          ← 包含 input_tokens 估算
content_block_start    ← thinking 块（推理模型）
ping                   ← 首块后立即发送
content_block_delta    ← thinking_delta × N
content_block_stop     ← 关闭 thinking
content_block_start    ← text 块（或 tool_use 块）
content_block_delta    ← text_delta × N（或 input_json_delta）
content_block_stop     ← 关闭
message_delta          ← stop_reason + usage
message_stop
```

### 健壮性

- **JSON 错误处理**：请求体解析失败返回 400 而非崩溃
- **空 choices 防护**：流式解析遇到空 choices 跳过而非 IndexError
- **孤立工具降级**：对话历史中不完整的 tool_calls 自动降级为纯文本
- **多线程并发**：ThreadingMixIn 处理并发请求
- **超时控制**：代理 → One-API 120s，透传请求 30s
- **错误流关闭**：流式异常时发送完整结束事件序列

## 快速开始

### 平台支持

| 平台 | Docker | setup 脚本 | Claude-3p 路径 |
|------|:---:|:---:|------|
| macOS (Apple Silicon) | ✅ | `bash setup.sh` | `~/Library/Application Support/Claude-3p` |
| macOS (Intel) | ✅ | `bash setup.sh` | 同上 |
| Windows (x64) | ✅ | `.\setup.ps1` | `%APPDATA%\Claude-3p` |
| Windows (ARM) | ✅ | `.\setup.ps1` | 同上 |
| Linux | ✅ | `bash setup.sh` | 取决于 Claude-3p 构建 |

### 前提条件

- Docker Desktop
- 至少一个 OpenAI 兼容模型的 API Key（如 [DeepSeek](https://platform.deepseek.com)、[OpenAI](https://platform.openai.com)、[Groq](https://console.groq.com) 等）
- Claude-3p 桌面应用

### 1. 克隆项目

```bash
git clone https://github.com/your-username/ai-gateway.git
cd ai-gateway
```

### 2. 配置

```bash
cp .env.example .env
```

编辑 `.env`，填入你的 API Key 和模型列表：

```bash
# 代理暴露的模型列表（JSON 数组）
MODELS=[{"id":"deepseek-v4-pro","type":"model","display_name":"DeepSeek V4 Pro"},{"id":"mimo-v2.5","type":"model","display_name":"MiMo V2.5"}]

# 渠道 API Key（setup.sh 会用到）
DEEPSEEK_API_KEY=sk-your-key-here
XIAOMI_API_KEY=tp-your-key-here
```

### 3. 启动服务

```bash
docker-compose up -d
```

首次启动自动拉取镜像。10 秒后验证：

```bash
curl http://localhost:9999/v1/models
# 返回你在 MODELS 中配置的模型列表
```

### 4. 配置渠道和令牌

**方式 A：Web 界面**

打开 `http://localhost:3000`，默认账号 `root` / `123456`：
1. 「渠道」→ 添加：类型选 OpenAI，填 API Key 和地址
2. 「令牌」→ 添加，勾选「无限额度」
3. 将令牌填入 `.env` 的 `ONEAPI_TOKEN`

**方式 B：命令行**

```bash
# macOS / Linux
source .env && bash setup.sh

# Windows PowerShell
.\.env; .\setup.ps1
```

然后将输出的令牌填入 `.env`，重启代理：

```bash
docker-compose restart proxy
```

### 5. 配置 Claude-3p

编辑 `claude-3p-config.json`，将 `inferenceModels` 改为与 `.env` 中 `MODELS` 一致的模型列表，然后安装：

```bash
# macOS
CONFIG_DIR="$HOME/Library/Application Support/Claude-3p/configLibrary"
UUID=$(uuidgen | tr '[:upper:]' '[:lower:]')
cp claude-3p-config.json "$CONFIG_DIR/$UUID.json"

# Windows PowerShell
$CONFIG_DIR="$env:APPDATA\Claude-3p\configLibrary"
$UUID = [guid]::NewGuid().ToString()
Copy-Item claude-3p-config.json "$CONFIG_DIR\$UUID.json"
```

编辑 `$CONFIG_DIR/_meta.json`，添加条目并设置 `appliedId`。

### 6. 重启 Claude-3p

右下角即可选择你配置的所有模型，随时切换。

## 添加更多模型

本项目不限制模型供应商。三步即可接入任何 OpenAI 兼容 API：

**1. One-API 添加渠道**

在 `http://localhost:3000` 后台添加新渠道：

| 供应商 | 类型 | 地址示例 |
|--------|------|----------|
| OpenAI | OpenAI | `https://api.openai.com` |
| DeepSeek | OpenAI | `https://api.deepseek.com` |
| Groq | OpenAI | `https://api.groq.com/openai` |
| Together | OpenAI | `https://api.together.xyz` |
| 阿里 Qwen | OpenAI | `https://dashscope.aliyuncs.com/compatible-mode` |
| 小米 MiMo | OpenAI | `https://token-plan-cn.xiaomimimo.com` |
| 硅基流动 | OpenAI | `https://api.siliconflow.cn` |
| 任意兼容 /v1/chat/completions 的端点 | OpenAI | 你的地址 |

**2. 更新模型列表**

编辑 `.env` 的 `MODELS` 变量，追加新模型：

```bash
MODELS=[{"id":"deepseek-v4-pro","type":"model","display_name":"DeepSeek V4 Pro"},{"id":"gpt-4o","type":"model","display_name":"GPT-4o"},{"id":"gemini-2.0-flash","type":"model","display_name":"Gemini Flash"}]
```

**3. 同步 Claude-3p 配置**

编辑 `claude-3p-config.json` 的 `inferenceModels` 添加对应条目。

**4. 重启**

```bash
docker-compose restart proxy
# 重启 Claude-3p
```

## 配置参考

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MODELS` | 代理暴露的模型列表（JSON） | DeepSeek + MiMo |
| `ONEAPI_PORT` | One-API Web 端口 | 3000 |
| `PROXY_PORT` | 协议代理端口 | 9999 |
| `DEEPSEEK_API_KEY` | DeepSeek Key（setup.sh 用） | - |
| `DEEPSEEK_MODEL` | DeepSeek 模型名 | deepseek-v4-pro |
| `XIAOMI_API_KEY` | 小米 Key（setup.sh 用） | - |
| `XIAOMI_MODEL` | 小米模型名 | mimo-v2.5 |
| `XIAOMI_BASE_URL` | 小米 API 地址 | https://token-plan-cn.xiaomimimo.com |
| `ONEAPI_TOKEN` | One-API 令牌 | - |

### Docker Compose 服务

| 服务 | 端口 | 镜像 | 说明 |
|------|------|------|------|
| `one-api` | 3000 | justsong/one-api | 模型网关 |
| `proxy` | 9999 | python:3.13-alpine | Anthropic ↔ OpenAI 翻译 |

## 故障排查

### 模型不回复

```bash
docker-compose ps                                    # 检查服务状态
curl http://localhost:9999/v1/models                # 测试代理
docker logs anth-openai-proxy --tail 30             # 代理日志
docker logs one-api --tail 30                       # 网关日志
```

### 工具调用不执行

```bash
docker logs anth-openai-proxy 2>&1 | grep "降级"    # 查看是否降级
```

有降级日志 = 对话历史中有不完整的工具调用链，**开新对话**即可。

### 响应很慢

```bash
time curl ... http://localhost:9999/v1/messages     # 完整路径延迟
time curl ... http://localhost:3000/v1/chat/completions  # One-API → 模型延迟
```

推理模型首字延迟 2-5 秒正常。非推理模型应在 1 秒内。

### 多轮工具调用报 400

某些推理模型（如 DeepSeek）要求后续请求保留 thinking 内容。proxy.py 已自动处理。如仍报错，尝试开新对话。

### 对话太长超时

累积数百条消息后建议开新对话。代理超时 120 秒。

## 项目结构

```
ai-gateway/
├── .env.example          # 配置模板
├── .gitignore            # 排除 .env 和 data/
├── README.md             # 本文档
├── claude-3p-config.json # Claude-3p 配置模板
├── docker-compose.yml    # 服务编排
├── proxy.py              # 协议翻译代理
├── setup.sh              # macOS/Linux 自动配置
└── setup.ps1             # Windows 自动配置
```

## 工作原理

### 为什么需要两层？

Claude 桌面应用使用 Anthropic Messages API 协议，而绝大多数第三方模型使用 OpenAI Chat Completions API。两者**消息格式、工具定义、流式事件、内容块类型**完全不同：

| | Anthropic | OpenAI |
|---|---|---|
| 端点 | `POST /v1/messages` | `POST /v1/chat/completions` |
| 文本 | `[{type:"text", text}]` | `"string"` |
| 工具 | `{type:"tool_use", id, name, input}` | `tool_calls: [{function: {name, arguments}}]` |
| 图片 | `{type:"image", source: {data, media_type}}` | `{type:"image_url", image_url: {url}}` |
| 推理 | `{type:"thinking", thinking}` | `reasoning_content` |
| 流式 | `event: content_block_delta` | `data: {"choices":[{"delta":...}]}` |

- **proxy.py** 专注协议翻译，轻量独立
- **One-API** 专注供应商管理、鉴权、额度

### 流式状态机

```
         ┌──────────┐
         │  None    │
         └────┬─────┘
              │ reasoning_content
         ┌────▼─────┐
         │ thinking │ → content_block_start(thinking) → delta × N
         └────┬─────┘
              │ content
         ┌────▼─────┐
         │   text   │ → content_block_stop(thinking) → start(text) → delta × N
         └────┬─────┘
              │ tool_calls
         ┌────▼──────┐
         │ tool_use  │ → start(tool_use) → input_json_delta × N
         └────┬──────┘
              │ finish_reason
              ▼
         content_block_stop → message_delta → message_stop
```

## License

GPL 3.0 — 免费使用，衍生作品必须开源。
