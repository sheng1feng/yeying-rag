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

### `POST /warehouse/bootstrap/challenge`

用途：

- 请求当前钱包在 `warehouse` 侧的 challenge

### `POST /warehouse/bootstrap/initialize`

输入：

```json
{
  "mode": "uploads_bundle",
  "signature": "0x..."
}
```

返回：

```json
{
  "attempt_id": 12,
  "status": "succeeded",
  "stage": "completed",
  "mode": "uploads_bundle",
  "mode_label": "uploads 读写凭证",
  "target_path": "/apps/knowledge.yeying.pub/uploads",
  "write_key_id": "ak_xxx",
  "read_key_id": "ak_yyy",
  "warnings": [],
  "cleanup_status": "not_needed"
}
```

当前状态语义：

- `succeeded`
  - 写凭证 / 读凭证都已回填完成
- `partial_success`
  - 当前 bootstrap 已经产生部分结果，最常见是写凭证已经回填，但读凭证未完成
- `failed`
  - 当前 bootstrap 没有完成，仍应根据返回字段检查是否已生成上游 key 或本地部分状态

当前阶段字段 `stage` 用于表达最近停在哪一步，例如：

- `verifying_signature`
- `creating_write_key`
- `binding_write_key`
- `ensuring_directories`
- `saving_write_credential`
- `creating_read_key`
- `binding_read_key`
- `saving_read_credential`
- `completed`

说明：

- `partial_success` 当前作为正常 JSON 响应返回，前端应展示告警而不是把它当成纯成功。
- `cleanup_status` 表达当前 attempt 的补偿清理状态。

### `GET /warehouse/bootstrap/attempts`

返回当前钱包最近的 bootstrap attempt 列表。

参数：

- `limit`

返回字段示例：

- `id`
- `mode`
- `target_path`
- `status`
- `stage`
- `write_key_id`
- `read_key_id`
- `write_credential_id`
- `read_credential_id`
- `details_json`
- `warnings`
- `cleanup_status`
- `created_at`
- `updated_at`

### `GET /warehouse/bootstrap/attempts/{attempt_id}`

返回当前钱包下指定 bootstrap attempt 的详情。

约束：

- 只能读取当前钱包自己的 attempt
- 其他钱包的 attempt 返回 `404`

### `POST /warehouse/bootstrap/attempts/{attempt_id}/cleanup`

用途：

- 对当前钱包下的 bootstrap attempt 执行 cleanup

输入：

```json
{
  "signature": "0x..."
}
```

当前行为：

- 后端会用当前钱包签名换取上游 `warehouse` token
- 然后调用 `POST /api/v1/public/webdav/access-keys/revoke`
- 成功后把本地关联凭证标记为 `revoked_local`
- 如果当前 attempt 不需要清理，则返回当前 attempt 状态，不重复执行 revoke

当前 `cleanup_status` 语义：

- `not_needed`
  - 当前 attempt 不需要补偿清理
- `manual_cleanup_required`
  - 当前 attempt 已生成远端 key，仍需要执行 cleanup
- `cleanup_completed`
  - 当前 attempt 关联的远端 key 已通过 `knowledge` 后端代理调用 `warehouse` revoke 接口完成撤销

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
说明：

- `credential_id` 当前只接受读凭证
- 如果要用写凭证，必须显式传 `use_write_credential=true`


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
- 传入时必须是读凭证
- 后端会校验该读凭证是否覆盖 `source_paths`
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
