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
3. 持 `Authorization: Bearer <knowledge_jwt>` 调用检索接口
4. bot / chat 生成最终回答
5. 调用 `POST /memory/ingest` 显式沉淀本轮记忆
6. 后续继续调用 `POST /retrieval-context` 获取知识块 + 记忆块

## 核心接口

### 1. 钱包登录

- `POST /auth/challenge`
- `POST /auth/verify`
- `POST /auth/refresh`

### 2. 知识库检索

- `POST /kbs/{kb_id}/search`
- `POST /retrieval-context`

`/retrieval-context` 返回：

- `short_term_memory_blocks`
- `long_term_memory_blocks`
- `kb_blocks`
- `source_refs`
- `scores`
- `trace_id`

### 3. 自动记忆沉淀

- `POST /memory/ingest`
- `GET /memory/ingestions`

推荐在 bot / chat 生成最终回复后调用 `POST /memory/ingest`。

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
- 生产接入优先使用 `retrieval-context + memory/ingest` 组合
- 对 `trace_id`、`source_refs` 做日志留存，便于问题排查
- 遇到索引异常时，优先检查 `/ops/*` 与最近失败任务

## 调试入口

- 交互式 OpenAPI：`/docs`
- 管理台首页：`/`
- 运维状态：`/ops/overview`、`/ops/stores/health`、`/ops/workers`
