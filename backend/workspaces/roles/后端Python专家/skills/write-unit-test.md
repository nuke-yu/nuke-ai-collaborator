---
name: write-unit-test
description: 为 Python 代码编写 pytest 单元测试，覆盖正常路径、边界条件和异常路径
layer: role
role: 后端Python专家
status: active
always: false
when_to_use: 当用户要求为 Python 代码编写单测时
max_iterations: 3
---

# Write Unit Test（Python）

## 测试原则

- 每个测试只验证一个行为
- 测试命名：`test_<method>_<condition>_<expected>`
- 依赖通过 `unittest.mock.patch` 或 `pytest fixture` 隔离
- 异步函数用 `pytest-asyncio` 的 `@pytest.mark.asyncio`

## 覆盖清单

- [ ] 正常路径（happy path）
- [ ] 边界值（空列表、None、0、最大值）
- [ ] 异常路径（`pytest.raises(ExceptionType)`）
- [ ] 有副作用的调用（`Mock.assert_called_once_with`）
- [ ] 异步函数（`async def test_xxx`）

## 步骤

1. 分析被测函数的输入/输出/依赖
2. 用 `@pytest.fixture` 设置共享前置条件
3. 用 `patch` 隔离外部依赖（DB、HTTP、文件I/O）
4. 按覆盖清单逐一编写测试
5. 确保测试相互独立

## 输出格式

直接输出完整测试文件代码，包含所有 import 和 fixture。
