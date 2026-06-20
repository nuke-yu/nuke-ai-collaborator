# 项目约定（backend）

## 跑测试的频率
- 节奏：**完成一个功能点 → 写完对应的 unit test → 只跑跟这段代码相关的 unit test**。无关的 test 文件一律不跑。
  - 「相关」= 直接覆盖本次改动的那个/那几个 test 文件（例如改 `core/orchestration/*` 就只跑 `tests/test_workflow.py`）。
  - 不要为了"保险"顺手把无关套件也带上，也不要每改一行就跑——以功能点为粒度。
- **全套**（`python3 -m pytest`）只在两种情况跑：① commit 之前做一次回归把关；② 明确要求"跑全套"时。
- python 命令是 `python3`（不是 `python3.11`）。
- Review: ** any task before start, you must validate the alignment between the code change and requirement

## 依赖管理（重要）
- `requirements.txt` 是人编辑的源（松散直接依赖）；`requirements.lock` 是全 pin 的传递闭包，**Docker 只读 lock**。
- **改完 `requirements.txt` 必须重新生成 `requirements.lock`**，否则镜像装的还是旧依赖。从仓库根目录跑（lock 文件头部也有这条命令）：
  ```
  docker run --rm -v "$PWD/backend:/b" -w /b python:3.11-slim sh -c \
    'pip install -q -r requirements.txt && pip freeze --all' \
    | grep -viE "^(pip|setuptools|wheel)==" > backend/requirements.lock
  ```
- lock 必须在 `linux/python:3.11-slim`（构建基础镜像）里生成——在 macOS 上 freeze 会解析出不同版本。
- 两个文件一起提交。
