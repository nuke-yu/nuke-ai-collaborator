---
name: debug-error
description: 分析前端报错（控制台错误、网络异常、渲染问题），定位根因并给出修复方案
layer: role
role: 前端工程师
status: active
always: false
when_to_use: 当用户粘贴前端错误信息、描述页面异常或白屏问题时
max_iterations: 4
---

# Debug Error（前端）

## 调试框架

### 1. 错误类型分类

| 类型 | 特征 | 排查方向 |
|------|------|----------|
| TypeError | Cannot read properties of undefined | 空值检查、数据加载时序 |
| 渲染错误 | React Error Boundary 触发 | 组件 props 类型、条件渲染 |
| 网络错误 | 4xx/5xx、CORS | 接口路径、请求头、跨域配置 |
| 水合错误 | Hydration mismatch | SSR/CSR 数据不一致 |
| 样式错误 | 布局错乱、元素不显示 | CSS 优先级、flex/grid 配置 |

### 2. 定位步骤

1. 读取完整错误信息和堆栈
2. 找到第一个属于项目代码的栈帧
3. 检查该位置的数据状态（console.log / React DevTools）
4. 构造最小复现

## 输出格式

```
### 根因
[一句话描述]

### 触发位置
[文件名:行号] 代码片段

### 修复方案
```tsx
// 修复后代码
```

### 预防建议
```
