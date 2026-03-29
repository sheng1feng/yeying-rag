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
  - bootstrap attempt cleanup 请求接口
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
