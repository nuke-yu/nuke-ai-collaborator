# Nuke AI Collaborator

一个基于 Web 的 AI 协作工作区，支持多群组聊天、AI Bot 成员、实时 WebSocket 通信，类 Slack / 微信群的交互体验。

---

## Tech Stack

| 层 | 技术 |
|---|---|
| 后端 | Python · FastAPI · aiosqlite · SQLite |
| 前端 | React 19 · Vite · Tailwind CSS v4 |
| 实时通信 | WebSocket |
| AI 接入 | DeepSeek · OpenAI · Anthropic Claude · Ollama |

---

## Features

### 💬 消息

- **Markdown 渲染** — 支持标题、列表、表格、引用、行内代码
- **代码块** — 折叠/展开、语言高亮（Prism）、一键复制
- **文件 / 图片上传** — 点击上传、拖拽上传、粘贴图片直接发送
- **图片预览** — 聊天内缩略图展示，点击放大（Lightbox，Esc 关闭）
- **消息回复** — 引用回复，显示被回复内容摘要
- **消息编辑** — 原地编辑，显示「已编辑」标记
- **消息撤回** — 软删除，显示「此消息已撤回」
- **消息置顶** — 顶部 Pin 栏，支持多条，实时同步
- **消息草稿** — 切换群组时草稿自动保存，回来后恢复
- **@ 提及** — 输入 `@` 弹出成员选择器，支持 `@all`
- **消息搜索** — 关键词高亮，点击结果跳转并高亮定位
- **时间分组** — 日期分隔线（今天 / 昨天 / 具体日期）
- **已读回执** — 消息下方显示哪些成员已读
- **消息反应** — 快捷 Emoji 栏 + 完整 Emoji 选择器（6 大分类）

### 🤖 AI Bot

- **多模型支持** — DeepSeek / OpenAI / Anthropic Claude / Ollama（本地）
- **流式输出** — 打字机效果，逐字实时显示
- **自定义角色** — 每个 Bot 可设置系统提示词、角色描述
- **角色模板** — 内置模板库，一键添加常用 Bot 角色
- **上下文记忆** — 每个群组维护独立对话历史，群组间完全隔离

### 👥 成员

- **在线状态** — 实时绿点指示，连接即在线，断开即离线（多标签安全处理）
- **自动回复** — 离线时被 @ 自动触发回复，可自定义回复内容
- **成员管理** — 添加 / 移除成员，群组成员数实时同步

### 🗂 群组

- **多群组** — 左侧列表，支持同时展开多个群组的成员子列表
- **群组公告** — 顶部公告栏，折叠/展开，支持在线编辑，实时同步
- **空状态引导** — 新群组显示成员头像和「发送消息开始对话」引导
- **未读角标** — 群组旁显示未读消息数
- **群组重命名** — 点击群组名称直接编辑

### 🎨 界面

- **深色 / 浅色主题** — 一键切换，Tailwind CSS 变量级反转，150ms 平滑过渡，偏好持久化
- **移动端适配** — 底部 Tab 导航（群组 / 聊天），响应式布局
- **无闪烁切群** — 本地消息缓存，切换群组即时显示，后台静默刷新
- **滚动翻页** — 上滑加载更早消息，保持滚动位置
- **键盘快捷键** — `⌘K` / `Ctrl+K` 打开搜索
- **打字状态** — Bot 思考时显示三点跳动动画
- **拖拽上传** — 拖文件到聊天区，蓝色虚线框提示
- **完整 Emoji 选择器** — 6 个分类，支持插入消息或用作 Reaction

### 📁 文件支持

| 类型 | 格式 |
|---|---|
| 图片 | JPEG · PNG · GIF · WebP · SVG |
| 文档 | PDF · Word (.doc / .docx) · Excel (.xls / .xlsx) |
| 文本 | TXT · JSON |

最大单文件 **10 MB**，图片内嵌显示，其他文件显示下载卡片。

### ⏰ 定时任务（Scheduler）

- **Cron 式调度** — 标准 5 段 cron 表达式（`0 9 * * 1-5`），由 APScheduler 驱动，运行在同一进程 asyncio 循环
- **插件式解耦** — 独立 `scheduler/` 模块，仅 `runner.py` 与主系统耦合；整体删除只需去掉 `main.py` 中 3 行代码
- **REST 管理 API** — CRUD + toggle + 立即执行（`POST /api/cron-jobs/{id}/run`）
- **持久化** — cron 规则存储于 `cron_jobs` 表，重启自动恢复

```
POST   /api/cron-jobs              # 创建定时任务
GET    /api/cron-jobs              # 列表（支持 bot_id / group_id 过滤）
PUT    /api/cron-jobs/{id}         # 修改
DELETE /api/cron-jobs/{id}         # 删除
POST   /api/cron-jobs/{id}/toggle  # 启用 / 禁用
POST   /api/cron-jobs/{id}/run     # 立即触发一次
```

### ⚙️ 管理

- **API Key 管理** — 界面化配置各 AI 提供商的 Key，保存在本地 `app_config.json`
- **数据导出** — 导出聊天记录为 Markdown 或 JSON，含发言人、时间戳、回复关系
- **使用统计** — 各成员发言数量统计，横向进度条可视化

### 📊 Token 统计（完整覆盖）

每条 AI 消息均记录四类 token —— `input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_creation_tokens`，覆盖所有 AI 调用路径：

| 路径 | 文件 | 说明 |
|------|------|------|
| 流式主回复 | `core/orchestrator.py` · `stream_bot_response` | `usage_out` → `save_message` |
| 竞速路径 | `core/orchestrator.py` · `call_bot` | `call_ai_once` 返回值 → `save_message` |
| 工作流链式阶段 | `core/workflow.py` · `_trigger_single_stage` | `usage_out` → `update_message` |
| 工作流池式阶段 | `core/workflow.py` · `_trigger_pool_bot` | `usage_out` → `update_message` |
| 最终流式回复 | `executors/plugins/tool_loop_v1.py` · `_stream_final` | 计入 `_total_input/output/cache_read/cache_creation_tokens` |
| 质量审查 Hook | `executors/plugins/tool_loop_v1.py` · `_before_finalize_hook` | 每次 review/regen 均通过 `_acc_usage` 累加 |
| Fork Skill | `executors/plugins/tool_loop_v1.py` · `_run_fork_skill` | `_acc_usage` → `usage_out` |
| 草稿生成 | `executors/plugins/tool_loop_v1.py` · `_finalize_reply` | gen draft + hook tokens 一并汇总 |

**Cache token 字段来源**：DeepSeek `prompt_cache_hit_tokens`、OpenAI `prompt_tokens_details.cached_tokens` 映射为 `cache_read_tokens`；Claude 的 `cache_read_input_tokens` / `cache_creation_input_tokens` 分别映射到 `cache_read_tokens` / `cache_creation_tokens`（仅 Claude 有写缓存概念）。非流式与流式（含 Claude `message_start` 事件）均已解析。

**会话级聚合**：`tool_loop_v1` 每轮 `call_ai_once` 后调用 `sessions.add_tokens()`，把四类 token 累加进 `agent_sessions` 表（`migration_006` 新增 `cache_read_tokens` / `cache_creation_tokens` 两列），用于会话级用量统计与崩溃恢复。

`db/queries.py` 中的 `update_message` 使用 `COALESCE(?, col)` 模式：传 `None` 保留数据库已有值，传整数则覆盖，对用户手动编辑消息无副作用。

---

## Getting Started

### 环境要求

- Python 3.11+
- Node.js 18+

### 后端

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

打开 [http://localhost:5173](http://localhost:5173)，输入你的名字加入。

### AI 配置

进入界面后点击左上角 🔑 按钮，配置对应 AI 提供商的 API Key：

| 提供商 | 环境变量 / 配置项 |
|---|---|
| DeepSeek | `DEEPSEEK_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Ollama | `OLLAMA_BASE_URL`（默认 `http://localhost:11434`）|

Key 保存在 `backend/app_config.json`，不会上传到任何第三方。

---

## Architecture：消息总线（Event Bus）

后端采用内部事件总线将业务逻辑与 WebSocket 传输层解耦，设计参考 [OpenCode](https://github.com/opencode-ai/opencode) 的 PubSub 双通道架构。

### 分层结构

```
业务逻辑（orchestrator / permissions / executors）
       ↓  bus.publish(TypedEvent)  /  bus.broadcast(group_id, dict)
EventBus（asyncio Queue，typed channel + wildcard channel）
       ↓  wildcard 订阅
WS Adapter（唯一知道 WSManager 的地方）
       ↓  manager.broadcast(group_id, payload)
WSManager（连接注册表，纯传输层）
       ↓  ws.send_json × N
浏览器
```

### 模块说明

```
backend/bus/
├── events.py   # 28 种 typed event 定义（@event 装饰器 + 中心注册表）
├── engine.py   # EventBus 核心：typed / wildcard 双通道，Subscription 上下文管理器
├── adapter.py  # wildcard 订阅 → manager.broadcast，服务启动时以后台 Task 运行
└── __init__.py # 导出 bus 单例、publish、ws_adapter
```

### 两种发布接口

```python
# 1. Typed event（orchestrator / main.py 使用）
from bus import bus
from bus.events import StreamChunk

await bus.publish(StreamChunk(group_id=1, temp_id="t", delta="hello"))

# 2. 兼容接口（executor 插件使用，call site 不变）
await ctx.broadcaster.broadcast(ctx.group_id, {"type": "tool_call", ...})
# ctx.broadcaster 就是 bus，broadcast() 走同一条路进 EventBus
```

### 订阅方式

```python
# typed 订阅（只收指定 type）
sub = bus.subscribe(StreamChunk)
payload = await sub._queue.get()

# wildcard 订阅（收所有事件）
async with bus.subscribe_all() as sub:
    async for payload in sub:
        ...  # adapter 用这个
```

### 完整事件流（以 Bot 流式回复为例）

```
用户发消息 → main.py 收到 WS 文本
    → bus.publish(Message(...))
    → dispatch_bots → stream_bot_response
        → bus.publish(StreamStart(...))
        → bus.publish(StreamChunk(...)) × N
        → bus.publish(StreamEnd(...))

每个 publish：
    EventBus._dispatch → wildcard Queue.put(payload)
    → ws_adapter 取出 → 去掉 group_id → manager.broadcast(group_id, out)
    → WSManager 遍历连接 → ws.send_json(out) × N 个浏览器
```

### 已定义事件类型（28 种）

| 分类 | 事件 |
|------|------|
| 流式输出 | `stream_start` · `stream_chunk` · `stream_error` · `stream_end` · `stream_aborted` |
| 消息 | `message` · `read` |
| 在线状态 | `presence` |
| Bot 状态 | `typing` · `error` · `steer_queued` · `followup_start` · `steer_injected` · `rewake_injected` |
| 工具执行 | `tool_call` · `tool_result` |
| ReAct | `react_thought` · `react_action` · `react_observation` |
| Compaction | `compaction` |
| Skill | `skills_loaded` · `skill_fork_start` · `skill_fork_end` · `skill_draft_added` |
| 权限审批 | `before_finalize_review` · `before_finalize_approved` · `before_finalize_rejected` · `permission_asked` |

---

## Project Structure

```
nuke-ai-collaborator/
├── backend/
│   ├── main.py              # FastAPI 入口、WebSocket 端点、lifespan
│   ├── ws_manager.py        # WebSocket 连接管理（纯传输层）
│   ├── models.py            # Pydantic 请求模型
│   ├── config.py            # API Key 配置管理
│   ├── bus/                 # 消息总线（见上方架构说明）
│   │   ├── events.py        # 28 种 typed event
│   │   ├── engine.py        # EventBus 核心
│   │   ├── adapter.py       # WS 推送适配层
│   │   └── __init__.py
│   ├── core/
│   │   ├── orchestrator.py  # Bot 调度、流式广播
│   │   ├── role_router.py   # Bot 角色路由逻辑
│   │   └── workflow.py      # 工作流状态机
│   ├── db/
│   │   ├── schema.py        # 建表 DDL
│   │   ├── queries.py       # 数据库查询
│   │   └── models.py        # ORM 模型
│   ├── ai/
│   │   ├── client.py        # 多模型 AI 流式调用
│   │   └── memory.py        # 对话摘要 & 上下文
│   ├── executors/           # Bot executor 插件系统
│   │   ├── base.py          # ExecutionContext / BotExecutor 基类
│   │   ├── registry.py      # executor 注册与发现
│   │   └── plugins/         # simple_v1 · react_v1 · tool_loop_v1
│   ├── permissions/         # 权限引擎
│   ├── skills/              # Skill 发现与加载
│   ├── workspace/           # Bot workspace 初始化
│   ├── scheduler/           # 定时任务插件（APScheduler · store · runner · router）
│   └── api/                 # REST 路由（groups · messages · templates · workflow · workspace）
└── frontend/
    └── src/
        ├── components/
        │   ├── ChatWindow.jsx       # 主聊天窗口
        │   ├── MessageBubble.jsx    # 消息气泡
        │   ├── MessageInput.jsx     # 输入框
        │   ├── GroupList.jsx        # 左侧群组列表
        │   ├── AnnouncementBar.jsx  # 群公告栏
        │   ├── PinnedBar.jsx        # 置顶消息栏
        │   ├── EmojiPicker.jsx      # Emoji 选择器
        │   ├── SearchPanel.jsx      # 消息搜索
        │   ├── AutoReplyModal.jsx   # 自动回复设置
        │   ├── ApiKeyManager.jsx    # API Key 管理
        │   └── MemberList.jsx       # 添加成员
        ├── hooks/
        │   ├── useWebSocket.js      # WebSocket 连接 & 重连
        │   └── useNotifications.js  # 浏览器通知
        └── api.js                   # REST API 请求封装
```

---

## License

MIT
