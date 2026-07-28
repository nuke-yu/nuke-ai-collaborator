# Nuke AI Collaborator — Memory System Testing Guide (内存系统测试指南)

本文档旨在提供针对最新重构后的 **Memory & Learning 内存系统** 的即时测试与快速验证指南，无需等待漫长的真实对话积累数据即可完成全量功能与性能校验。

---

## 1. 快速测试方案一览 (Quick Testing Overview)

| 测试方案 | 验证目标 | 耗时 | 执行命令 |
|---|---|---|---|
| **秒级数据注入与召回 (Fast Seed)** | 验证 FTS5 事实召回、Skill 智能匹配与 Personal Vault 偏好注入 | < 2 秒 | `PYTHONPATH=backend python3 scripts/seed_memory_demo.py --group-id 1` |
| **崩溃恢复与故障注入 (Durability Harness)** | 验证 Worker 崩溃 (`SIGKILL`) 断点续传、Outbox 超时重试与 250+ 知识大规模召回 | ~5 秒 | `PYTHONPATH=backend pytest backend/tests/test_memory_durability_eval_harness.py -v -s` |
| **算法打分评估 (Algorithm Benchmark)** | 验证 Hybrid Vector+Lexical 混合打分、Weighted Jaccard 相似度与成熟度权重 | ~3 秒 | `PYTHONPATH=backend pytest backend/tests/test_memory_algorithm_benchmark.py -v -s` |
| **内存全量回归测试 (Full Memory Suite)** | 验证 142+ 项 Memory 单元与集成测试契约 | ~8 秒 | `PYTHONPATH=backend pytest backend/tests/test_memory*.py` |

---

## 2. 秒级 Mock 数据注入与召回脚本 (`scripts/seed_memory_demo.py`)

项目内置了数据快速生成与召回校验脚本 `scripts/seed_memory_demo.py`，可以在指定 Group 的 SQLite 数据库和个人 Vault 中秒级注入结构化测试内存：

### 2.1 运行命令
```bash
PYTHONPATH=backend python3 scripts/seed_memory_demo.py --group-id 1 --user-id 1 --bot-id 1
```

### 2.2 验证内容
脚本会自动完成 4 步验证：
1. **Canonical Group Facts 注入**：注入架构定义与技术栈事实，验证 SQLite 原生 FTS5 倒排索引与触发器同步。
2. **Learned Skills 注入**：注入 `stable` / `active` / `trial` 三种不同成熟度的技能。
3. **Personal Knowledge Vault 注入与投影**：向用户独立 SQLite 库写入 `preference` 偏好，并建立与 Group 1 的授权投影。
4. **即时召回率与 Prompt 拼接测试**：输出 FTS5 事实匹配、Skill 加权过滤以及 Personal Context 字符预算截断预览。

---

## 3. 故障注入与高可用测试套件 (`test_memory_durability_eval_harness.py`)

验证系统在极端异常和高压环境下的工业级高可用性：

### 3.1 运行命令
```bash
PYTHONPATH=backend pytest backend/tests/test_memory_durability_eval_harness.py -v -s
```

### 3.2 包含测试场景
- **250+ 知识规模强压召回**：批量填充 250 条组事实与经验，验证倒排索引召回在 < 5ms 内完成。
- **Transactional Outbox 异常重试**：模拟向量库网络超时与短暂停机，验证背景 Job 的 Lease 锁与指数退避重试。
- **Worker 进程崩溃断点续传**：模拟 Worker 进程遭遇 `SIGKILL` 强制退出后，`projection_rebuild.py` 基于单调游标 `(sort_ts, record_id)` 从断点成功恢复重构。

---

## 4. 算法打分与重排评估套件 (`test_memory_algorithm_benchmark.py`)

验证 Memory 召回算法的精准度与防幻觉硬边界：

### 4.1 运行命令
```bash
PYTHONPATH=backend pytest backend/tests/test_memory_algorithm_benchmark.py -v -s
```

### 4.2 包含测试场景
- **Hybrid Scoring 混合打分**：验证 $\text{Score} = (0.45 \cdot \text{Lexical} + 0.35 \cdot \text{Vector} + 0.20 \cdot \text{ClusterMatch}) \cdot \text{Confidence}$ 针对同义词与反义词的区分度。
- **Skill Weighted Jaccard 相似度**：验证成熟度权重 $W_{\text{stable}}=1.0, W_{\text{active}}=0.9, W_{\text{trial}}=0.7$ 下，高置信度 Stable 技能不会被误杀。
- **硬相关度阈值过滤**：验证分值低于 0.08 的无用噪声经验被自动抛弃，保护 Prompt 上下文预算。

---

## 5. 内存系统全量回归测试

在修改任何 Memory 模块代码后，请确保跑通全部 Memory 单元与集成测试：

```bash
PYTHONPATH=backend pytest backend/tests/test_memory*.py
```

预期结果：`142 passed`（或更高），100% 绿色通过。
