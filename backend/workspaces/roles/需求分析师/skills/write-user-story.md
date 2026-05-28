---
name: write-user-story
description: 将业务需求转化为标准用户故事格式，包含验收条件
layer: role
role: 需求分析师
status: active
always: false
when_to_use: 当用户描述业务需求要求拆解为用户故事时
max_iterations: 3
---

# Write User Story

## 用户故事格式

```
作为 [用户角色]，
我希望 [功能/操作]，
以便 [业务价值/目的]。
```

## 验收条件格式（Given-When-Then）

```
Given [前置条件]
When [用户操作]
Then [系统响应/结果]
```

## 拆分原则

- 每个故事可在一个迭代内完成（1-3天）
- 故事之间相互独立，可单独交付
- 包含足够的业务价值，不是技术任务
- 用 INVEST 原则检验：Independent / Negotiable / Valuable / Estimable / Small / Testable

## 步骤

1. 识别涉及的用户角色
2. 将需求拆解为独立的功能单元
3. 为每个功能编写用户故事
4. 添加 2-5 条验收条件
5. 标注优先级（P0/P1/P2）

## 输出格式

```
### 用户故事列表

**[P0] US-001: [故事标题]**
作为...，我希望...，以便...

**验收条件：**
- Given... When... Then...
- ...
```
