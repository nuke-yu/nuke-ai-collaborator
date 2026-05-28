---
name: commit-and-pr
description: 生成规范的 git commit message 和 PR 描述
layer: role
role: developer
status: active
always: false
when_to_use: 当用户完成一个功能或 bugfix，需要提交代码或创建 PR 时
max_iterations: 3
---

# Commit and PR

## Commit Message 规范（Conventional Commits）

```
<type>(<scope>): <短描述>

[可选正文：解释为什么这样改，而不是改了什么]

[可选尾注：Breaking Change / Closes #issue]
```

**type 枚举：**
- `feat` — 新功能
- `fix` — Bug 修复
- `refactor` — 重构（不改变功能）
- `test` — 添加/修改测试
- `docs` — 文档变更
- `chore` — 构建配置、依赖更新

## 步骤

### 生成 Commit Message

1. 了解本次改动内容（diff 或用户描述）
2. 判断 type 和 scope
3. 输出 commit message，例：
   ```
   feat(auth): add JWT refresh token support
   
   Tokens now auto-refresh 5 minutes before expiry to prevent
   silent logouts during long sessions.
   ```

### 生成 PR 描述

```markdown
## 变更内容
- 简要列出核心改动（3-5 条）

## 动机
为什么要做这个改动？解决了什么问题？

## 测试
- [ ] 单元测试通过
- [ ] 手动测试场景：...

## 注意事项
上线前需要注意的事项（迁移、配置变更等）
```

## 注意事项

- commit message 第一行不超过 72 字符
- 不使用"fix bug"、"update code"等无意义描述
- Breaking Change 必须在 commit footer 注明
