---
name: debug-error
description: 分析 Java 异常堆栈和错误日志，定位根因并给出修复方案
layer: role
role: 后端Java工程师
status: active
always: false
when_to_use: 当用户粘贴 Java 异常堆栈、错误日志或描述程序崩溃/行为异常时
max_iterations: 4
---

# Debug Error（Java）

## 调试框架

### 1. 读懂堆栈
- 从 `Caused by` 最底层异常开始读
- 找到第一个属于项目包名的栈帧（不是 JDK/框架内部）

### 2. 常见异常类型速查

| 异常 | 常见原因 |
|------|----------|
| NullPointerException | 对象未初始化、Optional 未判断 |
| ClassCastException | 泛型擦除、错误强转 |
| ConcurrentModificationException | 遍历时修改集合 |
| OutOfMemoryError | 内存泄漏、大对象、无限循环创建 |
| DeadlockException | 多锁顺序不一致 |
| LazyInitializationException | Hibernate session 已关闭后访问懒加载属性 |

### 3. 定位步骤

1. 确认异常类型和消息
2. 找到触发代码行
3. 往上追调用链，找到业务入口
4. 构造最小复现场景

## 输出格式

```
### 根因
[一句话描述]

### 触发代码
[文件名:行号] 代码片段

### 修复方案
```java
// 修复后代码
```

### 预防建议
- 如何避免同类问题再次出现
```
