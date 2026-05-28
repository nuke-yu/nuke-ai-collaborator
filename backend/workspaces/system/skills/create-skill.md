---
name: create-skill
description: 创建新的 Skill 文件，自动生成标准 frontmatter 结构
layer: system
status: active
always: false
when_to_use: 当用户说「帮我创建一个技能」或「把这个做法记录为 skill」时
max_iterations: 3
---

# Create Skill

## 用途
根据用户描述，生成符合平台规范的 Skill 文件并写入工作区。

## 步骤

1. 与用户确认技能名称（kebab-case）、描述和触发时机
2. 生成 frontmatter 和技能正文：

```markdown
---
name: <skill-name>
description: <一句话描述>
layer: personal
status: active
always: false
when_to_use: <触发时机>
max_iterations: 5
---

# <技能标题>

## 步骤

1. 步骤一
2. 步骤二
3. 步骤三
```

3. 调用 `write_file` 写入 `skills/<skill-name>.md`
4. 告知文件路径，建议用户在 Skill 管理面板确认

## 注意事项

- `name` 使用小写短横线（kebab-case）
- `layer` 用户手写技能默认为 `personal`
- Bot 自学沉淀的技能写入 `skills/learned/draft/`，需审批后生效
