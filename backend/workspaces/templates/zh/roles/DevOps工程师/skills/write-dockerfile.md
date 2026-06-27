---
name: write-dockerfile
description: 为应用编写 Dockerfile，优化镜像大小和构建层缓存
layer: role
role: DevOps工程师
status: active
always: false
when_to_use: 当用户要求编写 Dockerfile 或容器化应用时
max_iterations: 3
---

# Write Dockerfile

## 编写原则

- **多阶段构建** — 构建产物和运行镜像分离，减小最终镜像体积
- **层缓存优化** — 变化少的指令（依赖安装）放前面，变化多的（COPY 源码）放后面
- **最小权限** — 使用非 root 用户运行
- **明确版本** — 基础镜像指定 digest 或具体版本，不用 `latest`

## 步骤

1. 确认语言/框架和运行时需求
2. 选择基础镜像（优先 alpine 或 distroless）
3. 编写构建阶段（安装依赖、编译）
4. 编写运行阶段（只复制产物）
5. 设置非 root 用户、暴露端口、ENTRYPOINT

## 输出格式

直接输出 Dockerfile 内容，关键决策点加注释说明原因。

附：推荐的 `.dockerignore` 内容。
