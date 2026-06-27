---
name: write-unit-test
description: 为指定函数或模块编写单元测试，覆盖正向、边界、异常场景
layer: role
role: 代码助手
status: active
always: false
when_to_use: 当用户要求写测试、补充覆盖率，或说「帮我写 unit test」时
max_iterations: 5
---

# Write Unit Test

## 测试设计原则

- **AAA 结构**：Arrange（准备）→ Act（执行）→ Assert（断言）
- **覆盖三类场景**：正向路径 / 边界条件 / 异常路径
- **测试名即文档**：`test_<函数名>_<场景描述>` 命名法
- **单一断言**：每个测试只验证一件事

## 步骤

1. 读取目标文件，理解函数签名和行为
2. 列出测试矩阵：正向场景 × 边界 × 异常
3. 逐一编写测试用例
4. 检查是否需要 mock 外部依赖

## 输出格式

直接输出可运行的测试代码，包含必要的 import 和 fixture。
每个测试附一行注释说明验证意图。
