---
name: design-api
description: 设计 Python 后端 RESTful API 接口，含路径、请求/响应结构和 FastAPI/Flask 代码示例
layer: role
role: 后端Python专家
status: active
always: false
when_to_use: 当用户要求设计 Python 后端接口或说「帮我设计一个 API」时
max_iterations: 4
---

# Design API（Python）

## 设计原则

- 资源用名词复数：`/users`, `/messages/{id}`
- 操作用 HTTP 方法：GET / POST / PUT / PATCH / DELETE
- 统一响应结构：`{ "code": 0, "message": "ok", "data": ... }`
- Pydantic 模型做请求/响应校验（FastAPI）
- 错误用 HTTPException，状态码语义化

## 步骤

1. 明确业务资源和操作
2. 定义 URL 路径和 HTTP 方法
3. 用 Pydantic 定义请求/响应 Schema
4. 定义错误码和异常处理
5. 写出路由函数骨架

## 输出格式

```
## API 设计：[功能模块]

### 接口列表
| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|

### Schema 定义
```python
class CreateXxxRequest(BaseModel):
    field: str

class XxxResponse(BaseModel):
    id: int
    field: str
```

### 路由骨架
```python
@router.post("/xxx", response_model=XxxResponse)
async def create_xxx(body: CreateXxxRequest):
    ...
```

### 错误码
| HTTP状态码 | 说明 |
|------------|------|
```
