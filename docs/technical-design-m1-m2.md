# M1 / M2 技术方案

## 1. 目标架构与现有架构差异

### 当前已有

- `warehouse` 资产浏览/上传与 token 绑定
- 文档导入、切片、embedding、向量检索
- 记忆 CRUD 与显式 `memory/ingest`
- 旧版 `retrieval-context` 单体接口

### 建议保留

- `warehouse` 作为唯一原始资产中心
- `knowledge` 内部保存派生索引与记忆
- 显式记忆沉淀的调用模式
- mock / db / weaviate / openai-compatible 的 provider 抽象方向

### 建议重构

- 将检索、记忆召回、上下文拼装拆成分层服务
- 旧 `retrieval-context` 改为兼容壳，内部调用新服务
- 长期记忆召回范围收敛到“全局 + 请求 KB”

### 建议新增

- `POST /retrieval/search`
- `POST /retrieval/retrieve`
- `POST /retrieval/recall-memory`
- `POST /retrieval/assemble-context`
- `POST /retrieval/generate-context`
- `POST /retrieval/context`
- `POST /bot/retrieval-context`

## 2. 总体架构图（文字）

### 写路径

1. `warehouse` 提供原始文件
2. `knowledge` 任务系统执行导入
3. parser + chunker 生成 chunks
4. embedding provider 生成向量
5. vector store + DB 保存索引

### 读路径

1. bot/chat 或应用编排层调用 `POST /retrieval/context`
2. retrieval service 执行 knowledge retrieve
3. retrieval service 执行 memory recall
4. retrieval service 执行 context assembly
5. orchestration 层消费结构化证据包，自行调用模型生成答案

## 3. 核心模块职责

### `warehouse`

- 资产源头
- personal / app 路径访问控制
- 不保存 knowledge 的派生索引结果

### `ingestion`

- 导入任务
- 文档解析
- 切片
- embedding
- index 写入

### `retrieval`

- search：原始向量命中
- retrieve：带策略与来源汇总的知识检索
- recall_memory：短期/长期记忆召回
- assemble_context：面向 prompt 的上下文组装
- generate_context：统一组合入口

### `memory`

- 显式 CRUD
- 显式 ingest
- 不与知识检索索引混成同一存储模型

## 4. 关键数据流

### 4.1 search

- 输入：`query + kb_ids + filters`
- 输出：chunk hits

### 4.2 retrieve

- 输入：`query + kb_ids + retrieval_policy`
- 输出：knowledge hits + source refs + applied policy

### 4.3 recall_memory

- 输入：`query + session_id + kb_ids`
- 输出：short-term hits + long-term hits
- 当前策略：`lexical-overlap + recency + long-term-score`

### 4.4 assemble_context

- 输入：knowledge hits + memory hits + token/max char budget
- 输出：`context_sections + assembled_context`

### 4.5 generate_context

- 串联 retrieve / recall_memory / assemble_context
- 输出统一的 bot-facing 证据包

## 5. API 设计草案

### `POST /retrieval/search`

- 作用：原始知识命中查询
- 典型场景：检索调试、召回对比

### `POST /retrieval/retrieve`

- 作用：知识检索 + 策略回显 + 来源汇总
- 典型场景：上层编排层单独使用知识证据

### `POST /retrieval/recall-memory`

- 作用：召回短期/长期记忆
- 当前限制：仍然是轻量记忆检索，不是向量 memory search

### `POST /retrieval/assemble-context`

- 作用：将命中的 knowledge/memory 组装成可直接用于 prompt 的上下文段落

### 当前分支实际公开检索入口

- `POST /service/search`
- `POST /service/search/formal`
- `POST /service/search/evidence`
- `POST /kbs/{kb_id}/search-lab/compare`

说明：

- 当前分支已经移除旧的 `/retrieval/*`、`/retrieval/context`、`/bot/retrieval-context` 和 `/retrieval-context`
- 当前公开叙事以“已发布正式知识项 + 证据兜底 + service grant” 为主

## 6. 关键对象模型建议

### 当前已有

- `KnowledgeBase`
- `SourceBinding`
- `ImportedDocument`
- `ImportedChunk`
- `EmbeddingRecord`
- `LongTermMemory`
- `ShortTermMemory`

### 本轮不改表，仅优化读写语义

- 长期记忆召回时限定为：
  - `kb_id is null`
  - 或 `kb_id in request.kb_ids`
- 短期记忆召回时限定为：
  - 当前 `memory_namespace + session_id`

## 7. 测试策略

### 已新增的重点覆盖

- `service search / search-lab / retrieval logs` 主链路联调
- source / asset / evidence / item / release / grant 的关键读写路径
- 记忆召回的 session / kb 作用域约束

### 后续应补

- Weaviate 模式过滤一致性
- OpenAI-compatible embedding 模式
- 多用户权限隔离
- 大规模向量数据下的性能回归

## 8. 迁移策略

### 当前迁移状态

1. legacy retrieval/context 路由已在当前分支下线
2. 当前稳定入口是 `service search` 与 `search-lab`
3. 兼容层保留在 `memory` 与旧 document ingest/task 流程，不再保留 retrieval 路由兼容壳

## 9. 风险控制

- 不修改现有数据库结构，降低迁移风险
- 旧接口复用新逻辑，避免双份实现漂移
- 当前 `service search` 不把 `trace/debug` 作为稳定返回契约
- `source_scope` 先做精确路径过滤，避免引入虚假的权限承诺

## 10. 为什么这些抽象是值得的

### 分层 retrieval pipeline

它解决的是当前单体 `retrieval-context` 不可组合、不可调试的问题，而不是人为增加层级。

### memory 与 knowledge 解耦

它解决的是“记忆只是最新几条列表”导致的语义混乱；即使当前记忆召回仍较轻量，也先把职责边界拉清楚。

### observability-first

当前分支的最小可解释面仍然是：

- `retrieval_logs`
- `search-lab/compare`
- `source-governance`

如果后续恢复 `trace/debug`，应作为单独的公开契约迭代，而不是默认假设它已经存在。
