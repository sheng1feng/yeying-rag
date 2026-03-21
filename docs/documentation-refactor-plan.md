# 文档重构计划

## 目标

基于当前仓库的真实实现状态，重构 `knowledge` 文档体系，尤其补齐 `warehouse` 权限收口后的设计、迁移和操作文档。

本计划优先解决两个问题：

1. 现有文档与代码事实不一致。
2. `warehouse` 权限模型正在改造，哪些文档可以先写，哪些必须等代码稳定后再写，没有明确边界。

## 当前代码状态

### 已经落地的部分

- 后端已经存在 `warehouse` 读凭证 / 写凭证模型与接口：
  - `backend/knowledge/services/warehouse_access.py`
  - `backend/knowledge/api/routes_warehouse.py`
- 前端已经出现凭证管理和按凭证绑定的主交互：
  - `backend/knowledge/templates/index.html`
  - `backend/knowledge/static/js/app.js`
- 绑定源创建已经要求 `credential_id`，不再是只传 `source_path`。
- `WarehouseGateway` 已支持 Basic Auth，请求可携带 `ak/sk`。

### 仍在过渡中的部分

- 多处读路径仍带 `allow_write_fallback=True`：
  - `backend/knowledge/services/asset_inventory.py`
  - `backend/knowledge/services/evidence_pipeline.py`
  - `backend/knowledge/services/ingestion.py`

这意味着：

- 旧钱包绑定接口已经下线，但读路径仍存在部分写凭证兜底。
- 文档可以按“无兼容接口”描述，但仍应保留实现细节说明。

## 已完成的文档补齐

### 1. `warehouse` 鉴权重构设计文档

- `warehouse-auth-refactor.md`

当前状态：

- 已新增
- 作为当前 `warehouse` 鉴权、绑定和兼容策略的主说明

### 2. `warehouse` 凭证使用说明

- `warehouse-credential-usage.md`

当前状态：

- 已新增
- 当前控制台关于读凭证 / 写凭证的主操作手册已经落到该文档

### 3. 控制面 API 文档

- `control-plane-api.md`

当前状态：

- 已新增
- 当前控制台 / 测试最常用接口已经集中到该文档

### 4. 控制台与入口文档修正

已同步更新：

- `README.md`
- `docs/README.md`
- `docs/api-integration.md`
- `docs/console-operations.md`

修正内容：

- 去掉“自动绑定 app UCAN”作为默认主流程的表述
- 改为“先导入读凭证 / 写凭证，再浏览、上传、绑定、导入”
- 增加新文档入口链接
- 删除旧 `/warehouse/auth/*` 兼容接口的描述

## 仍待补充的文档

### 1. `warehouse` 迁移说明

仍缺：

- 旧 UCAN/JWT 绑定用户的离线迁移手册

### 2. 领域模型说明

仍缺：

- binding、source、asset、document、evidence、item、release、grant 的关系图和术语说明

### 3. Worker 与失败语义说明

仍缺：

- 绑定驱动任务如何选路径
- `failed`、`partial_success`、`pending_sync`、`syncing`、`indexed` 的精确定义

## 当前建议的后续顺序

### 第一阶段

- 补 `warehouse-auth-migration.md`
- 视测试覆盖情况补 task / worker 失败语义文档

### 第二阶段

- 补 `domain-model-overview.md`
- 继续收紧内部读路径里的写凭证兜底

## 关于 `knowledge/docs` 目录

当前仓库只有顶层 `docs/`，没有稳定的 `knowledge/docs/` 目录。

建议：

- 在目录结构尚未定版前，先继续使用顶层 `docs/`。
- 如果后续确定要把文档迁到 `knowledge/docs/`，应一次性迁移并更新全部引用，避免短期内维护两套目录。

## 对 `warehouse` 权限文档的当前判断

基于当前项目状态，`warehouse` 权限文档应拆成两类：

### 已经定稿的

- 设计说明
- 使用说明
- 控制面 API
- TODO

### 仍不建议提前定稿的

- 迁移手册

原因很直接：虽然旧接口已经删除，但旧数据库和本地测试环境仍可能残留历史绑定数据，迁移说明需要结合清理策略一起写。
