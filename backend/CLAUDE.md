# 项目约定（backend）

## 跑测试的频率
- 改完代码**默认只跑改动相关的那个 test 文件**（例如改 `core/orchestration/*` 就只跑 `tests/test_workflow.py`），不要每次都跑全套。
- **全套**（`python3 -m pytest`）只在两种情况跑：① commit 之前做一次回归把关；② 明确要求"跑全套"时。
- python 命令是 `python3`（不是 `python3.11`）。
