---
name: read-file
description: 读取工作区文件内容
layer: system
status: active
always: false
when_to_use: 当需要读取工作区中的文件内容时
max_iterations: 3
---

# Read File

## 用途
读取 Bot 工作区中的指定文件，并返回其内容。

## 步骤

1. 确认目标文件路径（相对于工作区根目录）
2. 调用 `read_file` 工具读取文件内容
3. 返回文件内容，若文件不存在则说明原因

## 注意事项

- 只能读取当前 Bot 工作区内的文件
- 路径使用相对路径，禁止使用 `../` 跨越工作区边界
- 若文件内容很长，可按需摘要后返回
