---
name: write-file
description: 创建或更新工作区文件
layer: system
status: active
always: false
when_to_use: 当需要保存信息、生成文档或更新工作区文件时
max_iterations: 3
---

# Write File

## 用途
在 Bot 工作区中创建新文件或更新已有文件内容。

## 步骤

1. 确认目标路径和要写入的内容
2. 调用 `write_file` 工具写入内容
3. 确认写入成功，告知文件路径

## 注意事项

- 只能在当前 Bot 工作区内写文件
- 会覆盖同名文件，写前确认是否需要保留原内容
- 建议在文件头部写入日期和目的注释
- 重要决策或变更应同时追加到日志
