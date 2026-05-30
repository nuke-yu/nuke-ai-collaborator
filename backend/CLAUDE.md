# 项目约定（backend）

## 跑测试的频率
- 节奏：**完成一个功能点 → 写完对应的 unit test → 只跑跟这段代码相关的 unit test**。无关的 test 文件一律不跑。
  - 「相关」= 直接覆盖本次改动的那个/那几个 test 文件（例如改 `core/orchestration/*` 就只跑 `tests/test_workflow.py`）。
  - 不要为了"保险"顺手把无关套件也带上，也不要每改一行就跑——以功能点为粒度。
- **全套**（`python3 -m pytest`）只在两种情况跑：① commit 之前做一次回归把关；② 明确要求"跑全套"时。
- python 命令是 `python3`（不是 `python3.11`）。
