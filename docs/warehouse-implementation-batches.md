# Warehouse 改造批次记录

本文档用于记录当前 `warehouse` 改造过程中，已经完成但未必已推送远端的批次信息，方便后续按批次提交。

## 使用方式

每个批次记录四类信息：

- 目标
- 主要修改文件
- 已完成的测试
- 建议的 commit message

如果当前环境无法直接执行 `git commit` / `git push`，后续可以按本文件逐批补提交。

## 当前批次状态

### 已存在本地提交

#### 批次 0

- 目标
  - 文档基线
  - bootstrap attempt 基础
  - bootstrap API / 前端状态展示收口
- 对应提交
  - `7a6ad15 fix(warehouse): track bootstrap attempt state`

#### 批次 1

- 目标
  - 本地凭证元数据
  - bootstrap 本地复用
- 对应提交
  - `51dbabc fix(warehouse): reuse local bootstrap credentials`
- 主要修改文件
  - `backend/knowledge/models/entities.py`
  - `backend/knowledge/db/schema.py`
  - `backend/knowledge/services/warehouse_access.py`
  - `backend/knowledge/services/warehouse_bootstrap.py`
  - `tests/test_app.py`
- 已执行测试
  - `python3 -m pytest tests/test_app.py`
  - `PYTHONPYCACHEPREFIX=/tmp/knowledge_pycache python3 -m compileall backend/knowledge tests/test_app.py`

#### 批次 2

- 目标
  - bootstrap attempt 查询接口
  - bootstrap attempt cleanup 接口
  - 远端 cleanup 闭环
  - 通过上游 `warehouse` revoke API 撤销本次 bootstrap 生成的远端 key
  - 把本地关联凭证标记为 `revoked_local`
  - bootstrap key 名称前缀、过期策略与本地复用开关配置化
- 主要修改文件
  - `backend/knowledge/core/settings.py`
  - `backend/knowledge/schemas/warehouse.py`
  - `backend/knowledge/services/warehouse_bootstrap.py`
  - `backend/knowledge/api/routes_warehouse.py`
  - `tests/test_app.py`
  - `docs/control-plane-api.md`
  - `docs/README.md`
  - `docs/warehouse-implementation-batches.md`
- 对应提交
  - `feat(warehouse): add bootstrap attempt APIs and policy controls`
- 已执行测试
  - `python3 -m pytest tests/test_app.py`
  - `PYTHONPYCACHEPREFIX=/tmp/knowledge_pycache python3 -m compileall backend/knowledge tests/test_app.py`

#### 批次 3

- 目标
  - 前端/产品收口
  - cleanup 前端入口
  - `app_root_write` 模式文案收口
- 状态
  - 已完成开发、测试通过、待提交
- 主要修改文件
  - `backend/knowledge/templates/index.html`
  - `backend/knowledge/static/js/app.js`
  - `backend/knowledge/static/js/warehouse_bridge.js`
  - `docs/README.md`
  - `docs/warehouse-current-status-summary.md`
  - `docs/warehouse-credential-usage.md`
  - `docs/warehouse-auth-refactor.md`
  - `docs/control-plane-api.md`
  - `docs/warehouse-implementation-batches.md`
  - `tests/test_app.py`
- 对应提交
  - `fix(console): tighten bootstrap product flow`
- 已执行测试
  - `python3 -m pytest tests/test_app.py`
  - `PYTHONPYCACHEPREFIX=/tmp/knowledge_pycache python3 -m compileall backend/knowledge tests/test_app.py`

#### 批次 4

- 目标
  - 移除读路径里的写凭证兜底
  - browse / preview 的显式 `credential_id` 限定为读凭证
  - 任务显式 `credential_id` 限定为读凭证
  - source scan / task / evidence 只允许自动选择匹配路径的读凭证
- 状态
  - 已完成开发、测试通过、待提交
- 主要修改文件
  - `backend/knowledge/api/routes_tasks.py`
  - `backend/knowledge/services/asset_inventory.py`
  - `backend/knowledge/services/evidence_pipeline.py`
  - `backend/knowledge/services/ingestion.py`
  - `backend/knowledge/services/warehouse_access.py`
  - `backend/knowledge/static/js/app.js`
  - `tests/test_app.py`
- 对应提交
  - `fix(warehouse): remove write fallback from read paths`
- 已执行测试
  - `python3 -m pytest tests/test_app.py`
  - `PYTHONPYCACHEPREFIX=/tmp/knowledge_pycache python3 -m compileall backend/knowledge tests/test_app.py`

#### 批次 5

- 目标
  - 增加通用本地 `revoke-local` 控制面动作
  - 让 `revoked_local` 的凭证在 browse / upload / task 上被本地拒绝
  - 回写当前状态文档和迁移说明
- 状态
  - 已完成开发、测试通过、待提交
- 主要修改文件
  - `backend/knowledge/api/routes_warehouse.py`
  - `backend/knowledge/services/warehouse_access.py`
  - `backend/knowledge/static/js/app.js`
  - `docs/README.md`
  - `docs/control-plane-api.md`
  - `docs/todo-warehouse-auth-refactor.md`
  - `docs/warehouse-aksk-remediation-plan.md`
  - `docs/warehouse-auth-refactor.md`
  - `docs/warehouse-credential-usage.md`
  - `docs/warehouse-current-status-summary.md`
  - `docs/warehouse-migration-guide.md`
  - `docs/warehouse-implementation-batches.md`
  - `tests/test_app.py`
- 对应提交
  - `feat(warehouse): add local credential revoke controls`
- 已执行测试
  - `python3 -m pytest tests/test_app.py`
  - `PYTHONPYCACHEPREFIX=/tmp/knowledge_pycache python3 -m compileall backend/knowledge tests/test_app.py`
