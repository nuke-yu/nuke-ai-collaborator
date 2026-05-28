---
name: write-ci-config
description: 为项目编写 CI/CD 配置文件（GitHub Actions / GitLab CI / Jenkins）
layer: role
role: DevOps工程师
status: active
always: false
when_to_use: 当用户要求编写或优化 CI/CD 流水线配置时
max_iterations: 4
---

# Write CI Config

## 编写前确认

- **平台**：GitHub Actions / GitLab CI / Jenkins / 其他
- **触发条件**：push / PR / tag / 定时
- **流水线阶段**：lint → test → build → deploy
- **部署目标**：云服务商、K8s、服务器

## 设计原则

- **快速失败** — lint 和单测放最前，失败即停
- **缓存依赖** — 利用平台缓存机制（actions/cache、GitLab cache）避免重复下载
- **环境隔离** — 不同环境（dev/staging/prod）用不同 job 或 workflow
- **最小权限** — CI 使用专用 Service Account，只授予必要权限
- **密钥管理** — 敏感信息用平台 Secrets，不硬编码

## 步骤

1. 确认平台和触发条件
2. 定义 stages/jobs
3. 配置依赖缓存
4. 编写各阶段脚本
5. 配置部署阶段（含审批/手动触发机制）

## 输出格式

直接输出配置文件内容（YAML），关键配置加注释说明。
