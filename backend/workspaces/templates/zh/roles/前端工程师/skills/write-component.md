---
name: write-component
description: 根据需求编写 React 组件，包括 props 设计、状态管理和样式
layer: role
role: 前端工程师
status: active
always: false
when_to_use: 当用户要求编写或新建一个 React/Vue 组件时
max_iterations: 4
---

# Write Component（React）

## 组件设计前确认

- **功能**：组件做什么？接收哪些数据？
- **交互**：有哪些用户操作和状态变化？
- **复用性**：是通用组件还是业务组件？
- **样式方案**：TailwindCSS / CSS Modules / styled-components？

## 步骤

1. 定义 Props 接口（TypeScript）
2. 确定内部状态（useState / useReducer）
3. 实现渲染逻辑
4. 提取副作用到 useEffect
5. 导出组件和类型

## 代码规范

- 函数组件 + hooks，不用 class 组件
- Props 接口命名：`ComponentNameProps`
- 事件处理命名：`handle` 前缀，如 `handleSubmit`
- 避免在 JSX 中写匿名函数（影响性能和可读性）

## 输出格式

直接输出完整组件代码，包含类型定义和导出。必要时附使用示例。
