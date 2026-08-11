# 飞书与个人微信 Channel 部署验收

本文对应当前小公司/独立项目组版本。目标是单个 Docker 服务内运行 Supervisor、Group/Worker 和原生 Channel Connector；不要求云端多租户，也不要求为 Connector 增加额外容器沙箱。

当前支持的平台只有：

- 飞书/Lark Bot：Webhook 入站，Open API 出站；
- 个人微信 iLink Bot：长轮询入站，iLink API 出站。

## 1. 配置原则

Channel 配置只保存环境变量名，不把 Secret 写进 JSON：

```bash
NUKE_CHANNEL_PLATFORMS_JSON='[{"type":"feishu","channel_instance_id":"feishu:prod","app_id_env":"FEISHU_APP_ID","app_secret_env":"FEISHU_APP_SECRET","verification_token_env":"FEISHU_VERIFY_TOKEN","encrypt_key_env":"FEISHU_ENCRYPT_KEY"},{"type":"wechat_ilink","channel_instance_id":"wechat:personal","bot_id_env":"WECHAT_ILINK_BOT_ID","bot_token_env":"WECHAT_ILINK_BOT_TOKEN"}]'
```

对应 Secret 环境变量：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_VERIFY_TOKEN=xxx
FEISHU_ENCRYPT_KEY=xxx                 # 若启用飞书加密回调则填写

WECHAT_ILINK_BOT_ID=xxx
WECHAT_ILINK_BOT_TOKEN=xxx
```

Docker Compose 已提供这些变量的传递入口。生产环境应通过部署平台的 Secret 注入机制提供值；不要把含 Secret 的 `.env` 提交到 Git，也不要把 Secret 放进 `NUKE_CHANNEL_PLATFORMS_JSON`。

服务启动后会校验配置。缺少 active Binding 对应的 Connector、重复的实例 ID 或必需 Secret 时，服务应启动失败或拒绝审批，不会静默积压消息。

## 2. 飞书配置

在飞书开放平台创建企业自建应用并启用 Bot 能力，记录 App ID 和 App Secret。

配置事件订阅时，将请求地址设置为：

```text
https://<NUKE_HOST>/api/channels/webhooks/feishu/feishu:prod
```

其中 `feishu:prod` 必须与 `NUKE_CHANNEL_PLATFORMS_JSON` 中的 `channel_instance_id` 一致。事件订阅至少启用消息接收事件 `im.message.receive_v1`，并为应用配置接收和发送消息所需的权限。飞书控制台给出的权限名称可能随版本和租户类型变化，以控制台当前显示为准。

首次保存回调地址时，服务会处理飞书 URL verification challenge。若启用加密回调，将 Encrypt Key 填入 `FEISHU_ENCRYPT_KEY`；Verification Token 填入 `FEISHU_VERIFY_TOKEN`。服务会校验原始请求、Verification Token、时间戳和签名，并拒绝过期请求、重复事件以及自己的 Bot 回声。

本版本已覆盖文本、post、图片/文件/音频/视频的消息元数据解析，以及文本和富文本出站。二进制附件下载、上传并登记为 Group Artifact 仍是独立的未完成项，不能把附件 metadata 当成完整附件能力。

## 3. 个人微信配置

个人微信使用 iLink 登录流程，不依赖企业微信或公众号后台。

先启动服务并由 Operator 调用：

```text
POST /api/channels/wechat/login/qrcode
```

响应中的二维码信息交给操作者扫码，然后使用响应里的 `qrcode_id` 轮询：

```text
POST /api/channels/wechat/login/status
Content-Type: application/json

{"qrcode_id":"<qrcode_id>"}
```

轮询到登录成功后，将响应返回的 `bot_id` 和 `bot_token` 写入部署环境的 `WECHAT_ILINK_BOT_ID`、`WECHAT_ILINK_BOT_TOKEN`，并重启服务使 Supervisor 注册 `wechat:personal`。登录状态、游标和回复上下文由服务持久化；上下文和媒体引用加密保存，Bot token 不落 SQLite。

个人微信入站采用 iLink durable cursor，服务重启后从持久化游标继续拉取。回复需要最近一次入站消息的上下文，当前上下文有效期按 Connector 约定管理；没有可用上下文时会进入明确失败/人工处理路径，不盲目发送。

## 4. 将 Channel 接入 Group

Channel Connector 独立运行，只有 Binding 审批为 active 后才与 Group 通信。正式流程由对应 Group Owner 完成：

1. `POST /api/channels/groups/{group_id}/bindings` 创建 `configured` Binding。
2. `POST /api/channels/groups/{group_id}/bindings/{binding_id}/submit` 提交审批，进入 `pending_approval`。
3. `POST /api/channels/groups/{group_id}/bindings/{binding_id}/approve` 创建 Integration Member 并激活 Binding。

创建请求示例：

```json
{
  "channel_instance_id": "feishu:prod",
  "external_tenant_id": "tenant_xxx",
  "external_conversation_id": "oc_xxx",
  "default_bot_id": 42,
  "allowed_bot_ids": [42],
  "mention_required": false,
  "inbound_policy": {},
  "outbound_policy": {}
}
```

个人微信通常将 `external_tenant_id` 设置为已登录的 Bot ID，将 `external_conversation_id` 设置为允许通信的个人用户或会话 ID。飞书则分别使用 tenant key 和 chat ID。两个字段必须与实际入站消息精确匹配，不能用通配符代替 Group 隔离。

审批请求示例：

```json
{
  "display_name": "个人微信 Channel",
  "avatar": "",
  "metadata": {"purpose": "project-notifications"}
}
```

暂停、恢复和撤销由 Owner 操作：

```text
POST /api/channels/groups/{group_id}/bindings/{binding_id}/transition
{"target":"suspended"}

POST /api/channels/groups/{group_id}/bindings/{binding_id}/transition
{"target":"active"}

POST /api/channels/groups/{group_id}/bindings/{binding_id}/transition
{"target":"revoked"}
```

暂停或撤销后，旧 Router 不会继续使用缓存的 active Binding；跨 Group 的查询和路由必须返回拒绝。

## 5. 运行检查

Operator 可以检查：

```text
GET /api/channels/health
GET /health/liveness
GET /health/readiness
```

Channel health 应同时观察 Dispatcher、platform runtime、pending/retrying 数量、最老消息年龄、dead-letter 数量和最后成功投递时间。常用控制接口：

```text
POST /api/channels/{channel_instance_id}/pause
POST /api/channels/{channel_instance_id}/resume
POST /api/channels/replay
{"idempotency_key":"<dead-letter-key>"}
```

外部 HTTP 请求遇到“请求是否已经到达平台”无法判断的网络故障时，投递会进入 ambiguous dead-letter，不自动重发，避免个人微信或飞书收到重复通知。Operator 需要先在平台侧确认，再决定是否人工 replay。

## 6. 测试环境 Go/No-Go

进入测试环境前，至少完成以下检查：

- [ ] `docker compose config` 能解析 Channel JSON 和 Secret 环境变量；
- [ ] 新数据库启动后 Channel、Binding、Integration Member schema 全部存在；
- [ ] 飞书 URL verification 成功；
- [ ] 飞书真实群聊消息只路由到目标 Group 和允许的 Bot；
- [ ] 飞书真实 workflow 通知能发送并可在平台侧确认；
- [ ] 个人微信扫码登录成功，重启后仍能继续轮询；
- [ ] 个人微信真实消息只进入目标 Group；
- [ ] 个人微信真实回复包含正确 context，长文本按限制分段；
- [ ] 相同入站事件重复发送只触发一次 Bot 调度；
- [ ] 暂停/撤销 Binding 后，入站和出站均被拒绝；
- [ ] 注入 429、5xx、超时、Worker/服务重启后，消息状态、审计和 dead-letter 可解释；
- [ ] 从日志、审计和数据库中确认没有 token、Authorization、context token 或原始敏感错误明文落库；
- [ ] 记录真实账号 smoke 结果、失败率、延迟、限流响应和人工 replay 结果。

上述清单全部通过后，才可以把 C2、C6、C7、C8 从 Gate 4 提升为 Gate 5 Deployable。当前代码已有本地双平台 E2E，但没有伪造真实账号通过的结果，因此路线图保持 Gate 4 是有意的。

## 7. 当前明确不在范围内

- Slack；
- 企业微信；
- 云平台多租户和跨租户管理；
- Connector 的 OS/container 沙箱；
- 二进制附件下载、上传、Artifact 权限和完整 Artifact 生命周期。

这些不是当前单服务 Docker 版本的隐含“已完成”项，应在对应实现和验收完成后单独提升 Gate。
