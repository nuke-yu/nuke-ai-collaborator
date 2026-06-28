---
name: debug-error
description: 分析 Python 异常堆栈和错误日志，定位根因并给出修复方案
layer: role
role: 后端Python专家
status: active
always: false
when_to_use: 当用户粘贴 Python 异常堆栈或描述程序异常行为时
max_iterations: 4
---

# Debug Error（Python）

## 调试框架

### 1. 读懂 Traceback
- 从最底部的 `Exception` 行开始读
- 找到最后一个属于项目代码的栈帧（不是标准库/三方库）
- 注意 `During handling of the above exception` — 链式异常

### 2. 常见异常速查

| 异常 | 常见原因 |
|------|----------|
| AttributeError | 对象为 None、拼写错误、属性不存在 |
| KeyError | 字典 key 不存在，应用 `.get()` |
| RuntimeError: no running event loop | 在非协程上下文调用 `asyncio.run()` 嵌套 |
| RuntimeWarning: coroutine never awaited | 调用 async 函数忘记 await |
| RecursionError | 无限递归，检查终止条件 |
| ImportError / CircularImport | 循环导入，重构模块依赖 |

### 3. 定位步骤

1. 读取完整 Traceback
2. 找到项目代码第一个出问题的位置
3. 检查该位置的变量状态
4. 构造最小复现脚本

## 输出格式

```
### 根因
[一句话描述]

### 触发代码
[文件名:行号] 代码片段

### 修复方案
```python
# 修复后代码
```

### 预防建议
```
