---
name: write-unit-test
description: 为指定函数或模块编写单元测试
layer: role
role: developer
status: active
always: false
when_to_use: 当用户说「帮我写测试」或「给这个函数加单元测试」时
max_iterations: 5
---

# Write Unit Test

## 原则

- **TDD 优先**：先写失败的测试，再写实现
- **最小化 Mock**：只 mock 外部 I/O（网络、数据库），不 mock 业务逻辑
- **边界覆盖**：正常路径 + 边界值 + 错误路径 各至少一个 case

## 步骤

1. 读取目标函数/模块代码，理解输入输出和依赖
2. 列出需覆盖的场景：
   - 正常路径（happy path）
   - 边界值（空输入、最大值、0）
   - 错误路径（非法输入、依赖抛出异常）
3. 编写测试代码，每个 case 独立命名（`test_<函数名>_<场景>`）
4. 确认测试可独立运行（无隐性依赖全局状态）

## 测试模板（Python/pytest）

```python
import pytest
from module import target_function

def test_target_function_normal():
    result = target_function(valid_input)
    assert result == expected_output

def test_target_function_empty_input():
    result = target_function("")
    assert result == ""  # 或 pytest.raises(ValueError)

def test_target_function_error():
    with pytest.raises(ValueError, match="具体错误信息"):
        target_function(invalid_input)
```

## 注意事项

- 测试函数名要自文档化，读名字就知道在测什么
- 每个 test 只断言一件事
- 写完后提示用户运行 `run-tests` 验证
