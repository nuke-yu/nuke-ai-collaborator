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

### ⚙️ 管理

- **API Key 管理** — 界面化配置各 AI 提供商的 Key，保存在本地 `app_config.json`
- **数据导出** — 导出聊天记录为 Markdown 或 JSON，含发言人、时间戳、回复关系
- **使用统计** — 各成员发言数量统计，横向进度条可视化

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

## Project Structure

```
nuke-ai-collaborator/
├── backend/
│   ├── main.py          # FastAPI 路由、WebSocket 处理
│   ├── database.py      # SQLite 数据层
│   ├── ws_manager.py    # WebSocket 连接管理 & 在线状态
│   ├── ai_client.py     # 多模型 AI 流式调用
│   ├── models.py        # Pydantic 请求模型
│   ├── config.py        # API Key 配置管理
│   ├── role_router.py   # Bot 角色路由逻辑
│   └── memory.py        # 对话摘要 & 上下文
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
        │   ├── useWebSocket.js      # WebSocket 连接
        │   └── useNotifications.js  # 浏览器通知
        └── api.js                   # API 请求封装
```

---

## License

MIT
