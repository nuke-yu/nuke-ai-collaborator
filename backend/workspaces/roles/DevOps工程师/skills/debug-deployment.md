---
name: debug-deployment
description: 诊断容器/K8s/CI 部署失败问题，定位根因并给出修复步骤
layer: role
role: DevOps工程师
status: active
always: false
when_to_use: 当用户描述部署失败、Pod 崩溃、服务不可达或 CI 流水线失败时
max_iterations: 4
---

# Debug Deployment

## 诊断框架

### 1. 定位故障层

| 层 | 检查命令 | 常见问题 |
|----|----------|----------|
| 镜像构建 | `docker build` 日志 | 依赖下载失败、语法错误 |
| 容器启动 | `docker logs` / `kubectl logs` | 配置缺失、端口冲突 |
| 健康检查 | `kubectl describe pod` | 超时、端口不匹配 |
| 网络 | `kubectl exec` curl 测试 | Service selector 不匹配、NetworkPolicy 拦截 |
| 存储 | PVC 状态 | StorageClass 不存在、权限问题 |

### 2. K8s Pod 状态速查

| 状态 | 含义 | 排查方向 |
|------|------|----------|
| CrashLoopBackOff | 容器反复崩溃 | `kubectl logs --previous` 看上次崩溃日志 |
| ImagePullBackOff | 镜像拉取失败 | 检查镜像名、registry 权限 |
| Pending | 无法调度 | 资源不足、Node 亲和性、Taint |
| OOMKilled | 内存超限 | 调整 `resources.limits.memory` |

## 输出格式

```
### 故障定位
[层级 + 根因一句话]

### 诊断步骤
1. 执行命令：`xxx`
2. 观察输出：...

### 修复方案
[具体操作或配置变更]

### 验证方法
[如何确认修复成功]
```
