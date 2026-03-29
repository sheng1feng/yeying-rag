# Warehouse `ak/sk` 创建改造计划与执行状态

本文档是在 `docs/warehouse-aksk-creation-review.md` 的问题分析基础上，给出一版可执行的改造计划。

截至当前代码状态，这份计划里的主体工程已经不是“待开始”，而是“多数已落地，少数收口项待继续”。

范围只覆盖真实 `bound_token` 场景下的：

- bootstrap 创建 `ak/sk`
- 本地凭证回填
- 上游 key 生命周期治理
- 前端 bootstrap 交互
- 与这条链路直接相关的接口、数据模型、测试和文档

不覆盖：

- `mock` 模式
- Source / Asset / Ingestion 的全量重构
- 更广义的 `warehouse` 访问收口

## 0. 当前执行状态

已经完成的核心项：

- provisioning attempt 持久化
- bootstrap 结构化状态、`partial_success`、`cleanup_status`
- 本地 bootstrap 凭证元数据与本地复用
- bootstrap attempt 查询接口
- cleanup 闭环接上游 `revoke`
- TTL / key name prefix / reuse 开关配置化
- 前端 bootstrap 与 cleanup 收口
- 读路径移除写凭证回退
- 通用本地 `revoke-local` 管理动作

当前仍待继续的项：

- 将整篇文档从“计划口吻”进一步回写为“现状口吻”
- 评估 `SourceBinding` 根路径快照是否值得补模型
- 决定是否彻底移除 `warehouse_bridge.js`

## 1. 目标

这次改造的目标不是“再加一点提示文案”，而是解决真实环境里的四类核心问题：

1. bootstrap 非原子，失败后容易留下部分成功状态
2. 上游 access key 重复创建、无法复用、无法补偿清理
3. 本地缺少 provisioning 级别的状态与审计信息
4. bootstrap 响应和前端交互过于乐观，无法表达部分成功与可恢复状态

## 2. 当前代码基线

当前 `ak/sk` 创建链路的关键文件是：

- `backend/knowledge/api/routes_warehouse.py`
  - `POST /warehouse/bootstrap/challenge`
  - `POST /warehouse/bootstrap/initialize`
- `backend/knowledge/services/warehouse_bootstrap.py`
  - 上游 challenge / verify / create key / bind key / mkdir 的编排
- `backend/knowledge/services/warehouse_access.py`
  - 本地读凭证 / 写凭证保存、探测、Basic Auth 校验
- `backend/knowledge/services/warehouse.py`
  - WebDAV `PROPFIND` / `MKCOL` / `PUT` / `GET`
- `backend/knowledge/schemas/warehouse.py`
  - bootstrap 请求与响应模型
- `backend/knowledge/models/entities.py`
  - `WarehouseAccessCredential`
- `backend/knowledge/static/js/app.js`
  - 前端 bootstrap 主流程
- `backend/knowledge/static/js/warehouse_bridge.js`
  - 遗留的浏览器直连 helper
- `tests/test_app.py`
  - bootstrap 相关测试

## 3. 问题清单

下面的问题按优先级排序。

## 3.1 P0: bootstrap 事务边界是假的

现状：

- `routes_warehouse.py` 的 `/warehouse/bootstrap/initialize` 在外层用了 `db.commit()` / `db.rollback()`
- 但 `WarehouseAccessService.create_read_credential()` 内部会自己 `db.commit()`
- `WarehouseAccessService.upsert_write_credential()` 内部也会自己 `db.commit()`

直接后果：

- `initialize_credentials()` 中途失败时，外层 `rollback()` 回滚不了已提交的本地凭证
- 上游已创建的 key 和本地已保存的凭证可能同时存在，但接口仍返回失败

对应代码：

- `backend/knowledge/api/routes_warehouse.py`
- `backend/knowledge/services/warehouse_bootstrap.py`
- `backend/knowledge/services/warehouse_access.py`

## 3.2 P0: 没有 provisioning 级别的持久化状态

现状：

- 当前只有 `WarehouseAccessCredential`
- 没有一张表记录“这次 bootstrap 做到了第几步”
- 没有 attempt id、阶段状态、上游 key id、补偿状态

直接后果：

- 无法区分“完整成功”“部分成功”“失败但需要清理”
- 无法让前端恢复展示上一次失败的真实阶段
- 无法为运维提供最关键的排障上下文

对应代码：

- `backend/knowledge/models/entities.py`
- `backend/knowledge/schemas/warehouse.py`
- `backend/knowledge/services/warehouse_bootstrap.py`

## 3.3 P0: 没有补偿清理，会上游残留孤儿 key

现状：

- bootstrap 会调用 create key 和 bind key
- 但当前没有 delete / revoke / cleanup 的后端逻辑
- 一旦后续失败，只能把已创建 key 留在上游

直接后果：

- 重试 bootstrap 会不断新增 key
- 用户在 `warehouse` 里会看到越来越多历史 key
- 安全与审计面扩大

对应代码：

- `backend/knowledge/services/warehouse_bootstrap.py`
- `backend/knowledge/static/js/warehouse_bridge.js`

## 3.4 P1: 缺少幂等和复用策略

现状：

- `initialize_credentials()` 每次都无条件 create write key
- `uploads_bundle` 模式还会无条件 create read key
- 当前后端没有“先查已有 key 再决定复用”的逻辑

直接后果：

- 同一钱包、同一路径、同一模式重复点击会创建无限多批 key
- 覆盖本地写凭证不等于替换上游 key

对应代码：

- `backend/knowledge/services/warehouse_bootstrap.py`
- `backend/knowledge/static/js/warehouse_bridge.js`

## 3.5 P1: 权限、TTL 和命名策略写死

现状：

- write key 权限固定 `["read", "create", "update"]`
- read key 权限固定 `["read"]`
- `expiresValue=0`
- `expiresUnit="day"`

直接后果：

- TTL 语义不清楚
- 默认长期有效 key 不够稳妥
- 无法按环境或场景调整策略

对应代码：

- `backend/knowledge/services/warehouse_bootstrap.py`
- `backend/knowledge/static/js/app.js`
- `backend/knowledge/static/js/warehouse_bridge.js`
- `backend/knowledge/core/settings.py`

## 3.6 P1: 本地凭证缺少来源和上游映射元数据

现状：

- `WarehouseAccessCredential` 只保存本地使用所需字段
- 不记录它来自手工导入还是 bootstrap
- 不记录上游 access key id
- 不记录 provisioning mode
- 不记录远端名字、过期时间、批次号

直接后果：

- 无法做“按上游 key id 复用/清理”
- 无法在 UI 或运维日志里追踪一把 key 的来源

对应代码：

- `backend/knowledge/models/entities.py`
- `backend/knowledge/services/warehouse_access.py`
- `backend/knowledge/schemas/warehouse.py`

## 3.7 P2: bootstrap 响应模型过于理想化

现状：

- `WarehouseBootstrapInitializeResponse` 只能表达成功
- 返回里没有 `attempt_id`
- 没有 `status`
- 没有 `stage`
- 没有 `warnings`
- 没有 `cleanup_status`

直接后果：

- 接口只能“成功返回完整对象 / 失败抛 400”
- 无法表达部分成功
- 前端只能展示一段文案，不能指导恢复动作

对应代码：

- `backend/knowledge/schemas/warehouse.py`
- `backend/knowledge/api/routes_warehouse.py`
- `backend/knowledge/static/js/app.js`

## 3.8 P2: 前端保留遗留双轨集成

现状：

- 当前主 bootstrap 已经走后端代理
- 但 `warehouse_bridge.js` 仍保留浏览器直连 create/bind/list/mkcol helper

直接后果：

- 协议维护分散
- 未来容易出现前后端各维护一套上游协议

对应代码：

- `backend/knowledge/static/js/app.js`
- `backend/knowledge/static/js/warehouse_bridge.js`

## 4. 目标设计

本次改造后的目标状态：

1. bootstrap 变成显式的 provisioning 流程，而不是“同步函数里顺手创建两把 key”
2. 本地有可查询的 provisioning attempt 记录
3. 外层事务边界真实有效，内部服务不再随意提交
4. 接口能表达 `succeeded` / `partial_success` / `failed` / `needs_manual_cleanup`
5. 本地凭证能反查上游 key
6. 重试 bootstrap 时优先复用或清理，而不是无限新增
7. 对“仅改 `knowledge`”和“需要上游支持”两类优化分别规划

## 5. 数据模型改造

## 5.1 新增 `WarehouseProvisioningAttempt`

建议新增一张 provisioning 记录表，而不是把所有阶段信息硬塞进 `WarehouseAccessCredential`。

建议字段：

- `id`
- `owner_wallet_address`
- `mode`
  - `uploads_bundle`
  - `app_root_write`
- `target_path`
- `status`
  - `running`
  - `succeeded`
  - `partial_success`
  - `failed`
  - `compensated`
  - `needs_manual_cleanup`
- `stage`
  - `challenge_requested`
  - `signature_verified`
  - `write_key_created`
  - `write_key_bound`
  - `directories_ensured`
  - `write_credential_saved`
  - `read_key_created`
  - `read_key_bound`
  - `read_credential_saved`
  - `completed`
- `write_upstream_access_key_id`
- `write_key_id`
- `read_upstream_access_key_id`
- `read_key_id`
- `write_credential_id`
- `read_credential_id`
- `error_message`
- `warning_message`
- `cleanup_status`
- `details_json`
- `created_at`
- `updated_at`

落点：

- `backend/knowledge/models/entities.py`
- `backend/knowledge/db/schema.py`
- 如有必要补充 `backend/knowledge/schemas/warehouse.py`

## 5.2 扩展 `WarehouseAccessCredential`

建议为凭证表增加最少必要元数据：

- `credential_source`
  - `manual_import`
  - `bootstrap`
- `upstream_access_key_id`
- `provisioning_attempt_id`
- `provisioning_mode`
- `remote_name`
- `expires_at`

作用：

- 支持复用、审计、清理、UI 展示

落点：

- `backend/knowledge/models/entities.py`
- `backend/knowledge/db/schema.py`
- `backend/knowledge/services/warehouse_access.py`
- `backend/knowledge/schemas/warehouse.py`

## 6. API 与响应模型改造

## 6.1 扩展 `POST /warehouse/bootstrap/initialize`

当前接口应该从“只返回成功对象”改成“返回阶段化 provisioning 结果”。

建议响应字段：

- `attempt_id`
- `status`
- `stage`
- `mode`
- `mode_label`
- `target_path`
- `write_key_id`
- `read_key_id`
- `write_credential`
- `read_credential`
- `warnings`
- `cleanup_status`

这样即使发生部分成功，前端也能拿到结构化信息，而不是只靠 `detail` 字符串判断。

落点：

- `backend/knowledge/schemas/warehouse.py`
- `backend/knowledge/api/routes_warehouse.py`
- `backend/knowledge/static/js/app.js`

## 6.2 增加 bootstrap attempt 查询接口

建议新增：

- `GET /warehouse/bootstrap/attempts`
- `GET /warehouse/bootstrap/attempts/{attempt_id}`

作用：

- 前端恢复展示
- 运维排障
- 部分成功状态可追踪

落点：

- `backend/knowledge/api/routes_warehouse.py`
- `backend/knowledge/schemas/warehouse.py`

## 6.3 预留补偿接口

如果上游后续支持 revoke/delete，建议补：

- `POST /warehouse/bootstrap/attempts/{attempt_id}/cleanup`

短期即使不开放 UI，也建议保留后端接口形态，方便调试与恢复。

## 7. 服务层改造

## 7.1 重构 `WarehouseAccessService` 的提交策略

这是第一步必须做的。

建议拆成两层：

- 外部便捷接口
  - 保持现有手工导入场景可用
  - 默认仍可提交
- 事务内接口
  - 不做 `db.commit()`
  - 只做对象构建、探测、状态更新、`flush`

建议新增或重构：

- `create_read_credential(..., *, commit: bool = True)`
- `upsert_write_credential(..., *, commit: bool = True)`

或更清晰地拆成：

- `create_read_credential_in_tx()`
- `upsert_write_credential_in_tx()`

对应修改点：

- `backend/knowledge/services/warehouse_access.py`
- 调用它们的所有路由

注意：

- 手工导入接口可以继续使用 `commit=True`
- bootstrap 必须改用事务内版本

## 7.2 重构 `WarehouseBootstrapService` 为阶段化 orchestrator

当前 `initialize_credentials()` 过于线性，缺少阶段状态和补偿。

建议改造成：

1. 创建 `WarehouseProvisioningAttempt`
2. 每完成一步就更新 `stage`
3. 记录上游 key id 与本地 credential id
4. 出错时进入补偿逻辑
5. 最终统一生成 provisioning 结果对象

建议新增内部方法：

- `_create_attempt()`
- `_mark_stage()`
- `_record_write_key()`
- `_record_read_key()`
- `_save_write_credential_in_tx()`
- `_save_read_credential_in_tx()`
- `_build_initialize_response()`
- `_cleanup_partial_failure()`

落点：

- `backend/knowledge/services/warehouse_bootstrap.py`

## 7.3 给 bootstrap service 增加后端 key 生命周期接口

当前 service 只有：

- `create`
- `bind`

建议增加：

- `list_access_keys()`
- `get_access_key()`
- `revoke_access_key()` 或 `delete_access_key()`（如果上游支持）

即使当前上游不支持删除，也建议先把 service 层接口形态定出来，并明确返回“不支持远端清理”的状态。

落点：

- `backend/knowledge/services/warehouse_bootstrap.py`

## 7.4 增加 key 复用策略

建议复用优先级：

1. 查询本地当前钱包下，`credential_source=bootstrap`、路径相同、模式相同、状态仍 `active` 的凭证
2. 如果本地有凭证且已映射上游 key，先尝试校验可用性
3. 如果本地没有，但上游能列出匹配名称和路径的 key，则可选择复用
4. 只有以上都不满足时，才新建 key

这里分两档实现：

- 最小可行
  - 只做本地复用
- 完整版
  - 本地 + 上游联合复用

落点：

- `backend/knowledge/services/warehouse_bootstrap.py`
- `backend/knowledge/services/warehouse_access.py`

## 8. 前端改造

## 8.1 `app.js` 需要从“成功/失败二元态”升级到“阶段态”

当前前端状态机只维护：

- `requesting_challenge`
- `signing_challenge`
- `initializing`
- `ready`
- `failed`

这不够表达部分成功。

建议前端改成：

- 显示 `attempt_id`
- 显示 `stage`
- 显示 `status`
- 如果是 `partial_success`
  - 明确告诉用户 write key 是否已成功
  - read key 是否失败
  - 当前是否需要重试或人工清理

落点：

- `backend/knowledge/static/js/app.js`

## 8.2 清理或降级 `warehouse_bridge.js`

建议：

- 明确标注它不是当前主流程
- 把当前不用的 create/bind/list/mkcol helper 标成内部调试用途
- 或逐步把 bootstrap 相关 helper 从浏览器侧移除

目标不是立即删代码，而是避免未来继续双轨维护。

落点：

- `backend/knowledge/static/js/warehouse_bridge.js`

## 9. 配置项改造

当前 bootstrap policy 硬编码在代码里，不利于生产治理。

建议新增配置项：

- `WAREHOUSE_BOOTSTRAP_WRITE_EXPIRES_VALUE`
- `WAREHOUSE_BOOTSTRAP_WRITE_EXPIRES_UNIT`
- `WAREHOUSE_BOOTSTRAP_READ_EXPIRES_VALUE`
- `WAREHOUSE_BOOTSTRAP_READ_EXPIRES_UNIT`
- `WAREHOUSE_BOOTSTRAP_ENABLE_REUSE`
- `WAREHOUSE_BOOTSTRAP_KEY_NAME_PREFIX`

落点：

- `backend/knowledge/core/settings.py`
- `backend/knowledge/services/warehouse_bootstrap.py`

## 10. 文档改造

本轮代码改造完成后，需要同步更新这些文档：

- `docs/warehouse-auth-refactor.md`
  - 更新 provisioning 模型、attempt 状态、补偿语义
- `docs/warehouse-credential-usage.md`
  - 更新 bootstrap 流程与用户可见状态
- `docs/control-plane-api.md`
  - 更新 bootstrap 响应模型与 attempt 查询接口
- `docs/warehouse-aksk-creation-review.md`
  - 把“现状问题”与“已规划方案”对齐
- `docs/todo-warehouse-auth-refactor.md`
  - 把已进入正式计划的项从 TODO 提升为有阶段的执行任务

## 11. 测试计划

## 11.1 服务层测试

新增或重构测试用例：

- bootstrap 完整成功时，attempt 状态为 `succeeded`
- write key 创建成功、read key 失败时，attempt 状态为 `partial_success`
- bootstrap 失败时，本地没有意外多提交的凭证
- 手工导入读凭证仍然保持原行为
- 手工导入写凭证仍然保持原行为
- 复用已有 bootstrap 凭证时不会重复 create key

对应文件：

- `tests/test_app.py`
- 如有必要补 `tests/test_warehouse_bootstrap.py`

## 11.2 API 测试

新增测试：

- `/warehouse/bootstrap/initialize` 返回 `attempt_id/status/stage`
- `/warehouse/bootstrap/attempts` 可查询最近记录
- 部分成功时 HTTP 语义与响应体一致

## 11.3 回归测试

必须回归：

- `/warehouse/credentials/read`
- `/warehouse/credentials/write`
- `/warehouse/upload`
- `/warehouse/browse`
- `/warehouse/preview`
- `/kbs/{kb_id}/bindings`

因为 `WarehouseAccessService` 的提交策略会被改动，影响范围不小。

## 12. 分阶段实施顺序

## Phase 0: 建安全护栏

目标：

- 不改变产品入口
- 先修事务与可观察性问题

任务：

1. 新增 `WarehouseProvisioningAttempt`
2. 改造 bootstrap response
3. 去掉 bootstrap 链路里的内层 `commit`
4. 前端展示阶段化结果
5. 增加 attempt 查询接口

完成标志：

- bootstrap 失败时，前后端都能准确知道失败阶段
- 不再出现“接口失败但本地状态不可解释”

## Phase 1: 做本地复用和治理

目标：

- 减少重复创建 key
- 建立本地与上游 key 的映射关系

任务：

1. 给 `WarehouseAccessCredential` 增加来源与上游 key 元数据
2. 新增本地复用策略
3. 前端补“复用现有 bootstrap key”提示

完成标志：

- 重复 bootstrap 不再默认无限新建 key

## Phase 2: 做补偿与清理

目标：

- 解决上游孤儿 key

任务：

1. 如果上游支持，接入 revoke/delete API
2. bootstrap 失败时自动补偿
3. 增加 attempt cleanup 接口

完成标志：

- 失败后不再默认遗留新 key

## Phase 3: 做策略收口

目标：

- 让权限、TTL 和产品语义更稳

任务：

1. 把 TTL/命名/复用开关移入 settings
2. 重新定义 `app_root_write` 的 UI 文案和后续行为
3. 收拢浏览器侧遗留 helper

完成标志：

- provisioning policy 从“写死”变成“可治理”

## 13. 风险与注意事项

### 13.1 最大技术风险

`WarehouseAccessService` 提交策略改动会影响：

- 手工导入凭证
- upload
- browse
- preview
- binding

所以 Phase 0 一定要配套回归测试。

### 13.2 最大产品风险

一旦 bootstrap 开始返回 `partial_success`，前端必须同步升级，否则用户会看到结构化字段但不知道怎么处理。

### 13.3 最大外部依赖风险

如果上游 `warehouse` 没有 revoke/delete/list 的稳定 API，Phase 2 只能做“标记需要人工清理”，做不到完整补偿。

## 14. 建议的落地顺序

如果只允许做一轮短周期改造，我建议按下面顺序落地：

1. 先做 Phase 0
2. 再做本地复用
3. 最后再看上游是否支持补偿

原因：

- Phase 0 直接解决最危险的一致性问题
- 它对现有产品流程最小侵入
- 即便上游暂时不给 revoke/delete API，也值得先做

## 15. 对应文档关系

当前相关文档的职责建议这样划分：

- `docs/warehouse-aksk-creation-review.md`
  - 负责批判性分析现状问题
- `docs/warehouse-aksk-remediation-plan.md`
  - 负责记录正式改造计划
- `docs/warehouse-auth-refactor.md`
  - 等代码落地后，记录最终设计事实
- `docs/warehouse-credential-usage.md`
  - 面向用户与运营，记录最终操作手册
