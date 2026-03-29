# Warehouse 迁移说明

本文档说明如何把旧的 `warehouse` 钱包绑定使用方式，迁移到当前 `knowledge` 的凭证模型。

如果本文件与旧设计文档冲突，以当前代码和 `docs/control-plane-api.md` 为准。

## 1. 适用范围

适用于以下情况：

- 本地数据库里仍保留旧 `/warehouse/auth/*` 时代的数据
- 控制台使用者还沿用“钱包绑定后直接读写”的旧心智
- 需要把现有 `warehouse` 访问方式迁移到“读凭证 / 写凭证 / binding”模型

不适用于：

- 只跑 `mock` 模式的本地演示环境
- 需要从旧库自动导出并批量生成新凭证的场景

当前仓库没有提供“旧绑定自动迁移脚本”。迁移以人工核对和显式重建为主。

## 2. 新旧模型差异

旧模型的主要心智是：

- 钱包签名后长期复用上游绑定状态
- 浏览、上传、绑定、导入之间没有明确的凭证边界

当前模型的主心智是：

- 写操作靠写凭证
- 读操作靠读凭证
- 绑定、导入、evidence、source scan 都只走读凭证
- 所有路径都限制在当前 app 根目录下

默认 app 目录：

- `/apps/knowledge.yeying.pub`

## 3. 推荐迁移顺序

1. 备份当前本地数据库文件
2. 确认 `warehouse` 中还需要保留哪些 access key
3. 在 `warehouse` 侧重新准备最小权限的读 key / 写 key
4. 在 `knowledge` 中导入写凭证
5. 在 `knowledge` 中导入读凭证
6. 重新建立或核对 `SourceBinding`
7. 重新跑 import / reindex / source scan 验证新链路
8. 对不再使用的本地凭证执行 `revoke-local` 或删除

## 4. 推荐的最小迁移动作

### 4.1 写链路

至少准备一把写凭证，作用域指向：

- `/apps/knowledge.yeying.pub`
- 或 `/apps/knowledge.yeying.pub/uploads`

导入后验证：

- `GET /warehouse/credentials/write`
- `POST /warehouse/upload`

### 4.2 读链路

按目录准备一把或多把读凭证。

推荐做法：

- 给长期绑定目录单独一把读凭证
- 给 `uploads/` 单独一把读凭证

导入后验证：

- `GET /warehouse/credentials/read`
- `GET /warehouse/browse?credential_id=...`
- `GET /warehouse/preview?credential_id=...`

### 4.3 绑定与任务

迁移后重点核对：

- 绑定是否仍然引用有效读凭证
- 直接创建任务时是否传入了读凭证
- 按绑定源创建任务时，binding 是否完整

注意：

- 读链路已经不再回退到写凭证
- 显式传 `credential_id` 时，该凭证必须是读凭证

## 5. 何时建议直接重建本地库

如果满足下面任一情况，优先考虑备份后删库重建：

- 本地仍大量依赖旧 `/warehouse/auth/*` 语义
- 绑定表与当前凭证关系已经难以人工核对
- 测试环境可以接受重新导入凭证和重新建绑定

在当前仓库里，“手工重建 + 重新导入最小凭证”通常比写一次性迁移脚本更稳妥。

## 6. 本地 revoke 与删除的区别

当前控制面有两种本地治理动作：

- `revoke-local`
  - 保留凭证记录，但本地拒绝继续使用
  - 适合先止血、再排查引用关系
- `DELETE`
  - 物理删除本地记录
  - 适合确认再也不会使用，并且没有 binding 引用

读凭证如果仍被 `SourceBinding` 引用，当前不允许直接删除；但允许先做 `revoke-local`。

## 7. 验证清单

迁移完成后，至少检查：

- 写凭证可以上传
- 读凭证可以 browse / preview
- binding 创建时能成功校验路径
- import / reindex 不再依赖写凭证兜底
- source scan 可以靠匹配的读凭证完成
- `revoked_local` 的凭证不会再被 browse / upload / task 使用

## 8. 相关文档

- `docs/control-plane-api.md`
- `docs/warehouse-auth-refactor.md`
- `docs/warehouse-credential-usage.md`
- `docs/warehouse-current-status-summary.md`
