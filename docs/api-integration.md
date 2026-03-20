# Knowledge API 接入文档

`knowledge` 负责消费 `warehouse` 中的资产，为外部服务提供已发布知识视图、证据兜底、服务授权与检索审计能力。

## 资产边界

- `warehouse` 是唯一资产中心
- `knowledge` 只读消费 `warehouse` 资产
- 用户上传文件时，只写入 `knowledge` 自己的 app 目录
- `knowledge` 不回写 chunk、embedding 或知识处理结果到 `warehouse`

## 推荐调用顺序

1. 调用 `POST /auth/challenge`
2. 用钱包签名后调用 `POST /auth/verify`
3. 创建 `ServicePrincipal`，拿到服务侧 `api_key`
4. 为目标 KB 创建 `ServiceGrant`
5. 服务侧携带 `X-Service-Api-Key` 调用 `POST /service/search*`
6. 如需调试，管理侧再调用 `search-lab / retrieval-logs / source-governance`

## 核心接口

### 1. 钱包登录

- `POST /auth/challenge`
- `POST /auth/verify`
- `POST /auth/refresh`

### 2. 服务身份与授权

- `POST /service-principals`
- `POST /service-principals/verify`
- `GET /service-principals`
- `POST /kbs/{kb_id}/grants`
- `GET /kbs/{kb_id}/grants`
- `PATCH /kbs/{kb_id}/grants/{grant_id}`

### 3. 服务检索主入口

- `POST /service/search`
- `POST /service/search/formal`
- `POST /service/search/evidence`

这些接口的共同特征：

- 读取对象是“已发布正式知识项 + 证据兜底”，而不是旧 retrieval/context 块
- 结果明确带 `content_health_status` / `source_health_summary`
- 读取哪一个 release 由 `ServiceGrant.release_selection_mode` 决定
- `trace.trace_id`
- `debug`

说明：

- `memory_namespace` 是由上游应用生成的 opaque key，用于隔离不同机器人/工作流的短期记忆
- `knowledge` 不维护 bot 对象，只消费该 namespace 做短期记忆隔离
- `trace.trace_id` 用于串联 retrieval 响应、后续 `POST /memory/ingest` 事件，以及运维排障查询
- `trace.applied_policy` 表示实际生效的 retrieval policy，而不是简单回显请求参数
- `debug` 仅在请求显式开启时返回；它用于解释过滤条件、作用域限制、空结果原因、budget 裁剪和 provider 模式，不属于稳定顶层返回面

### 3. Service Search APIs

- `POST /service/search`
- `POST /service/search/formal`
- `POST /service/search/evidence`
- `GET /service/grants`
- `GET /service/kbs`
- `GET /service/releases/current`

说明：

- `POST /service/search`：默认 `formal_first`，先读已发布正式知识项，不足时补证据项
- `POST /service/search/formal`：只返回已发布正式知识项
- `POST /service/search/evidence`：只返回证据项，适合调试或未形成正式知识项的场景
- 所有服务搜索都要求服务先通过 `ServicePrincipal` 证明身份，再由 `ServiceGrant` 决定可读 KB 与 release 选择策略
- 所有服务搜索结果都显式带 `content_health_status` / `source_health_summary`

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

### 4. Search Lab / Governance APIs

- `POST /kbs/{kb_id}/search-lab/compare`
- `GET /kbs/{kb_id}/retrieval-logs`
- `GET /kbs/{kb_id}/retrieval-logs/{log_id}`
- `GET /kbs/{kb_id}/source-governance`

说明：

- `search-lab/compare` 用于对比 `formal_only / evidence_only / formal_first`
- `retrieval-logs` 用于审计服务消费行为
- `source-governance` 用于查看 `source_missing / stale` 对结果的影响范围

### 5. 兼容记忆接口

- `POST /memory/ingest`
- `GET /memory/ingestions`

`memory` 相关接口仍保留为兼容模块，但不再是当前主产品叙事。

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
  "source_refs": ["/apps/knowledge.yeying.pub/uploads/profile.txt"]
}
```

当前默认行为：

- 自动写入一条 `recent_turn` 短期记忆
- 如果有知识源引用，会写入一条 `summary` 短期记忆
- 命中偏好语义时，会写入长期记忆
- 对完全相同的记忆内容做去重

## Warehouse 相关说明

- `knowledge` 前台会自动尝试绑定当前 `Warehouse App` 目录
- 默认 app 标识为 `knowledge.yeying.pub`，主路径为 `/apps/knowledge.yeying.pub/`
- 上传、浏览、绑定、导入都只允许发生在该 app 目录内
- 默认鉴权模式为 app UCAN；后端仍保留 JWT 绑定能力作为兼容兜底

## 稳定性建议

- 外部服务只依赖公开 API，不直接依赖控制台行为
- 生产接入优先使用 `POST /service/search`
- 需要正式知识项和证据结果拆分时，再按需调用 `POST /service/search/formal` / `POST /service/search/evidence`
- 对 `retrieval_logs`、`source_governance` 和 release 选择策略做留存，便于问题排查
- 遇到索引异常时，优先检查 `/ops/*` 与最近失败任务
- `GET /ops/tasks/failures` 可按 `trace_id` 过滤最近失败任务，用于最小 trace 关联排障
- worker 运行协调已从本地文件锁迁移为数据库租约；若是多实例部署，请确保各实例共享同一数据库

## 调试入口

- 交互式 OpenAPI：`/docs`
- 管理台首页：`/`
- 运维状态：`/ops/overview`、`/ops/stores/health`、`/ops/workers`
- `ops` 接口现在要求携带 `Authorization: Bearer <knowledge_jwt>`，不再对匿名请求开放
