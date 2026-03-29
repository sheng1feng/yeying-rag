# Warehouse 改造当前状态总览

本文档用于总结当前 `knowledge` 仓库中与 `warehouse` 相关改造的真实状态，方便后续继续开发和做阶段性回顾。

如果本文件与旧设计文档冲突，以当前代码与本文件为准。

## 1. 当前仓库状态

当前 `warehouse` 主链改造已经不再停留在“零散未提交改动”阶段，而是已经形成一套可追溯的代码与文档基线。

最近已经补齐到当前分支的三类闭环：

- 前端 / 产品收口
- 读路径移除写凭证回退
- 通用本地 `revoke-local` 管理

如果本文件与某个旧计划文档冲突，以当前代码、`docs/control-plane-api.md` 和本文件为准。

如果需要确认阅读当下的瞬时 git 状态，请直接执行：

- `git status`
- `git log --oneline`

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

### 2.3 Attempt 查询与 cleanup

已提供：

- `GET /warehouse/bootstrap/attempts`
- `GET /warehouse/bootstrap/attempts/{attempt_id}`
- `POST /warehouse/bootstrap/attempts/{attempt_id}/cleanup`

当前可用于：

- 查询最近 attempt
- 查看失败阶段
- 查询 cleanup 状态
- 对需要清理的 attempt 执行远端 revoke，并把本地关联 credential 标记为 `revoked_local`

### 2.4 Bootstrap 策略配置化

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

### 2.5 前端/产品收口

当前前端已经基本收口到统一主路径：

- `uploads` 模式明确标为推荐
- `app_root_write` 模式明确标为只写入口，不再暗示读链路完整
- bootstrap 状态区会展示 attempt / status / cleanup
- 当 `cleanup_status=manual_cleanup_required` 时，前端已提供 cleanup 操作入口
- `warehouse_bridge.js` 已明确标注为 legacy helper，不再被当作主流程说明

### 2.6 读路径权限模型收口

当前已完成：

- browse / preview 的显式 `credential_id` 现在只接受读凭证
- 若要用写凭证浏览，必须显式传 `use_write_credential=true`
- `asset_inventory`、`evidence_pipeline`、`ingestion` 这些读链路都已经移除写凭证回退
- source scan / task / evidence 的自动选择现在最多只会选“匹配路径的读凭证”，不会再偷走写凭证

### 2.7 通用本地 revoke 管理

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

### 5.1 技术计划文档仍保留了大量原始方案结构

`warehouse-aksk-remediation-plan.md` 现在已经在文档头部补了“执行状态”说明，但中后段仍保留原始问题分析与方案结构。

这没有错，但使用时要明确：

- 它更适合做设计背景与审计依据
- 当前事实口径应优先回到代码与本文件

### 5.2 `warehouse_bridge.js` 仍是保留态 legacy helper

当前它已经不再是主 bootstrap 路径，但也还没有彻底删除。

### 5.3 SourceBinding 根路径快照仍未实现

这项在 TODO 里仍是“评估项”，目前没有单独的数据模型字段承接。

### 5.4 本地 revoke 仍没有恢复动作

当前只有 `revoke-local`，还没有与之对应的“恢复为 active”的控制面动作。

## 6. 最近已落地批次

当前批次记录见：

- `docs/warehouse-implementation-batches.md`

最近已经落到当前分支的关键批次是：

- 批次 3：`935d119 fix(console): tighten bootstrap product flow`
- 批次 4：`1657965 fix(warehouse): remove write fallback from read paths`
- 批次 5：`ce0a871 feat(warehouse): add local credential revoke controls`

## 7. 建议下一步

如果后续继续开发，我建议优先做：

1. 继续收口 remediation plan / TODO，让文档和代码状态一致
2. 决定是否彻底移除 `warehouse_bridge.js`
3. 评估是否要给 `SourceBinding` 增加根路径快照
4. 评估是否要给本地 `revoke-local` 增加恢复动作

## 8. 相关文档索引

如需继续推进，可优先看：

- `docs/warehouse-access-deep-dive.md`
- `docs/warehouse-aksk-creation-review.md`
- `docs/warehouse-aksk-remediation-plan.md`
- `docs/warehouse-implementation-batches.md`
- `docs/control-plane-api.md`
- `docs/warehouse-credential-usage.md`
- `docs/warehouse-auth-refactor.md`
