# Knowledge API 接入文档

`knowledge` 负责消费 `warehouse` 中的资产，为 bot / chat 类服务提供检索、上下文与记忆沉淀能力。

## 资产边界

- `warehouse` 是唯一资产中心
- `knowledge` 只读消费 `warehouse` 资产
- 用户上传文件时，仍然是上传到 `warehouse personal`
- `knowledge` 不回写 chunk、embedding 或知识处理结果到 `warehouse`

## 推荐调用顺序

1. 调用 `POST /auth/challenge`
2. 用钱包签名后调用 `POST /auth/verify`
3. 持 `Authorization: Bearer <knowledge_jwt>` 调用中立的 context / retrieval 接口
4. bot / chat 生成最终回答
5. 调用 `POST /memory/ingest` 显式沉淀本轮记忆
6. 后续继续调用 `POST /retrieval/context` 或兼容接口 `POST /retrieval-context`

## 核心接口

### 1. 钱包登录

- `POST /auth/challenge`
- `POST /auth/verify`
- `POST /auth/refresh`

### 2. 中立上下文接口

- `POST /retrieval/context`

推荐新的 bot/chat/应用编排层优先使用该接口。

该接口采用“钱包用户 + 会话上下文 + 检索范围 + 检索策略”的中立模型，支持传入：

- `conversation.session_id`
- `conversation.conversation_id`
- `conversation.memory_namespace`
- `conversation.scene`
- `conversation.intent`
- `scope.kb_ids`
- `scope.source_scope`
- `scope.filters`
- `policy.top_k`
- `policy.memory_top_k`
- `policy.token_budget`
- `caller.app_name`
- `caller.request_id`
- `debug`

该接口返回：

- `knowledge.hits`
- `knowledge.source_refs`
- `memory.short_term_hits`
- `memory.long_term_hits`
- `context.sections`
- `context.assembled_context`
- `trace.applied_policy`
- `trace.trace_id`
- `debug`

说明：

- `memory_namespace` 是由上游应用生成的 opaque key，用于隔离不同机器人/工作流的短期记忆
- `knowledge` 不维护 bot 对象，只消费该 namespace 做短期记忆隔离
- `trace.trace_id` 用于串联 retrieval 响应、后续 `POST /memory/ingest` 事件，以及运维排障查询
- `trace.applied_policy` 表示实际生效的 retrieval policy，而不是简单回显请求参数
- `debug` 仅在请求显式开启时返回；它用于解释过滤条件、作用域限制、空结果原因、budget 裁剪和 provider 模式，不属于稳定顶层返回面

### 3. 分层 retrieval APIs

- `POST /retrieval/search`
- `POST /retrieval/retrieve`
- `POST /retrieval/recall-memory`
- `POST /retrieval/assemble-context`
- `POST /retrieval/generate-context`

说明：

- `POST /retrieval/search`：原始知识命中查询，只返回 hits / trace / debug，不负责 `source_refs` 汇总或 policy 回显
- `POST /retrieval/retrieve`：知识检索能力面，返回 knowledge hits、`source_refs` 与 `applied_policy`，但不做 memory recall 或 context assembly
- `POST /retrieval/recall-memory`：独立记忆召回，只处理 session / `memory_namespace` / kb 范围内的短期与长期记忆
- `POST /retrieval/assemble-context`：只做上下文组装，只消费显式传入的 knowledge / memory hits 与 budget
- `POST /retrieval/generate-context`：组合型 retrieval API，内部串联 retrieve / recall-memory / assemble-context

当前 provider 一致性口径：

- 已验证 `db` 与 `weaviate` 模式在过滤语义上保持一致，包括 `source_scope`、`source_kinds`、`document_ids`
- 已验证 `mock` 与 `openai_compatible` embedding provider 的调用契约与返回形状
- 不承诺不同向量后端的相似度分值、排序细节或召回分布完全一致

### 3.1 绑定源驱动的任务接口

- `POST /kbs/{kb_id}/tasks/import-from-bindings`
- `POST /kbs/{kb_id}/tasks/reindex-from-bindings`
- `POST /kbs/{kb_id}/tasks/delete-from-bindings`
- `PATCH /kbs/{kb_id}/bindings/{binding_id}`
- `GET /kbs/{kb_id}/workbench`

说明：

- 默认使用当前知识库下全部 `enabled=true` 的绑定源
- 也可通过 `binding_ids` 精确指定要处理的绑定
- 返回的 `stats_json.created_from = "bindings"`，可用于区分这类任务来源
- `PATCH /kbs/{kb_id}/bindings/{binding_id}` 可启用/停用某个绑定源
- `GET /kbs/{kb_id}/workbench` 返回绑定源同步状态、最近任务与知识库工作台摘要

### 4. 兼容检索接口

- `POST /kbs/{kb_id}/search`
- `POST /bot/retrieval-context`
- `POST /retrieval-context`

`/retrieval-context` 返回：

- `short_term_memory_blocks`
- `long_term_memory_blocks`
- `kb_blocks`
- `source_refs`
- `scores`
- `trace_id`

`POST /retrieval-context` 目前仍保留，用于兼容旧接入方；内部已经复用新的分层 retrieval pipeline。

兼容边界：

- legacy 与标准入口要求“语义一致、形状不同”
- `POST /retrieval/context` 继续作为新接入默认主入口
- `POST /retrieval-context` 不再演化新的独立编排逻辑

### 5. 自动记忆沉淀

- `POST /memory/ingest`
- `GET /memory/ingestions`

推荐在 bot / chat 生成最终回复后调用 `POST /memory/ingest`。

说明：

- `POST /memory/ingest` 推荐透传上一次 retrieval 响应中的 `trace_id`
- `GET /memory/ingestions` 可按 `trace_id` 过滤，用于排查 retrieval → memory ingest 链路

示例：

```json
{
  "session_id": "chat-session-1",
  "kb_id": 1,
  "query": "用户希望以后回答更简洁",
  "answer": "好的，后续我会保持简洁回答。",
  "source": "bot",
  "trace_id": "trace-demo",
  "source_refs": ["/personal/uploads/profile.txt"]
}
```

当前默认行为：

- 自动写入一条 `recent_turn` 短期记忆
- 如果有知识源引用，会写入一条 `summary` 短期记忆
- 命中偏好语义时，会写入长期记忆
- 对完全相同的记忆内容做去重

## Warehouse 相关说明

- `knowledge` 前台会自动尝试绑定 `warehouse personal`
- 对 `personal` 的实际访问目前主路径仍然是 JWT
- UCAN 仍保留在后端能力中，用于后续更细粒度 app 访问

## 稳定性建议

- 外部服务只依赖公开 API，不直接依赖控制台行为
- 生产接入优先使用 `POST /bot/retrieval-context + POST /memory/ingest` 组合
- 生产接入优先使用 `POST /retrieval/context + POST /memory/ingest` 组合
- 需要细粒度编排时，再按需调用 `POST /retrieval/*`
- 对 `trace_id`、`source_refs` 做日志留存，便于问题排查
- 遇到索引异常时，优先检查 `/ops/*` 与最近失败任务
- `GET /ops/tasks/failures` 可按 `trace_id` 过滤最近失败任务，用于最小 trace 关联排障
- worker 运行协调已从本地文件锁迁移为数据库租约；若是多实例部署，请确保各实例共享同一数据库

## 调试入口

- 交互式 OpenAPI：`/docs`
- 管理台首页：`/`
- 运维状态：`/ops/overview`、`/ops/stores/health`、`/ops/workers`
- `ops` 接口现在要求携带 `Authorization: Bearer <knowledge_jwt>`，不再对匿名请求开放
