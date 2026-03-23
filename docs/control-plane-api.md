# Knowledge 控制面 API 文档

本文档覆盖控制台和测试常用的控制面接口。

不包含：

- `service search` 面向外部服务的读面接口

那部分继续参考 `docs/api-integration.md`。

## 1. 登录

### `POST /auth/challenge`

输入：

```json
{
  "wallet_address": "0x..."
}
```

用途：

- 获取钱包签名 challenge

### `POST /auth/verify`

输入：

```json
{
  "wallet_address": "0x...",
  "signature": "0x..."
}
```

输出：

- `knowledge JWT`

## 2. Warehouse 凭证管理

### 2.1 读凭证

#### `GET /warehouse/credentials/read`

返回当前钱包的读凭证列表。

#### `POST /warehouse/credentials/read`

输入：

```json
{
  "key_id": "ak_xxx",
  "key_secret": "sk_xxx",
  "root_path": "/apps/knowledge.yeying.pub/library/contracts"
}
```

行为：

- 校验 key 格式
- 校验 `root_path` 在当前 app 目录内
- 用 Basic Auth 访问目标路径
- 可访问才保存

#### `GET /warehouse/credentials/read/{credential_id}/secret`

返回明文 `sk`。

#### `DELETE /warehouse/credentials/read/{credential_id}`

删除读凭证。

约束：

- 如果仍被 `SourceBinding` 引用，会返回冲突

### 2.2 写凭证

#### `GET /warehouse/credentials/write`

返回当前钱包的写凭证摘要：

```json
{
  "configured": true,
  "credential": {
    "id": 1,
    "credential_kind": "read_write",
    "key_id": "ak_xxx",
    "key_secret_masked": "sk_t****rite",
    "root_path": "/apps/knowledge.yeying.pub",
    "status": "active"
  }
}
```

#### `POST /warehouse/credentials/write`

输入与读凭证相同，但语义为写凭证配置。

行为：

- 当前钱包只保留一条写凭证
- 再次保存会覆盖旧写凭证

#### `GET /warehouse/credentials/write/secret`

返回当前写凭证的明文 `sk`。

#### `DELETE /warehouse/credentials/write`

删除当前写凭证。

## 3. Warehouse 浏览与上传

### `GET /warehouse/status`

返回当前 `warehouse` 绑定状态摘要。

字段示例：

- `credentials_ready`
- `read_credentials_count`
- `write_credential_id`
- `write_credential_status`
- `current_app_root`

### `GET /warehouse/browse`

参数：

- `path`
- `credential_id`
- `use_write_credential`

示例：

```http
GET /warehouse/browse?path=/apps/knowledge.yeying.pub/uploads&credential_id=2
```

或：

```http
GET /warehouse/browse?path=/apps/knowledge.yeying.pub/uploads&use_write_credential=true
```

### `GET /warehouse/preview`

参数与浏览一致，但只支持文件。

### `POST /warehouse/upload`

表单字段：

- `file`
- `target_dir`

说明：

- 只使用写凭证
- 没有写凭证时直接失败

### `GET /warehouse/uploads`

返回最近上传记录。

## 4. 绑定源

### `GET /kbs/{kb_id}/bindings`

返回当前知识库绑定源列表，以及每个绑定的凭证摘要和同步状态。

### `POST /kbs/{kb_id}/bindings`

输入：

```json
{
  "source_path": "/apps/knowledge.yeying.pub/library/contracts",
  "scope_type": "directory",
  "credential_id": 2
}
```

说明：

- `credential_id` 当前允许为空，但仅当当前钱包只有一把读凭证时可自动推断
- 推荐始终显式传入

校验规则：

- `source_path` 必须位于当前 app 目录内
- 必须在读凭证 `root_path` 范围内
- 目标路径必须真实存在且类型匹配

### `PATCH /kbs/{kb_id}/bindings/{binding_id}`

输入：

```json
{
  "enabled": false
}
```

### `DELETE /kbs/{kb_id}/bindings/{binding_id}`

只删除绑定，不删除原文件，也不删除读凭证。

## 5. 导入任务

### 手工任务

- `POST /kbs/{kb_id}/tasks/import`
- `POST /kbs/{kb_id}/tasks/reindex`
- `POST /kbs/{kb_id}/tasks/delete`

输入：

```json
{
  "source_paths": [
    "/apps/knowledge.yeying.pub/uploads/demo.txt"
  ],
  "credential_id": 2
}
```

说明：

- `credential_id` 可选
- 传入时会记录到 `stats_json.explicit_credential_id`

### 按绑定源创建任务

- `POST /kbs/{kb_id}/tasks/import-from-bindings`
- `POST /kbs/{kb_id}/tasks/reindex-from-bindings`
- `POST /kbs/{kb_id}/tasks/delete-from-bindings`

输入：

```json
{
  "binding_ids": [1, 2]
}
```

说明：

- 不传 `binding_ids` 时默认取全部启用中的绑定源
- 绑定缺少凭证会直接报错
- `stats_json.created_from = "bindings"`

### 任务查询与处理

常用接口：

- `GET /tasks`
- `GET /tasks/{task_id}`
- `GET /tasks/{task_id}/items`
- `POST /tasks/{task_id}/retry`
- `POST /tasks/{task_id}/cancel`
- `POST /tasks/process-pending`

## 6. Source / Asset 治理

如果使用 `Source` 主线而不是 `Binding` 主线，常用接口还有：

- `POST /kbs/{kb_id}/sources`
- `GET /kbs/{kb_id}/sources`
- `GET /kbs/{kb_id}/sources/{source_id}`
- `PATCH /kbs/{kb_id}/sources/{source_id}`
- `POST /kbs/{kb_id}/sources/{source_id}/scan`
- `GET /kbs/{kb_id}/sources/{source_id}/assets`
- `GET /kbs/{kb_id}/assets`

这些接口同样遵守当前 app 路径边界。

## 7. 运维接口

常用接口：

- `GET /ops/overview`
- `GET /ops/stores/health`
- `GET /ops/workers`
- `GET /ops/tasks/failures`

说明：

- 都要求先登录并携带 `knowledge JWT`

## 8. 当前状态字段说明

### 凭证状态

- `active`
- `invalid`
- `revoked_local`

### 绑定状态

- `pending_sync`
- `syncing`
- `indexed`
- `failed`
- `disabled`

### 任务状态

- `pending`
- `running`
- `succeeded`
- `failed`
- `partial_success`
- `cancel_requested`
- `canceled`

## 9. 相关文档

- `docs/api-integration.md`
- `docs/warehouse-auth-refactor.md`
- `docs/warehouse-credential-usage.md`
- `docs/console-operations.md`
