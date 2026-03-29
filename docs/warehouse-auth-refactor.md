# Warehouse 鉴权与绑定重构说明

## 背景

`knowledge` 现在把 `warehouse` 继续作为唯一资产中心，但控制面访问模型已经从“钱包签名后长期复用上游绑定 token”为主，切到“手工导入 WebDAV `ak/sk` 凭证”为主。

当前仓库的主目标不是让 `knowledge` 代替 `warehouse` 发 key，而是：

- 让用户在 `warehouse` 自行创建受限 key
- 让 `knowledge` 只保存当前 app 目录下所需的最小凭证
- 让绑定源、浏览、上传、导入都显式依赖当前场景对应的凭证

## 目标

- 读取能力与写入能力分离
- 绑定源与凭证显式关联，不再只靠 `source_path`
- 所有路径继续限制在当前 app 根目录下
- 对 `warehouse` 的请求显式使用 Basic Auth `ak/sk`
- 删除旧钱包绑定接口，只保留凭证驱动的主路径

## 当前 app 边界

默认 app 标识与路径边界：

- `WAREHOUSE_APP_ID=knowledge.yeying.pub`
- app 根路径：`/apps/knowledge.yeying.pub`
- 默认上传目录：`/apps/knowledge.yeying.pub/uploads`

所有与 `warehouse` 相关的路径都必须落在当前 app 根目录内。`/personal/*` 等路径会直接被拒绝。

## 凭证模型

当前代码中的主凭证模型是 `WarehouseAccessCredential`。

字段语义：

- `id`
- `owner_wallet_address`
- `credential_kind`
  - `read`
  - `read_write`
- `key_id`
- `encrypted_key_secret`
- `root_path`
- `status`
  - `active`
  - `invalid`
  - `revoked_local`
- `last_verified_at`
- `last_used_at`
- `created_at`
- `updated_at`

实现位置：

- `backend/knowledge/models/entities.py`
- `backend/knowledge/services/warehouse_access.py`

### 读凭证

读凭证用于：

- 浏览绑定源目录
- 预览源文件
- 创建绑定时校验目标路径
- 导入任务读取文件
- evidence 构建时读取源文件

### 写凭证

写凭证用于：

- 上传文件到当前 app 目录
- 作为浏览 app 目录时的默认写口凭证

注意：

- `app_root_write` bootstrap 只保证当前 app 根目录的写入口
- 它不等于“读链路已经完整接通”
- 绑定、导入、evidence 构建等读路径，仍应优先使用显式读凭证

当前实现按“每个钱包一条当前写凭证”维护；重复保存时会覆盖旧写凭证并清理多余记录。

## 认证方式

当前 `WarehouseGateway` 现在只保留 Basic Auth 作为 `warehouse` 访问认证方式：

- `Authorization: Basic base64(ak:sk)`

实现位置：

- `backend/knowledge/services/warehouse.py`

## 凭证导入与校验

### 导入读凭证

接口：

- `POST /warehouse/credentials/read`

行为：

- 校验 `key_id` / `key_secret` 非空
- 校验前缀必须分别为 `ak_` / `sk_`
- 规范化并校验 `root_path` 必须位于当前 app 目录内
- 使用 Basic Auth 访问 `root_path`
- 可访问才保存

### 导入写凭证

接口：

- `POST /warehouse/credentials/write`

行为：

- 同样校验 `ak_` / `sk_`
- `root_path` 必须位于当前 app 目录内
- 会从 `root_path` 向上回溯到 app 根目录，寻找至少一个可访问路径作为校验探针
- 如果探针都不可访问，但当前写 key 能对自己的 `root_path` 完成 bootstrap，仍允许保存
- 保存成功后会立即对写凭证作用域执行最小目录 bootstrap，而不是等待第一次上传
- 成功后保存或覆盖当前写凭证

### Secret 存储

`sk` 不以明文落库，当前使用 `Fernet` 加密后保存。

## 绑定模型

当前 `SourceBinding` 已持久化 `credential_id`，用于把绑定源与读凭证固定关联。

绑定创建时会做以下检查：

- `source_path` 必须在当前 app 目录内
- 若传了 `credential_id`，必须是当前钱包可见的读凭证
- 若未传 `credential_id`
  - 当且仅当当前钱包只有一把读凭证时，后端会自动使用该凭证
  - 若存在多把读凭证，则直接报错
- `source_path` 必须在该读凭证 `root_path` 范围内
- 后端会对目标路径做一次真实可访问性校验
- `scope_type=file` 时目标必须是文件
- `scope_type=directory` 时目标必须是目录

绑定返回结构会附带凭证摘要：

- `credential_id`
- `credential_kind`
- `credential_key_id`
- `credential_key_secret_masked`
- `credential_root_path`
- `credential_status`

## 浏览、预览、上传

### 浏览

接口：

- `GET /warehouse/browse`

参数：

- `path`
- `credential_id`
- `use_write_credential`

行为：

- 优先使用显式 `credential_id`
- 若要求使用写凭证，则走写凭证
- `credential_id` 当前只接受读凭证
- 如果要用写凭证浏览，必须显式传 `use_write_credential=true`

### 预览

接口：

- `GET /warehouse/preview`

行为与浏览一致，但只支持文件。

### 上传

接口：

- `POST /warehouse/upload`

行为：

- 只使用写凭证
- 目标目录必须位于当前 app 目录内
- 若未配置写凭证会直接失败
- 上传前会按“写凭证 `root_path` -> 目标目录”的最小目录链调用 `ensure_app_space()`
- 不再为仅覆盖 `uploads/` 的写 key 额外创建无关 app 子目录

## 任务执行与绑定关系

### 手工任务

接口：

- `POST /kbs/{kb_id}/tasks/import`
- `POST /kbs/{kb_id}/tasks/reindex`
- `POST /kbs/{kb_id}/tasks/delete`

这类任务可以在请求里显式携带 `credential_id`，写入 `stats_json.explicit_credential_id`。

约束：

- 这里的 `credential_id` 现在必须是读凭证
- 后端会在创建任务时校验它是否覆盖当前 `source_paths`

### 绑定驱动任务

接口：

- `POST /kbs/{kb_id}/tasks/import-from-bindings`
- `POST /kbs/{kb_id}/tasks/reindex-from-bindings`
- `POST /kbs/{kb_id}/tasks/delete-from-bindings`

行为：

- 先解析启用中的绑定源
- 绑定缺少 `credential_id` 会直接拒绝创建任务
- 执行阶段按 binding 解析读凭证，不再靠纯路径猜测

## 凭证状态与失败语义

当前凭证状态：

- `active`
- `invalid`
- `revoked_local`

运行期如果遇到 `401/403`：

- 当前访问凭证会被标记为 `invalid`
- 绑定摘要里的 `credential_status` 会变成 `invalid`
- 工作台中的绑定状态会转为 `failed`

当前实现中，`binding` 工作台状态判断规则是：

- `disabled`
- `failed`
  - 绑定缺少凭证
  - 绑定凭证不存在
  - 绑定凭证不是 `active`
  - 最近任务失败或部分成功
- `syncing`
- `indexed`
- `pending_sync`

## 已删除的旧绑定路径

当前仓库已经删除以下旧接口：

- `/warehouse/auth/challenge`
- `/warehouse/auth/verify`
- `/warehouse/auth/ucan/*`
- `/warehouse/auth/apps/ucan/*`
- `/warehouse/auth/binding`
- `/warehouse/auth/status`

当前状态接口为：

- `/warehouse/status`

当前控制台主流程只保留“先导入读/写凭证，再浏览、上传、绑定、导入”。

## 当前实现说明

下面这些点在写文档和测试时需要明确，不要按旧方案假设：

- 读凭证可以有多条，写凭证当前只保留一条
- 绑定创建时 `credential_id` 现在是“强烈建议显式传”，但在只有一把读凭证时可省略
- 浏览与预览对显式凭证要求比旧版本严格，不再接受通过 `credential_id` 显式传写凭证
- source scan / task / evidence 最多只会自动匹配读凭证，不再回退到写凭证
- 旧钱包绑定接口已经删除，不应再写成兼容能力

## 相关代码

- `backend/knowledge/api/routes_warehouse.py`
- `backend/knowledge/api/routes_tasks.py`
- `backend/knowledge/services/warehouse_access.py`
- `backend/knowledge/services/warehouse.py`
- `backend/knowledge/services/bindings.py`
