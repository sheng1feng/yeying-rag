# Warehouse 改造当前状态总览

本文档用于总结当前 `knowledge` 仓库中与 `warehouse` 相关改造的真实状态，方便后续继续开发、补提交和做阶段性回顾。

如果本文件与旧设计文档冲突，以当前代码与本文件为准。

## 1. 当前仓库状态

截至当前整理时：

- 当前分支：`pr/rebase-main`
- 本地相对远端状态：`ahead 4`
- 当前工作区不是干净状态，仍有一批未提交改动

当前未提交改动主要集中在：

- `backend/knowledge/api/routes_tasks.py`
- `backend/knowledge/api/routes_warehouse.py`
- `backend/knowledge/services/asset_inventory.py`
- `backend/knowledge/services/evidence_pipeline.py`
- `backend/knowledge/services/ingestion.py`
- `backend/knowledge/services/warehouse_access.py`
- `backend/knowledge/templates/index.html`
- `backend/knowledge/static/js/app.js`
- `backend/knowledge/static/js/warehouse_bridge.js`
- `docs/todo-warehouse-auth-refactor.md`
- `docs/control-plane-api.md`
- `docs/warehouse-auth-refactor.md`
- `docs/warehouse-credential-usage.md`
- `docs/warehouse-migration-guide.md`
- `docs/warehouse-implementation-batches.md`
- `docs/warehouse-aksk-remediation-plan.md`
- `tests/test_app.py`

这批改动目前覆盖三类闭环：

- 前端/产品收口
- 读路径去掉写凭证兜底
- 通用本地 revoke 管理

## 2. 已完成的主链改造

当前已经完成并验证过的核心闭环包括：

### 2.1 Bootstrap 事务与可观察性基础

- 新增 `WarehouseProvisioningAttempt`
- bootstrap 已支持 `attempt_id / status / stage`
- bootstrap 不再依赖内层凭证服务隐式提交来表达事务成功
- `partial_success` 已经成为正式状态，而不是纯字符串错误

### 2.2 本地凭证元数据与本地复用

- `WarehouseAccessCredential` 已补充 bootstrap 来源与上游 key 映射信息
- 同 wallet + mode + target_path 的 bootstrap 已支持本地复用
- 重复 bootstrap 不再默认无脑新建上游 key

### 2.3 Attempt 查询接口

已提供：

- `GET /warehouse/bootstrap/attempts`
- `GET /warehouse/bootstrap/attempts/{attempt_id}`

作用：

- 查询最近 attempt
- 查看失败阶段
- 查询 cleanup 状态

### 2.4 远端 cleanup 闭环

当前 `knowledge` 已经接入上游 `warehouse` 的：

- `POST /api/v1/public/webdav/access-keys/revoke`

因此 cleanup 不再只是“登记人工请求”，而是可以：

1. 再次请求钱包签名
2. 后端代理换上游 token
3. 调上游 revoke access key
4. 把本地关联 credential 标记为 `revoked_local`

### 2.5 Bootstrap 策略配置化

bootstrap 策略已进入 `settings`：

- `warehouse_bootstrap_enable_reuse`
- `warehouse_bootstrap_key_name_prefix`
- `warehouse_bootstrap_write_expires_value`
- `warehouse_bootstrap_write_expires_unit`
- `warehouse_bootstrap_read_expires_value`
- `warehouse_bootstrap_read_expires_unit`

这意味着：

- 命名策略可配置
- TTL 可配置
- 本地复用开关可配置

### 2.6 前端/产品收口

当前前端已经基本收口到统一主路径：

- `uploads` 模式明确标为推荐
- `app_root_write` 模式明确标为只写入口，不再暗示读链路完整
- bootstrap 状态区会展示 attempt / status / cleanup
- 当 `cleanup_status=manual_cleanup_required` 时，前端已提供 cleanup 操作入口
- `warehouse_bridge.js` 已明确标注为 legacy helper，不再被当作主流程说明

### 2.7 读路径权限模型收口

当前已完成：

- browse / preview 的显式 `credential_id` 现在只接受读凭证
- 若要用写凭证浏览，必须显式传 `use_write_credential=true`
- `asset_inventory`、`evidence_pipeline`、`ingestion` 这些读链路都已经移除写凭证回退
- source scan / task / evidence 的自动选择现在最多只会选“匹配路径的读凭证”，不会再偷走写凭证

### 2.8 通用本地 revoke 管理

当前已提供：

- `POST /warehouse/credentials/read/{credential_id}/revoke-local`
- `POST /warehouse/credentials/write/revoke-local`

效果：

- 本地把凭证标成 `revoked_local`
- 仍保留记录，方便排查和解释
- 被本地吊销的凭证不会再用于 browse / upload / task / binding 读链路

## 3. 当前可用能力

从控制面实际能力看，当前已可稳定支撑：

- challenge + verify 登录 `knowledge`
- 手工导入读凭证 / 写凭证
- bootstrap 自动创建并回填 write/read key
- bootstrap partial success 可观测
- bootstrap attempt 查询
- bootstrap cleanup 执行远端 revoke
- 本地 bootstrap key 复用
- 本地手工 `revoke-local` 读凭证 / 写凭证
- uploads / browse / preview / binding / import 主链

## 4. 当前测试状态

当前这条主链已通过的主要验证方式：

- `python3 -m pytest tests/test_app.py`
- `PYTHONPYCACHEPREFIX=/tmp/knowledge_pycache python3 -m compileall backend/knowledge tests/test_app.py`

`tests/test_app.py` 当前是这条链路最重要的集成回归入口，已经覆盖：

- bootstrap 完整成功
- bootstrap partial success
- bootstrap local reuse
- bootstrap attempt list/get
- bootstrap cleanup wallet 隔离
- bootstrap cleanup 成功后的本地状态收口
- 读路径不再回退到写凭证
- `revoke-local` 后 browse / upload / task 会被本地拒绝

## 5. 仍未彻底完成的内容

下面这些是当前最明确的未完成项。

### 5.1 技术计划文档还没有完全回写为“已完成状态”

`warehouse-aksk-remediation-plan.md` 仍主要是计划口吻。

现在应当补一轮回写，明确：

- 已完成项
- 剩余项
- 当前优先级

### 5.2 `warehouse_bridge.js` 仍是保留态 legacy helper

当前它已经不再是主 bootstrap 路径，但也还没有彻底删除。

### 5.3 SourceBinding 根路径快照仍未实现

这项在 TODO 里仍是“评估项”，目前没有单独的数据模型字段承接。

## 6. 当前待提交批次

当前批次记录见：

- `docs/warehouse-implementation-batches.md`

按该文档，当前最关键的待提交内容是：

### 批次 3

- 前端/产品收口
- cleanup 前端入口
- `app_root_write` 模式文案收口

建议 commit message：

- `fix(console): tighten bootstrap product flow`

### 批次 4

- 读路径移除写凭证兜底
- browse / preview 显式凭证语义收口
- 任务显式 `credential_id` 限定为读凭证

建议 commit message：

- `fix(warehouse): remove write fallback from read paths`

### 批次 5

- 通用本地 revoke 管理动作
- `revoked_local` 的前端入口和本地拒绝语义
- 迁移说明与文档回写

建议 commit message：

- `feat(warehouse): add local credential revoke controls`

## 7. 建议下一步

如果后续继续开发，而不是先补提交，我建议优先做：

1. 回写 remediation plan / TODO，让文档和代码状态一致
2. 决定是否彻底移除 `warehouse_bridge.js`
3. 评估是否要给 `SourceBinding` 增加根路径快照

## 8. 相关文档索引

如需继续推进，可优先看：

- `docs/warehouse-access-deep-dive.md`
- `docs/warehouse-aksk-creation-review.md`
- `docs/warehouse-aksk-remediation-plan.md`
- `docs/warehouse-implementation-batches.md`
- `docs/control-plane-api.md`
- `docs/warehouse-credential-usage.md`
- `docs/warehouse-auth-refactor.md`
