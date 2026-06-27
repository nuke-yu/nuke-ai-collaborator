---
name: code-review
description: 对 Python 代码进行评审，关注 Pythonic 风格、异步安全、类型标注和性能
layer: role
role: 后端Python专家
status: active
always: false
when_to_use: 当用户提交 Python 代码要求 review 时
max_iterations: 3
---

# Code Review（Python）

## 评审维度

1. **Pythonic 风格** — 是否使用了合适的内置特性（推导式、生成器、上下文管理器）
2. **类型标注** — 函数签名和变量是否有完整的类型提示
3. **异步安全** — async/await 使用是否正确，有无阻塞调用在协程中
4. **异常处理** — 是否捕获了足够精确的异常，有无裸 `except`
5. **性能** — 有无不必要的同步 I/O、重复计算、内存泄漏
6. **可测试性** — 依赖是否可注入，副作用是否隔离

## 步骤

1. 通读代码，理解意图
2. 按维度逐一检查
3. 区分 **必须修改** 和 **建议优化**
4. 给出修改示例

## 输出格式

```
### Code Review 结果

**必须修改**
- [行号/函数名] 问题描述
  ```python
  # 建议改为：
  ```

**建议优化**
- [说明]

**整体评价**
一句话总结。
```
