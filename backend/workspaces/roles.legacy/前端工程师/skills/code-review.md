---
name: code-review
description: 对前端代码（React/Vue/TS）进行评审，关注组件设计、性能和可维护性
layer: role
role: 前端工程师
status: active
always: false
when_to_use: 当用户提交前端代码要求 review 时
max_iterations: 3
---

# Code Review（前端）

## 评审维度

1. **组件设计** — 职责是否单一，props 接口是否清晰
2. **状态管理** — 状态是否放在正确的层级，有无不必要的全局状态
3. **性能** — 有无不必要的重渲染、缺少 memo/useMemo/useCallback
4. **可访问性** — 语义化标签、aria 属性、键盘导航
5. **类型安全** — TypeScript 类型是否完备，有无 `any` 滥用
6. **样式** — 类名是否语义化，有无样式泄露

## 步骤

1. 通读组件结构和数据流
2. 按维度逐一检查
3. 区分 **必须修改** 和 **建议优化**
4. 给出代码片段示例

## 输出格式

```
### Code Review 结果

**必须修改**
- [组件名/行号] 问题描述 + 建议代码

**建议优化**
- [说明]

**整体评价**
一句话总结。
```
