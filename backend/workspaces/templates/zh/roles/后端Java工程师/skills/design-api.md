---
name: design-api
description: 设计 RESTful API 接口，包括路径、请求/响应结构和错误码规范
layer: role
role: 后端Java工程师
status: active
always: false
when_to_use: 当用户要求设计接口、定义 API 规范或说「帮我设计一个接口」时
max_iterations: 4
---

# Design API（Java REST）

## 设计原则

- 资源用名词复数：`/users`, `/orders/{id}`
- 操作用 HTTP 方法：GET 查询 / POST 创建 / PUT 全量更新 / PATCH 部分更新 / DELETE 删除
- 版本化：`/api/v1/...`
- 统一响应结构：`{ code, message, data }`

## 步骤

1. 明确业务资源和操作
2. 定义 URL 路径和 HTTP 方法
3. 设计请求体（DTO）和响应体
4. 定义错误码和错误响应
5. 列出鉴权要求（需要 Token / 公开接口）

## 输出格式

```
## API 设计：[功能模块]

### 接口列表
| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|

### 请求/响应示例

#### POST /api/v1/xxx
**Request Body:**
```json
{
  "field": "value"
}
```

**Response 200:**
```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

### 错误码
| code | 说明 |
|------|------|
```
