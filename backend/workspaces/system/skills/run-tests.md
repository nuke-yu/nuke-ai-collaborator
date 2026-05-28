---
name: run-tests
description: 运行项目测试套件并汇报结果
layer: system
status: active
always: false
when_to_use: 当用户要求跑测试、验证改动或检查测试覆盖率时
max_iterations: 5
---

# Run Tests

## 用途
执行项目的测试命令，解析输出，汇报通过/失败情况并给出修复建议。

## 步骤

1. 查找测试配置文件（package.json / pytest.ini / Makefile / 工作区 BOOTSTRAP.md）
2. 确认测试命令（如 `npm test` / `pytest` / `make test`）
3. 在工作区记录测试命令和预期结果
4. 请用户或 Shell 工具执行测试命令
5. 解析输出，区分通过/失败/跳过
6. 对失败项：定位原因，给出最小修复建议

## 输出格式

```
✅ 通过：24 个
❌ 失败：2 个
  - test_login: AssertionError — 期望 200，得到 401
  - test_register: TimeoutError

建议：检查 auth middleware 是否正确注入 test_client。
```

## 注意事项

- 不自动修改代码，只分析和建议
- 若无测试配置，建议用户先添加测试框架
