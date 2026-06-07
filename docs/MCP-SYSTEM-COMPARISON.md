# MCP (Model Context Protocol) 与工具执行机制横向对比

> 最后更新：2026-06-07
> 状态：设计与选型分析 (基于本地源码审计)

---

## 一、 本地源码审计：智能体框架 MCP 架构横向对比表

| 维度 / 机制 | Claude Code (TypeScript)<br>[claude-code-haha-main](file:///Users/Nuke/claude-code-haha-main) | opencode (TypeScript)<br>[opencode](file:///Users/Nuke/opencode) | gsd-2 (TypeScript/Rust)<br>[gsd-2](file:///Users/Nuke/gsd-2) | openclaw (TypeScript)<br>[openclaw-main](file:///Users/Nuke/openclaw-main) | nuke-ai-collaborator (Python/SQLite)<br>[当前项目](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **协议角色** | **Client**：<br>作为 MCP 客户端运行，拉起本地或远程 MCP 服务器并将工具注入到它的执行环境中。 | **Client**：<br>实现完整的 MCP 客户端管理器，维护多个本地与远程连接状态。 | **双向 (Client & Server)**：<br>1. **Server**：内置 stdio MCP 服务端，向外部客户端（如 Claude Desktop）暴露自身工具。<br>2. **Client**：通过 `mcp-client/manager.ts` 管理客户端配置。 | **Client**：<br>客户端模式，通过 Stdio 包装器连接底层服务器。 | **本地 Plugin (无 MCP)**：<br>非 MCP 架构，目前通过本地 Python 模块动态反射加载工具并生成 OpenAI Schema。 |
| **传输协议实现** | **stdio / SSE**：<br>使用官方 `@modelcontextprotocol/sdk`，提供跨进程环境变量继承（子进程继承 Bash/LSP/MCP 环境变量）。 | **stdio / SSE / StreamableHTTP**：<br>使用 `StdioClientTransport` 启动本地进程，以及 `SSEClientTransport` / `StreamableHTTPClientTransport` 执行远程通信。 | **stdio (StdioServerTransport)**：<br>服务端通过 `StdioServerTransport` 对接 `stdin`/`stdout` 提供进程间同步的 RPC 通信。 | **stdio / SSE**：<br>自定义 `OpenClawStdioClientTransport` 包装标准 I/O 管道，支持 stderr 数据流重定向与格式化日志记录。 | **本地进程反射**：<br>不走 RPC/I/O 管道通信，使用 `importlib.util` 在主进程内直接反射实例化本地 Python 类。 |
| **工具 Schema 转换** | **动态元数据注入**：<br>扫描远程/本地自动注册的 Server 暴露的 tools 列表并直接映射至运行时 prompt 提示词。 | **AI SDK dynamicTool 转换**：<br>通过 `convertMcpTool` 将 MCP Tool 定义转换成 Vercel AI SDK 的 `dynamicTool`，利用 `jsonSchema` 校验参数并异步执行 `callTool`。 | **GSD Tool 映射**：<br>服务端通过 `ListToolsRequestSchema` 自动将 GSD 内置的工具元数据参数（JSON Schema）暴露为 MCP 兼容格式。 | **XML 格式平铺**：<br>将获取的 MCP schema 参数在 Prompt 构建时转换为 XML `<available_skills>` 平铺注入。 | **OpenAI 兼容 Schema**：<br>通过 `get_schemas()` 提取 Python 插件中的参数说明，转换为 OpenAI 格式 function 定义。 |
| **热更新与动态感知** | **生命周期重连**：<br>随主进程生命周期加载，环境变化时重新实例化。 | **事件总线与热更新**：<br>使用 `setNotificationHandler` 监听 `ToolListChangedNotificationSchema`，当工具集变更时拉取新定义并向 Bus 广播 `ToolsChanged` 事件。 | **进程级绑定**：<br>随后台任务拉起，不提供动态运行时 tools 增删。 | **Stderr 订阅监听**：<br>订阅 `transport.stderr.on("data")`，一旦捕获到崩溃或数据变动日志，触发动态重连和警告上报。 | **手动 reload()**：<br>通过 `reload()` 清空 before/after 钩子缓存并重新 `discover()` 扫描加载本地 `.py` 文件。 |
| **子进程退出控制** | **主进程回收**：<br>随主进程生命周期进行释放。 | **未作深层强杀**：<br>仅依赖标准 `client.close()`，子进程可能在后台残留。 | **递归 descendants 强杀**：<br>通过 `pgrep -P` 递归获取子进程树的 PID，并在 close 时发送 `SIGTERM` 强杀所有子进程。 | **管道流感知**：<br>基于 `transport.stderr.on("data")` 辅助感知崩溃。 | **本地同步阻塞**：<br>直接在进程中运行，利用 [win_sandbox.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/executors/plugins/win_sandbox.py) 在 Win 下实施 Job 内存限额。 |
| **认证与 OAuth 机制** | **无/默认 OAuth**：<br>主要继承主应用的安全态与 credentials。 | **McpOAuthProvider**：<br>内置 `McpOAuthProvider`、`McpOAuthCallback` 与 `McpAuth` 服务，支持完整的 OAuth 客户端注册与三方鉴权流程。 | **配置文件配置**：<br>在本地项目根目录 `.mcp.json` 中配置，不涉及复杂的用户三方认证流程。 | **OAuth 凭证解析**：<br>通过配置文件解析配置的敏感 headers 及 url 属性。 | **无**：<br>无鉴权层，工具为本地脚本，运行于当前的进程权限空间下。 |
| **并发锁与竞态消除** | **无/进程级**：<br>依赖异步串行，未针对具体文件资源实施并发安全锁。 | **无**：<br>依赖 Effect-TS 流程并发控制，无具体资源排他锁。 | **AbortSignal 联动**：<br>利用 RPC AbortSignal 控制长时工具（如 bash），超时或主动关闭时中止任务。 | **无**：<br>依赖运行时限制最大并发数。 | **SHA-256 临时锁**：<br>通过 [lifecycle.py](file:///Users/Nuke/claudeFolder/nuke-ai-collaborator/backend/skills/lifecycle.py) 实现精细的文件级互斥，不执行 unlink 以消除 flock 竞态。 |

---

## 二、 关键代码实现深度解构 (Source Code Analysis)

### 1. opencode 的 Effect-TS + AI SDK 桥接机制
In [opencode/packages/opencode/src/mcp/index.ts](file:///Users/Nuke/opencode/packages/opencode/src/mcp/index.ts) 中，客户端将拉取到的 MCP 工具转换为 AI SDK 兼容格式的关键实现：
```typescript
function convertMcpTool(mcpTool: MCPToolDef, client: MCPClient, timeout?: number): Tool {
  const inputSchema = mcpTool.inputSchema
  const schema: JSONSchema7 = {
    ...(inputSchema as JSONSchema7),
    type: "object",
    properties: (inputSchema.properties ?? {}) as JSONSchema7["properties"],
    additionalProperties: false,
  }

  return dynamicTool({
    description: mcpTool.description ?? "",
    inputSchema: jsonSchema(schema),
    execute: async (args: unknown) => {
      return client.callTool(
        {
          name: mcpTool.name,
          arguments: (args || {}) as Record<string, unknown>,
        },
        CallToolResultSchema,
        {
          resetTimeoutOnProgress: true,
          timeout,
        },
      )
    },
  })
}
```
并且在 `layer` 实例化时，设置了工具变更监听器：
```typescript
client.setNotificationHandler(ToolListChangedNotificationSchema, async () => {
  log.info("tools list changed notification received", { server: name })
  const listed = await bridge.promise(defs(name, client, timeout))
  if (!listed) return
  s.defs[name] = listed
  await bridge.promise(bus.publish(ToolsChanged, { server: name }))
})
```

### 2. gsd-2 的 MCP Server 工具映射与中止机制
在 [gsd-2/src/mcp-server.ts](file:///Users/Nuke/gsd-2/src/mcp-server.ts) 中，GSD-2 充当服务器，接收外来 JSON-RPC 请求并将其分发到内置工具。同时通过 `AbortSignal` 确保当客户端取消调用时能及时中止后台的 Bash/Grep 任务：
```typescript
server.setRequestHandler(CallToolRequestSchema, async (request: any, extra: any) => {
  const { name, arguments: args } = request.params
  const tool = toolMap.get(name)
  if (!tool) return { isError: true, content: [{ type: 'text', text: `Unknown tool: ${name}` }] }

  const signal: AbortSignal | undefined = extra?.signal

  try {
    const result = await tool.execute(
      `mcp-${Date.now()}`,
      args ?? {},
      signal
    )
    const content = result.content.map((block: any) => {
      if (block.type === 'text') return { type: 'text', text: block.text ?? '' }
      if (block.type === 'image') return { type: 'image', data: block.data ?? '', mimeType: block.mimeType ?? 'image/png' }
      return { type: 'text', text: JSON.stringify(block) }
    })
    
    const base: Record<string, unknown> = { content }
    if (isPlainObject(result.details)) {
      base.structuredContent = result.details // 将 details 传导至非标准的 structuredContent
    }
    if (result.isError === true) base.isError = true
    return base
  } catch (err: unknown) {
    return { isError: true, content: [{ type: 'text', text: String(err) }] }
  }
})
```

### 3. openclaw 的 Stdio 自定义管道连接与日志重定向
在 [openclaw-main/src/agents/mcp-transport.ts](file:///Users/Nuke/openclaw-main/src/agents/mcp-transport.ts) 中，OpenClaw 通过自定义 Stdio 管道捕获 stderr 的日志流并转换格式投递到系统总线，确保本地 MCP 服务器的不稳定日志能够被完整观测：
```typescript
function attachStderrLogging(serverName: string, transport: OpenClawStdioClientTransport) {
  const stderr = transport.stderr;
  if (!stderr || typeof stderr.on !== "function") return undefined;
  
  const onData = (chunk: Buffer | string) => {
    const message = normalizeOptionalString(String(chunk)) ?? "";
    for (const line of message.split(/\r/\n/)) {
      const trimmed = line.trim();
      if (trimmed) logDebug(`bundle-mcp:${serverName}: ${trimmed}`);
    }
  };
  stderr.on("data", onData);
  return () => stderr.off("data", onData);
}
```

---

## 三、 nuke-ai-collaborator 接入 MCP 的最佳实践演进

基于对上述四个本地框架源码的深入审计，本项目的 MCP 客户端最佳演进架构如下：

```mermaid
graph TD
    LLM[LLM Agent Core] -->|1. Call Tool| Exec[tool_executor.py]
    Exec -->|2. Before Hooks| Hook[Before-Hooks]
    Exec -->|3. File Lock| Lock[file_lock SHA-256]
    Exec -->|4. Dispatch| Adapter[mcp_client_executor.py]
    
    subgraph MCP Adapter Plugin
        Adapter -->|5.1 StdioClientTransport| Stdio[Subprocess stdio]
        Adapter -->|5.2 SSEClientTransport| SSE[Aiohttp SSE Client]
    end
    
    Stdio -->|6. JSON-RPC| LocalMCP[Local MCP Server]
    SSE -->|6. JSON-RPC| RemoteMCP[Remote MCP Server]
```

### 关键实现策略

1. **命名空间隔离 (Namespace Isolation)**：
   将获取的 MCP 工具统一在 `tool_executor` 中注册为 `mcp::[server_name]::[tool_name]`，完美避开本地插件（如 `read_file`）的冲突问题。
2. **异步进程生命周期管理 (Subprocess Handoff)**：
   借鉴 `opencode` 和 `gsd-2`，对于 Stdio 启动的本地 MCP 进程，在其异常退出或热重载时，通过 Python 的 `psutil` 追踪当前主进程启动的所有子进程树并发送 `SIGTERM`，彻底防止进程残留和孤儿进程出现。
3. **参数强类型与别名容错的结合**：
   在 `mcp_client_executor` 执行具体 MCP RPC 前，继续复用本项目的 `_normalize_arg_aliases` 机制，先纠正大模型产生的不规范参数名，再向 MCP 服务器发送请求，确保兼容性。
