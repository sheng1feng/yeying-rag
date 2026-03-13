# Bot / Chat 知识库重构 PRD

## 1. 背景与问题

当前 `knowledge` 已具备基础的资产导入、索引、检索、记忆沉淀与控制台能力，但对 bot/chat 的对接形态仍偏“控制台驱动 + 单体接口”：

- 检索入口主要是 `POST /kbs/{kb_id}/search` 与 `POST /retrieval-context`
- `/retrieval-context` 同时承担知识召回、记忆召回与上下文拼装，职责偏重
- bot/chat 场景需要传入的会话上下文、检索范围、短期记忆隔离 namespace、`token_budget`、`filters` 等关键参数尚未纳入统一接口
- 记忆召回与知识召回耦合，且长期记忆的召回策略不够可解释

这使得当前服务可用，但还不够适合作为 bot/chat 的稳定知识能力底座。

## 2. 产品目标

将 `knowledge` 重构为面向 bot/chat 的知识检索与上下文编排服务，核心目标如下：

1. 提供统一、稳定、低耦合的上下文调用入口
2. 同时提供分层能力接口，支持编排层按需组合
3. 明确 knowledge、warehouse、memory、bot orchestration 的职责边界
4. 让检索结果、记忆命中和上下文拼装过程可解释、可调试
5. 保持与现有接口兼容，支持渐进迁移

## 3. 非目标

本阶段不做以下事项：

- 不在 `knowledge` 内部做最终答案生成
- 不在 `knowledge` 内部承载 agent workflow 编排
- 不在 `knowledge` 内部维护 bot/robot 注册与管理模型
- 不引入高成本的评测平台或复杂 memory graph
- 不移除现有 `POST /retrieval-context`，仅做兼容保留

## 4. 用户角色

### 4.1 管理端

- 创建和维护知识库
- 绑定资产路径、发起导入/重建/删除任务
- 查看任务、文档、切片、失败和存储状态

### 4.2 bot/chat 调用方

- 传入会话、memory namespace、检索范围、场景与检索策略
- 调用统一入口或分层接口获取证据包
- 在生成最终回复后显式调用记忆沉淀接口

### 4.3 最终用户

- 通过 bot/chat 获得基于个人知识库或应用知识库的回答
- 希望知识引用可信、上下文稳定、偏好能被正确记住

## 5. 关键使用场景

1. **单 bot 单知识库问答**
   - bot 请求知识证据与记忆摘要
   - orchestration 层根据返回内容构造 prompt

2. **多知识库联合检索**
   - 一个 bot 同时查询多个 KB
   - 由 knowledge 返回统一排序后的证据命中与来源列表

3. **多会话记忆协同**
   - 同一用户在当前会话使用短期记忆
   - 结合全局或 KB 级长期记忆构造更贴近用户偏好的上下文

4. **调试与排障**
   - 调用方通过 debug 信息定位“为什么命中了这些 chunk / memory”

## 6. 核心能力列表

- 资产接入与绑定
- 文档解析与切片
- 向量索引与知识检索
- 记忆召回
- 上下文拼装
- 统一 context 调用入口
- 分层 retrieval APIs
- 任务治理与运维观测

## 7. 详细功能需求

### 7.1 统一 context 调用入口

提供一个统一入口，允许 bot/chat 传入：

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

返回内容应至少包括：

- `knowledge.*`
- `memory.*`
- `context.*`
- `trace.*`
- `debug`

### 7.2 分层能力接口

对 bot 编排层提供以下分层接口：

- `search`
- `retrieve`
- `recall_memory`
- `assemble_context`
- `generate_retrieval_context`

### 7.3 兼容性

- 保留现有 `POST /retrieval-context`
- 旧接口内部复用新分层逻辑
- 新旧接口可并存一段时间

## 8. 接口需求

### 8.1 新增接口

- `POST /retrieval/search`
- `POST /retrieval/retrieve`
- `POST /retrieval/recall-memory`
- `POST /retrieval/assemble-context`
- `POST /retrieval/generate-context`
- `POST /retrieval/context`
- `POST /bot/retrieval-context`

### 8.2 兼容接口

- `POST /kbs/{kb_id}/search`
- `POST /retrieval-context`

## 9. 非功能需求

### 9.1 性能

- 本地 mock/db 模式可完成端到端开发与联调
- 生产模式支持向量库承载主要相似度检索

### 9.2 可用性

- 旧接口兼容
- 失败任务可重试
- 检索与上下文接口可输出 trace/debug 信息

### 9.3 可维护性

- 分层接口职责明确
- 检索、记忆、上下文拼装服务解耦

### 9.4 安全性

- `warehouse` 继续作为唯一原始资产中心
- `knowledge` 只处理派生索引与记忆数据
- 用户隔离继续以登录钱包为主边界

### 9.5 可观测性

- 所有新接口返回 `trace_id`
- debug 模式输出过滤条件、策略与召回说明
- `trace_id` 可用于关联 retrieval、memory ingest 与最小 ops 排障链路
- legacy 与标准入口保持语义一致，调试细节只在显式开启时返回

## 10. 成功指标

- bot/chat 接入方可以只依赖公开 API，而不依赖控制台
- 新分层接口能覆盖 search / memory / context 的独立调用场景
- 旧 `retrieval-context` 接口保持可用
- 新接口具备测试覆盖和文档说明

## 11. 版本范围

### 本次版本包含

- 新分层 retrieval APIs
- 新统一 bot 入口
- 旧接口兼容复用
- PRD / 技术方案 / 接入文档更新

### 本次版本不包含

- rerank provider 正式启用
- 复杂多租户模型重构
- 多实例分布式任务队列改造

## 12. 风险与依赖

### 风险

- 当前记忆召回仍属于轻量策略，不是 embedding-based memory search
- `source_scope` 当前仅支持精确路径过滤，不是完整授权模型
- mock/db 与生产向量库的行为仍可能有细节差异

### 依赖

- 现有 `warehouse` 资产边界继续成立
- 现有任务系统继续负责导入与重建
- 上层 bot/orchestration 负责最终 prompt 和模型调用
