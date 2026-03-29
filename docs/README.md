# knowledge 文档索引

## 当前状态

- 当前仓库只存在顶层 `docs/`，还没有稳定的 `knowledge/docs/` 目录。
- `warehouse` 控制面鉴权已经切到“手工导入 WebDAV `ak/sk` 凭证”的主模型。
- 旧 `/warehouse/auth/*` 绑定接口已经删除；如果看到旧描述，应以当前代码和本目录新文档为准。

## 建议阅读顺序

### 先看这些

- `docs/control-plane-api.md`
  - 控制台与测试最常用的控制面接口汇总。
- `docs/console-operations.md`
  - 面向测试、运营与交付的控制台实际操作手册。
- `docs/warehouse-auth-refactor.md`
  - 当前 `warehouse` 鉴权、绑定与兼容策略的主说明。
- `docs/warehouse-credential-usage.md`
  - 面向控制台操作的读凭证 / 写凭证使用说明。
- `docs/warehouse-access-deep-dive.md`
  - 从真实代码出发整理 `warehouse` 访问全景、调用链、失败语义与排障要点。
- `docs/warehouse-aksk-creation-review.md`
  - 聚焦真实 `bound_token` 路径下 `ak/sk` 的创建、绑定、回填流程，并从批判视角评审优化空间。
- `docs/warehouse-aksk-remediation-plan.md`
  - 面向工程落地的正式改造计划，包含问题分级、代码改动点、测试计划和阶段划分。
- `docs/warehouse-implementation-batches.md`
  - 记录分批开发、测试和后续补提交所需的信息。
- `docs/warehouse-current-status-summary.md`
  - 汇总当前 `warehouse` 改造的真实完成度、最近已落地批次与建议下一步。
- `docs/warehouse-migration-guide.md`
  - 说明从旧钱包绑定 / 旧本地数据迁移到当前凭证模型的建议步骤。
- `docs/api-integration.md`
  - 面向外部服务接入。
  - `service search`、`grant`、`release` 相关内容仍有参考价值。
  - `warehouse` 控制面细节已拆到其他文档，不再在这里展开。
- `docs/worker-deployment.md`
  - 面向 worker 部署与运行维护。
  - 当前仍可作为部署参考。

### 这些文档需要重写或降级为历史参考

- `docs/technical-design-m1-m2.md`
  - 主要描述检索与 memory 演进，不覆盖当前 `warehouse` 凭证模型、绑定关系和失败语义。
  - 可作为历史设计背景，不应当作为当前实现文档。
- `docs/prd-bot-knowledge.md`
  - 仍主要服务 bot/chat 产品叙事。
  - 不覆盖当前控制台、绑定源、`warehouse` 权限收口后的操作事实。

## 当前已补齐的关键文档

- `warehouse` 鉴权重构设计说明
- `warehouse` 控制台操作手册
- `warehouse` 凭证使用说明
- `warehouse` 访问全景与问题排查
- `warehouse` `ak/sk` 创建流程评审
- `warehouse` `ak/sk` 改造计划
- `warehouse` 改造批次记录
- `warehouse` 当前状态总览
- `warehouse` 迁移说明
- `warehouse` 收口 TODO
- 控制面 API 文档

## 仍可继续补充的文档

- 领域模型说明
- task / worker 失败语义专项文档

## 重构中的事实来源

如果要判断当前代码到底实现到了哪里，优先看下面这些文件，而不是旧文档：

- `backend/knowledge/api/routes_warehouse.py`
- `backend/knowledge/services/warehouse_access.py`
- `backend/knowledge/templates/index.html`
- `backend/knowledge/static/js/app.js`

## 后续文档建议

- 当前关于 `warehouse` 的主文档已经切到“设计说明 + 使用说明 + 控制面 API + 操作手册 + 迁移说明 + TODO”。
- 当前已经补到“状态总览 + 迁移说明”，后续应优先维护这些文档与代码事实一致。
- `docs/technical-design-m1-m2.md` 与 `docs/prd-bot-knowledge.md` 继续保留为历史参考，不作为当前实现口径。
- 在仓库决定是否引入 `knowledge/docs/` 之前，先避免同时维护两套文档目录。
