---
name: write-unit-test
description: 为 Java 代码编写 JUnit 5 单元测试，覆盖正常路径、边界条件和异常路径
layer: role
role: 后端Java工程师
status: active
always: false
when_to_use: 当用户要求为 Java 代码编写单测时
max_iterations: 3
---

# Write Unit Test（Java）

## 测试原则

- 每个测试只验证一个行为
- 测试命名：`method_condition_expectedResult`
- 使用 `@DisplayName` 说明测试意图
- 依赖通过 Mockito mock，不依赖外部服务

## 覆盖清单

- [ ] 正常路径（happy path）
- [ ] 边界值（空集合、null、最大/最小值）
- [ ] 异常路径（期望抛出异常用 `assertThrows`）
- [ ] 有副作用的调用（用 `verify` 验证）

## 步骤

1. 分析被测方法的输入/输出/依赖
2. 用 `@ExtendWith(MockitoExtension.class)` 管理 mock
3. 按覆盖清单逐一编写
4. 确保测试相互独立（无共享状态）

## 输出格式

直接输出完整测试类代码，包含所有 import。
