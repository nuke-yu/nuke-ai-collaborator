# AI Chat Workspace — Phase 1 POC Design

## 目标

构建一个类 Slack 的聊天工作区，真人和 AI 角色在同一群组中协作，AI 角色根据任务阶段依次介入。

## 范围（POC）

- 单个聊天群组
- 真人用户 + 多个 AI Bot 成员
- AI 角色可配置（角色名、职责、system prompt）
- 实时消息（WebSocket）
- 消息持久化（SQLite）
- 不需要登录/注册（POC 阶段直接输入用户名进入）

## 技术栈

- **前端**：React + Vite + Tailwind CSS
- **后端**：FastAPI + WebSocket + SQLite
- **AI**：DeepSeek API（抽象成可替换接口）

## 架构

```
Browser (React)
    ↕ WebSocket
FastAPI Server
    ├── /ws/{group_id}         # WebSocket 消息通道
    ├── /api/groups            # 群组管理
    ├── /api/members           # 成员管理（human/bot）
    └── AI Agent Layer
            ├── RoleRouter     # 判断哪个角色应该响应
            └── DeepSeekClient # 可替换的模型接口
SQLite
    ├── groups
    ├── members (human + bot)
    └── messages
```

## 数据模型

**Group**: id, name, created_at
**Member**: id, group_id, name, type(human/bot), role, system_prompt, avatar_color
**Message**: id, group_id, member_id, content, created_at

## 消息流

1. 真人发送消息 → WebSocket → 服务端广播给所有成员
2. 服务端判断是否需要 AI 响应（关键词 / @mention / 任务阶段）
3. 触发对应 AI 角色 → 调用 DeepSeek → 流式返回消息
4. AI 消息广播给所有成员

## UI 布局

```
┌─────────────────────────────────────────┐
│  群组名称                               │
├──────────┬──────────────────────────────┤
│          │  消息列表（气泡式）           │
│  成员列表 │  [人类] 消息内容             │
│          │  [分析师🤖] 分析内容          │
│  👤 张三  │  [开发者🤖] 代码实现          │
│  🤖 分析师│                             │
│  🤖 开发者│  输入框 + 发送按钮           │
│  🤖 测试员└─────────────────────────────┘
```

## POC 不包含

- 用户登录/权限系统
- 多群组切换
- 文件上传
- 管理平台（Phase 2）
