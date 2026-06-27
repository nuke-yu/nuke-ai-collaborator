---
name: update-board
description: 更新群组工作看板，管理任务状态流转
layer: role
role: pm
status: active
always: false
when_to_use: 当需要新增任务、更新进度或标记完成时
max_iterations: 3
---

# Update Board

## 看板结构

```
Backlog → 进行中 → 已完成
```

## 步骤

1. 读取群组工作区的 `BOARD.md`
2. 根据用户指令执行操作：
   - **新增任务**：填写需求名、优先级，添加到 Backlog
   - **开始任务**：从 Backlog 移至进行中，填写负责人
   - **完成任务**：从进行中移至已完成，填写完成时间和产出
   - **更新状态**：修改进行中任务的 Todo 或状态描述
3. 更新 `更新时间` 字段为今日日期
4. 写回 `BOARD.md`

## 优先级定义

| 级别 | 说明 |
|------|------|
| P0   | 阻塞发布，立即处理 |
| P1   | 核心功能，当前迭代必须完成 |
| P2   | 重要但不紧急，下个迭代 |
| P3   | Nice-to-have，有空再做 |

## 注意事项

- 操作前先读取最新看板内容，避免覆盖他人更新
- 每次更新同步告知相关 Bot 任务变化
