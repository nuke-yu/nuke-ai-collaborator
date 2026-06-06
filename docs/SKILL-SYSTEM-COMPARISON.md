# 🛠️ Skill 系统：跨智能体框架的加载与处理机制横向对比

在智能体（Agent）开发体系中，**Skill（技能/指令工具）**是衔接“大模型自然语言决策”与“具体物理环境执行”的关键桥梁。与通用的 VFS 文件读写或 shell 执行不同，Skill 承载了领域内的高密经验、避坑指南和固化的多步操作流。

本报告对业界四大主流框架（**Claude Code / Claude-haha**、**opencode**、**gsd-2**、**openclaw**）与我们当前的项目（**nuke-ai-collaborator**）的 Skill 系统，从目录拓扑、加载优先级、去重冲突、Prompt 预算管理、安全校验、参数替换、以及执行副作用等核心维度进行深度横向解构与对比，并包含来自严苛架构师视角的诊断与改进建议。

---

## 一、 智能体框架 Skill 系统多维度横向对比表

| 维度 / 机制 | Claude Code (TypeScript) | opencode (TypeScript) | gsd-2 (TypeScript/Rust) | openclaw (TypeScript) | 我们的项目 (Python/SQLite) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. 目录拓扑与扫描范围** | **三级 Scope 扫描**：<br>1. Managed (`.claude/skills`) <br>2. User (`~/.claude/skills`) <br>3. Project (`.claude/skills`，向根目录追溯)<br>支持 `--add-dir` 手动扩展。 | **配置文件与 URL 双轨制**：<br>1. 全局与项目级目录扫描<br>2. `cfg.skills.paths` 配置文件<br>3. `cfg.skills.urls` 远程 URL 动态拉取并本地缓存。 | **行业标准目录**：<br>1. 全局使用 `~/.agents/skills/` (Ecosystem)<br>2. 项目级 `.agents/skills/`<br>3. 兼容旧版 `~/.gsd/agent/skills/` 迁移。 | **两级嵌套目录**：<br>1. 扫描直属 `.md` 技能<br>2. 支持一级子文件夹分组（如 `skills/coze/koze-retrieval/SKILL.md`）<br>3. 支持 Symlink 符号链接加载。 | **4 层覆盖架构 (L1 $\rightarrow$ L4)**：<br>1. **L1 General**（内置通用）<br>2. **L2 Group**（群组共享）<br>3. **L3 Role**（角色专属）<br>4. **L4 Learned**（自学沉淀）<br>外加 **Personal** 层（个人手写，优先级最高）。 |
| **2. 物理目录结构形式** | **技能包文件夹制**：<br>仅支持 `[skill-name]/SKILL.md` 目录格式，不扫描根部平铺的单体 `.md` 文件（legacy commands 除外）。 | **SKILL.md 规范**：<br>外部/远程包必须包含 `SKILL.md` 入口文件，且不支持单体 `.md` 平铺加载。 | **混合扫描**：<br>1. 根目录下支持平铺的单体 `.md` 文件<br>2. 子目录下必须使用 `SKILL.md` 格式。 | **SKILL.md 规范**：<br>子目录中必须有且仅有一个 `SKILL.md` 入口文件。 | **混合扫描**：<br>1. 支持直接平铺的单体 `.md` 文件（如基础技能）<br>2. 复杂技能支持以目录为包进行入口加载。 |
| **3. 重名去重与冲突解决** | **先入为主 (First-Wins)**：<br>通过 `realpath` 解析 canonical 真实物理路径，排队去重，忽略后续同名/同物理文件的加载并记录 Log。 | **本地覆写 (Local Overrides)**：<br>内置技能最先注册，随后扫描的本地磁盘技能若重名直接**覆盖**内置技能。 | **冲突诊断警告 (Collision Warning)**：<br>不允许重名。若发生重名，系统生成 `collision` 诊断报告并发出警告，指定 `winnerPath` 与 `loserPath`。 | **物理路径去重**：<br>基于 `realpathSync` 物理路径过滤 duplicate，若存在重名且有冲突直接警告并跳过。 | **层级覆盖 (Layer-Override)**：<br>按 L1 $\rightarrow$ L2 $\rightarrow$ L3 $\rightarrow$ L4 $\rightarrow$ Personal 扫描合并，同名技能**后层直接覆写前层**（例如 L4/Personal 会覆盖 L1 System）。 |
| **4. 伴随条件激活与懒加载** | **路径匹配激活 (`paths`)**：<br>frontmatter 中配置 `paths` 过滤规则（gitignore 语法），仅在触碰/修改对应特征文件时才激活注入。 | **按需加载 (Get-on-Demand)**：<br>只在模型请求或 UI 渲染时通过 `get()` and `available()` 动态获取内容。 | **触发控制 (`disable-model-invocation`)**：<br>若配置为 `true`，模型不能自动感知识别（不进 XML），仅能通过用户 slash 命令手动触发。 | **模型过滤与可见性控制**：<br>在 Prompt 中通过 `<available_skills>` 提示词进行按 `agentId` 动态过滤和可见性控制。 | **双态注入 Base**：<br>`always: true` 的技能全文常驻 system prompt；`always: false` 的技能仅以 XML/JSON 元数据声明，供 LLM 懒加载。 |
| **5. Prompt 容量控制与压缩** | **轻量前置预估**：<br>未激活时仅将 `name`、`description` 等 frontmatter 组成短句参与 Token 估算，不加载 Markdown 实体。 | **元数据渲染**：<br>在系统提示词中仅以 `- name: desc` 简短形式渲染，执行时才加载具体 Body。 | **XML 标准格式**：<br>将可见技能转换为 `<skill><name>...</name><location>...</location></skill>` 注入 Prompt 中。 | **Home 目录压缩 (`~/`) & 熔断**：<br>1. 将技能绝对路径中的 homedir 缩短为 `~/`（节省 token 并防信息泄漏）<br>2. 限制单文件大小（< 256KB） and 总 Prompt 长度（默认 18K 字符）。 | **冷热分流机制**：<br>将元数据 XML 平铺进 System Prompt，减少常驻 Prompt 预算，大模型有需时按名索取。 |
| **6. 安全沙箱与 HIL 防线** | **Shell 指令阻断**：<br>本地技能允许 `!{bash}` 预评估；但**绝对禁止** remote MCP 技能评估任何 shell 指令，防 RCE 溢出。 | **角色权限网关 (Permission Gate)**：<br>对 Skill 进行 Permission 安全组划归，评估 Agent 角色，`deny` 用户可阻断特定技能的拉取。 | **前置合规检验**：<br>严格验证 `name === parentDirName`，限制 lowercase-hyphen-only 命名规范，避免任意字符转义漏洞。 | **Symlink 越界阻断 (Escape Guard)**：<br>严格检验 realpath，禁止通过 symlink 逃逸出工作区或允许的安全目录。 | **两段式审批防线 (HITL Gate)**：<br>Bot 产生的自学技能（Learned）限制写入 `draft/`（不可注入），需人类在 Web UI 审批后移至 `active/` 生效。 |
| **7. 参数替换与动态能力** | **标准参数占位符**：<br>支持 `$ARGUMENTS` 以及 `$ARGUMENTS[N]` 参数替换。若无占位符则追加在内容尾部。 | **管道流处理**：<br>利用 Effect-TS 并行管道流替换参数和 ${SKILL_DIR} 环境占位符。 | **Schema 参数绑定**：<br>遵循标准规范，通过 JSON Schema 或 YAML 格式校验输入参数的合法性。 | **内置脚本环境**：<br>基于模板的替换，解析 `${SKILL_DIR}`。 | **全面兼容与安全替换**：<br>支持 `$ARGUMENTS`, `$ARGUMENTS[N]`, `$N` 占位符替换；同时安全地解析 `${SKILL_DIR}` 相对路径。 |
| **8. 执行后副作用机制 (Side-Effects)** | **缓存清除与热更新**：<br>执行后可触发 `clearSkillCaches()` 等动作。 | **无明显机制**：<br>主要作为纯 Prompt 模板使用。 | **无明显机制**：<br>偏向标准化只读调用。 | **无明显机制**：<br>主要作为纯 Prompt 注入。 | **大模型行为调整 (Side-Effects)**：<br>从元数据解析 `max_iterations`, `allowed_tools`, `model`, `context: "fork"` 等，动态改变 LLM 本次执行的循环上限、模型配置和工具白名单。 |

---

## 二、 架构诊断建议与薄弱点反馈（Strict Architect Feedback）

从严格又苛刻的架构师视角出发，我们当前的 Python Skill 实现虽然在顶层设计上具备了层级覆盖、沙箱逃逸 guard 等优秀思路，但底层代码结构存在以下技术硬伤和隐患：

### 1. 覆写逻辑不完整（破坏性覆盖缺陷）
在 [discovery.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/skills/discovery.py) 的 `_list_skills_all_sync` 中合并字典时，使用 `merged.update(personal_skills)` 执行了整轨覆盖。当用户通过 [lifecycle.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/skills/lifecycle.py) 中的 `update_skill_status` 禁用下层技能时，会在 Personal 层生成一个仅包含 frontmatter 的 Stub 存根文件：
```python
stub = f"---\nname: {skill_name}\nlayer: personal\nstatus: {new_status}\n---\n"
```
由于没有对内容进行深度合并（Deep Merge），该 Stub 会**彻底覆盖并弄丢**下层文件的技能内容 Body。当大模型未来重新启用该技能或尝试加载时，读到的是一个完全被掏空的技能。
* **改进方案**：应改为**元数据深度合并**，即 Personal 层的 Stub 只重写 status 等特定字段，其物理文件内容在加载时若缺失，自动降级（Fallback）去读取下层定义。

### 2. 脆弱的手工 YAML 解析器
在 [metadata.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/skills/metadata.py) 的 `parse_frontmatter` 中，我们采用了自定义的字符串 Partition 和 Comma Split 方式解析元数据。
当用户定义符合标准 YAML 规范的多行文本或层级列表时，手工解析器会完全崩溃或失效，使得 `allowed-tools` 等控制字段读取不全，对安全白名单构成隐患。
* **改进方案**：引入 Python 标准的 `yaml.safe_load` 解析 Frontmatter，确保元数据解析逻辑绝对符合 YAML 标准。

### 3. 并发安全与文件 I/O 竞争
生命周期操作（写 Draft、审批、拒绝、改状态）直接以同步方式对磁盘进行覆盖与 Rename，没有任何文件锁（File Lock）或事务保障。在高并发会话或多 Agent 并行运行时，针对同一技能的操作极易发生冲突，引发 `FileNotFoundError` 或文件内容截断损坏。
* **改进方案**：引入文件排他锁，或将技能生命周期状态和层级关系彻底**数据库化（SQLite）**，仅将磁盘作为初始导入源。

### 4. 缺乏安全的动态求值引擎
在 [processor.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/skills/processor.py) 中，为了防止恶意智能体进行注入，我们一刀切地禁用了 Shell 预执行（`!`）。这保证了绝对的安全，但也使得技能沦为纯静态 prompt 模板，失去了环境感知能力（例如根据当前工作区语言自动注入对应的测试流程规范）。
* **改进方案**：引入一个隔离且安全的 Jinja2 沙箱或 Python 受限表达式计算器，在不给 RCE 任何可乘之机的前提下，允许根据环境动态计算 Prompt 变量。

### 5. 平铺命名空间冲突
所有技能在 `list_skills` 后被强行平铺合并，如果不同 Role（L3）或 Group（L2）下定义了同名的 `test.md` 技能，会出现隐式的覆盖冲突，且不输出任何 Collision 警告（gsd-2 有健全的冲突诊断警告报告）。
* **改进方案**：在底层扫描时引入命名空间机制（例如 `role:developer::run-tests`），或者在发现重名冲突时生成 Collision Diagnostic Log 供系统排查。

### 6. 高频磁盘扫描开销
每次运行技能或刷新 UI 时，系统都会高频同步扫描 4 层物理目录，在高负载下，这将成为系统响应时间的木桶短板。
* **改进方案**：为 `list_skills_all` 引入基于 File Watcher 驱动的内存缓存（In-Memory Cache），实现秒级热更新的同时消除磁盘同步扫描延迟。
