# Warehouse 访问全景与问题排查

本文档基于当前仓库真实代码整理，目标是把 `warehouse` 访问相关的关键事实集中写清楚，包括：

- `warehouse` 在整个项目里的角色
- 前端、控制面 API、后台任务分别怎样访问 `warehouse`
- 当前的鉴权、路径收口和凭证解析规则
- `mock` / `bound_token` 两种模式的真实差异
- 常见问题、失败语义和容易漏掉的行为

如果本文档与旧描述冲突，应以当前代码为准。

## 1. 先说结论

当前项目是一个围绕 `warehouse` 作为唯一资产中心的知识运营后端。

主链路不是“上传文档后直接搜索”，而是：

1. 用户登录 `knowledge`
2. 用户导入 `warehouse` 读凭证 / 写凭证，或通过 bootstrap 初始化
3. 浏览 `warehouse` 当前 app 目录，上传文件，或绑定已有目录
4. 从绑定路径或显式路径创建导入 / 重建 / 删除任务
5. worker 再去 `warehouse` 读取文件，生成 document / chunk / embedding
6. 再构建 evidence、candidate、knowledge item、release、grant
7. 对外服务搜索只读取数据库中的 release / item / evidence，不再直接访问 `warehouse`

这意味着：

- `warehouse` 是源资产中心
- `knowledge` 不是资产存储中心
- `warehouse` 访问并不只发生在 `/warehouse/*` 控制面接口
- 任务执行、source scan、evidence build 这些后台流程也会继续访问 `warehouse`

## 项目功能模块一览

为了理解 `warehouse` 在哪里被访问，先把整个项目的主要功能面简单摆平：

- 登录与控制台
  - 钱包 challenge / verify / refresh / logout
  - 控制台首页、状态面板、操作按钮
- 知识库管理
  - KB CRUD、stats、workbench
- `warehouse` 控制面
  - bootstrap、凭证管理、浏览、预览、上传、binding
- Source / Asset 治理
  - source CRUD、scan、asset 列表
- 导入任务与 worker
  - import / reindex / delete、按 binding 批量创建任务、worker 执行
- Evidence / Candidate / Item
  - evidence build、候选生成、item 接受/拒绝/手工维护
- Release / Grant / Service Search
  - 发布、授权、对外检索
- Search Lab / Retrieval Logs / Source Governance
  - 检索对比、日志审计、来源健康度
- Memory / Ops
  - 长短期记忆、worker / store / failure 运维视图

其中真正会反复回源访问 `warehouse` 的，主要集中在：

- `warehouse` 控制面
- source scan
- worker 导入链路
- evidence build

## 2. 项目里 `warehouse` 的职责边界

### 2.1 `warehouse` 负责什么

- 作为唯一资产中心保存原始目录和文件
- 提供 WebDAV 读写能力
- 提供 access key 创建与目录绑定能力
- 在 bootstrap 场景下提供钱包 challenge / verify 能力

### 2.2 `knowledge` 负责什么

- 保存本地的读凭证 / 写凭证摘要与密文
- 限制所有路径只能落在当前 app 根目录
- 用导入任务把原始文件转成文档、chunk、embedding
- 基于文档继续构建 evidence、candidate、item、release、grant
- 提供控制台、worker、审计和检索读面

### 2.3 什么不在当前 `knowledge` 的职责里

- 不负责替代 `warehouse` 作为文件主存储
- 不负责把 chunk、embedding、item 回写到 `warehouse`
- 不再保留旧 `/warehouse/auth/*` 长期绑定主流程

## 3. 当前 app 边界

所有 `warehouse` 相关路径都被限制在当前 app 根目录下。

默认配置下：

- app id：`knowledge.yeying.pub`
- app root：`/apps/knowledge.yeying.pub`
- 默认上传目录：`/apps/knowledge.yeying.pub/uploads`

当前代码中的统一规则是：

- 任何传给后端的 `warehouse` 路径都会先标准化
- 然后校验它必须等于 app root，或者位于 app root 之下
- `/personal/*`、其他 app、根目录外路径都会被直接拒绝

因此现在的 `warehouse` 访问模型不是“任意浏览用户资产”，而是“只浏览当前 Knowledge App 的资产空间”。

## 4. 两种 `warehouse` 网关模式

当前代码只支持两种网关模式。

### 4.1 `mock`

用途：

- 本地开发
- 测试
- 不依赖真实 `warehouse`

行为：

- 把用户资产映射到本地目录
- 路径大致落在 `WAREHOUSE_MOCK_ROOT/<wallet>/apps/<app_id>/...`
- 浏览、上传、读取最终走本地文件系统
- 底层 gateway 几乎不真正校验 `ak/sk`
- 但应用层仍保留完整的“读凭证 / 写凭证 / 路径覆盖 / binding”语义

这意味着：

- 业务流程和线上尽量保持一致
- 但 `401/403`、真实绑定目录问题、远端 WebDAV 语义不会完整复现

### 4.2 `bound_token`

用途：

- 真实环境
- 对接上游 `warehouse`

行为：

- 浏览目录：`PROPFIND`
- 建目录：`PROPFIND` + `MKCOL`
- 上传文件：`PUT`
- 读取文件：`GET`
- 常规读写请求使用 Basic Auth `ak/sk`
- bootstrap 特殊路径使用 Bearer token 访问 `warehouse` 公共 API

这意味着：

- 控制台上的一次“浏览”“预览”“绑定”动作，底层通常不止一个请求
- 目录层级越深，递归扫描和递归导入的请求量越大

## 5. 当前的凭证模型

项目本地保存的是 `WarehouseAccessCredential`。

核心字段语义：

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

### 5.1 读凭证的用途

- 浏览目录
- 预览文件
- 创建 binding
- source scan
- import / reindex / delete 时读取源文件
- evidence build 时读取源文件

### 5.2 写凭证的用途

- 上传到当前 app 目录
- 作为浏览 app 目录时的写口凭证
- 在没有更合适的读凭证时，部分后台流程允许回退使用

### 5.3 当前实现的重要事实

- 一个钱包可以有多把读凭证
- 一个钱包当前只保留一把写凭证
- 读写凭证都要求 `root_path` 落在当前 app 目录内
- `sk` 落库前会加密，不是明文保存

## 6. 鉴权与路径解析总规则

`warehouse` 访问的解析顺序，需要分场景看。

### 6.1 浏览 / 预览

优先级：

1. 如果显式传了 `credential_id`，优先用这把凭证
2. 如果显式要求 `use_write_credential=true`，使用写凭证
3. 如果没传参数，但当前写凭证覆盖目标路径，自动使用写凭证
4. 否则报错，提示必须提供 `credential_id` 或写凭证

### 6.2 创建 binding

优先级：

1. 如果请求里带 `credential_id`，必须是当前钱包的读凭证
2. 如果没带，且当前钱包只有一把读凭证，则自动推断
3. 如果没带且存在多把读凭证，直接报错

同时必须满足：

- `source_path` 在当前 app 目录内
- `source_path` 位于该读凭证 `root_path` 下
- 路径真实存在
- `scope_type` 和真实路径类型匹配

### 6.3 后台读文件流程

对 source scan、导入任务、evidence build 这类后台读路径，优先级是：

1. 显式 `credential_id`
2. binding 匹配出的读凭证
3. 允许回退时，使用写凭证

这里最重要的事实是：

- `Source` 本身不带 `credential_id`
- 真正绑定权限的是 `SourceBinding`
- 后台流程会根据路径去找“最合适的 binding”

### 6.4 成功与失败后的本地状态变化

访问成功：

- 更新 `last_used_at`
- 把凭证状态保持或改回 `active`

访问出现 `401/403`：

- 当前使用的本地凭证会被标记为 `invalid`
- 后续 binding 工作台会表现为失败

## 7. `warehouse` 访问入口总表

这里必须先区分两件事：

- `warehouse` 相关 API
  - 路径、凭证、binding、上传记录这些都归在这一类
- 真正会发起上游 `warehouse` I/O 的 API
  - 只有这类才会触发真实 WebDAV 或 `warehouse` 公共 API 请求

如果不区分这两类，就会把很多纯本地数据库接口误判成“正在访问仓库”。

### 7.1 仓库相关但只读写本地状态的接口

这些接口属于 `warehouse` 领域，但默认不直接访问上游 `warehouse`：

- `/warehouse/status`
- `GET /warehouse/credentials/read`
- `GET /warehouse/credentials/read/{credential_id}/secret`
- `DELETE /warehouse/credentials/read/{credential_id}`
- `GET /warehouse/credentials/write`
- `GET /warehouse/credentials/write/secret`
- `DELETE /warehouse/credentials/write`
- `/warehouse/uploads`
- `GET /kbs/{kb_id}/bindings`
- `PATCH /kbs/{kb_id}/bindings/{binding_id}`
- `DELETE /kbs/{kb_id}/bindings/{binding_id}`
- `/kbs/{kb_id}/tasks/import`
- `/kbs/{kb_id}/tasks/reindex`
- `/kbs/{kb_id}/tasks/delete`
- `/kbs/{kb_id}/tasks/*-from-bindings`

说明：

- 它们可能会读写本地数据库
- 也可能改变后续 worker 的执行计划
- 但调用本身不一定立即触发上游 `warehouse` I/O

### 7.2 会立即访问上游 `warehouse` 的控制面接口

这些接口在请求执行过程中会直接访问上游 `warehouse` 或 mock gateway：

- `/warehouse/bootstrap/challenge`
- `/warehouse/bootstrap/initialize`
- `POST /warehouse/credentials/read`
- `POST /warehouse/credentials/write`
- `/warehouse/browse`
- `/warehouse/upload`
- `/warehouse/preview`
- `POST /kbs/{kb_id}/bindings`

说明：

- `POST /warehouse/credentials/read`
  - 会用 Basic Auth 探测 `root_path`
- `POST /warehouse/credentials/write`
  - 会探测路径并尝试最小目录 bootstrap
- `POST /kbs/{kb_id}/bindings`
  - 会做真实存在性校验，而不是只写一条 binding 记录

### 7.3 会间接或延迟访问上游 `warehouse` 的接口

这些接口本身不一定马上读文件，但它们会驱动后续真实仓库访问，或者自身内部会触发一部分回源流程：

- `/kbs/{kb_id}/sources/{source_id}/scan`
- `/tasks/process-pending`
- `/kbs/{kb_id}/assets/{asset_id}/build-evidence`
- `/kbs/{kb_id}/sources/{source_id}/build-evidence`
- `PATCH /kbs/{kb_id}` 在 chunking / embedding 配置变化时触发的 rescan / rebuild

这里面最容易误判的是：

- `/tasks/process-pending`
  - 不是简单的“队列刷新”
  - 它会直接驱动 worker 跑任务
- `PATCH /kbs/{kb_id}`
  - 不是纯元数据更新
  - 它既会创建后续 reindex 任务，也会直接触发 source scan 与 evidence rebuild

### 7.4 明确不会直接访问 `warehouse` 的读面

这些接口主要消费本地数据库快照：

- `/service/search`
- `/service/search/formal`
- `/service/search/evidence`
- `/service/grants`
- `/service/kbs`
- `/service/releases/current`

也就是说，服务搜索不应该再去读 `warehouse` 原文件。

## 8. 端到端调用链

下面按常见动作拆开。

### 8.1 冷启动 bootstrap

前端动作：

- 控制台点击“初始化 uploads 读写凭证（推荐）”
- 或点击“只初始化 app 根写凭证（高级）”

前端流程：

1. 调 `POST /warehouse/bootstrap/challenge`
2. 用当前登录钱包签 challenge
3. 调 `POST /warehouse/bootstrap/initialize`

后端流程：

1. 请求上游 `warehouse` challenge
2. 用签名换 Bearer token
3. 调上游 access key create
4. 调上游 access key bind
5. 用 Bearer token 创建目录链
6. 回填本地写凭证
7. 如为 `uploads_bundle`，再创建并回填一把读凭证

要点：

- 当前主流程是“浏览器签名 + knowledge 后端代理调用上游”
- 不是浏览器直接全量操作 `warehouse`
- 如果返回 `manual_cleanup_required`，前端当前会显示 cleanup 入口，允许再次签名后撤销本次 bootstrap 生成的远端 key
- 前端的 `warehouse_bridge.js` 仍保留了浏览器直连 helper，但当前控制台主流程实际走的是后端代理 bootstrap

### 8.2 导入读凭证

前端动作：

- 在控制台填写 `ak`、`sk`、`root_path`

后端流程：

1. 校验 `key_id` / `key_secret` 非空
2. 强制要求 `ak_` / `sk_` 前缀
3. 校验 `root_path` 位于当前 app 目录内
4. 用 Basic Auth 探测该路径是否可访问
5. 可访问才保存

常见失败原因：

- `root_path` 在 app 根之外
- `ak/sk` 填错
- key 还没在 `warehouse` 绑定任何目录
- key 绑定的目录不覆盖当前 `root_path`

### 8.3 保存写凭证

后端流程：

1. 校验 `ak_` / `sk_`
2. 校验 `root_path` 在当前 app 目录内
3. 从 `root_path` 往上回溯到 app root，尝试找可访问探针路径
4. 如果已有探针可访问，则立刻执行最小目录 bootstrap
5. 如果探针都不可访问，但当前 key 能直接创建自己的目标目录链，也允许保存
6. 覆盖当前写凭证

这块的关键差异是：

- 写凭证允许“当前还没有目录，但这把 key 有能力创建目录”的场景
- 所以写凭证的校验逻辑比读凭证更宽

### 8.4 浏览目录

前端动作：

- 控制台点击浏览目录

后端流程：

1. 校验目标 `path` 在当前 app 根下
2. 解析浏览权限
3. 调 `WarehouseGateway.browse`
4. 成功后标记凭证 `active`
5. 返回目录项

底层动作：

- `mock`：读取本地目录
- `bound_token`：对目标路径发送 `PROPFIND Depth: 1`

### 8.5 预览文件

后端流程不是单跳：

1. 先做 browse 权限解析
2. 先 `browse(path)` 找到精确 entry
3. 再 `read_file(path)`
4. 再做文档解析，截断前 4000 字作为 preview

因此一次“预览”在真实 WebDAV 模式下一般至少是两次上游访问。

### 8.6 上传文件

后端流程：

1. 必须存在写凭证
2. `target_dir` 必须位于当前 app 目录内
3. 基于写凭证解析写权限
4. 先确保从写凭证 `root_path` 到目标目录的最小目录链存在
5. 执行上传
6. 记录本地 `UploadRecord`

底层动作：

- `mock`：本地写文件
- `bound_token`：必要时 `MKCOL`，然后 `PUT`

### 8.7 创建 binding

后端流程：

1. 校验 `source_path` 在当前 app 目录内
2. 解析或推断读凭证
3. 校验 `source_path` 被该读凭证 `root_path` 覆盖
4. 调用 `path_exists_with_auth`
5. 根据真实 entry 判定 `scope_type`
6. 写入或更新 `SourceBinding`

`path_exists_with_auth` 自身并不轻量，它会：

- 先试目标路径 browse
- 必要时再试父目录 browse
- 在某些场景下还会再次试目标路径

因此 binding 创建的网络请求数通常高于 1。

### 8.8 Source Scan

入口：

- `/kbs/{kb_id}/sources/{source_id}/scan`

执行链：

1. `SourceSyncService.scan_source`
2. `AssetInventoryService.list_asset_snapshots`
3. `resolve_path_read_access`
4. 递归 `browse`
5. 根据目录结构生成 `SourceAsset` 快照

特点：

- 它不是只读数据库，而是真实遍历 `warehouse`
- 目录越深、文件越多，请求越多
- `scope_type=file` 与 `scope_type=directory` 路径类型不匹配会直接失败

### 8.9 导入 / 重建 / 删除任务

任务创建阶段：

- 只校验 `source_paths` 都在当前 app 目录内
- 不会在创建接口里立刻把所有文件读出来

真正读 `warehouse` 的是 worker 执行阶段。

执行链：

1. `IngestionService.process_task`
2. 对每个 source_path 递归 `browse`
3. 找到文件后对每个文件 `read_file`
4. 解析文本、切 chunk、写 embedding / vector

如果是 `import`：

- 同版本文件会跳过

如果是 `reindex`：

- 会重新读取、重切 chunk、重建向量

如果是 `delete`：

- 不再读取 `warehouse` 文件内容
- 主要删除本地 document / chunk / vector 状态

### 8.10 Evidence Build

入口：

- `/kbs/{kb_id}/assets/{asset_id}/build-evidence`
- `/kbs/{kb_id}/sources/{source_id}/build-evidence`

执行链：

1. 解析读权限
2. 对 asset 对应原文件再次 `read_file`
3. 解析文本、切 chunk
4. 重建本地 `EvidenceUnit`
5. 建 evidence 向量

重要事实：

- evidence build 不会复用导入时缓存的原始文件内容
- 它会重新回到 `warehouse` 读取源文件

## 9. 当前哪些模块会真实访问 `warehouse`

按服务层看，真正会发起 `warehouse` 访问的模块主要有：

- `WarehouseAccessService`
  - 用于校验凭证、探测路径、解析授权
- `WarehouseBootstrapService`
  - 用于冷启动初始化
- `AssetInventoryService`
  - 用于 source scan
- `IngestionService`
  - 用于 import / reindex
- `EvidencePipelineService`
  - 用于 evidence build

按业务上看：

- 控制台浏览
- 控制台预览
- 控制台上传
- binding 创建时的存在性校验
- source scan
- 任务执行
- evidence build

## 10. 当前哪些模块不再访问 `warehouse`

以下能力不应直接访问 `warehouse` 原文件：

- 已导入 document 列表与详情
- item / candidate 管理
- release 发布、hotfix、rollback
- grant 管理
- service search
- retrieval logs
- ops 统计面板

这些模块的输入已经转为数据库状态与发布快照。

## 11. `bound_token` 模式下的上游请求矩阵

### 11.1 常规控制面 / 后台读写

认证：

- `Authorization: Basic base64(ak:sk)`

操作：

- 浏览：`PROPFIND`
- 读文件：`GET`
- 上传：`PUT`
- 建目录：`MKCOL`

### 11.2 Bootstrap 特殊链路

认证：

- 先用钱包签名换 Bearer token
- 再用 `Authorization: Bearer <token>`

上游 API：

- `POST /api/v1/public/auth/challenge`
- `POST /api/v1/public/auth/verify`
- `POST /api/v1/public/webdav/access-keys/create`
- `POST /api/v1/public/webdav/access-keys/bind`

随后再用 Bearer token 创建目录链。

## 12. 常见问题与失败语义

### 12.1 为什么导入读凭证时报 `root_path must be under /apps/...`

原因：

- 当前实现强制 app-only
- 不允许直接绑定 `/personal/*` 或其他 app

这不是权限不足，而是后端业务规则直接拒绝。

### 12.2 为什么保存读凭证时报 `401 Unauthorized`

最常见原因：

- `ak` 或 `sk` 错了
- key 已撤销或过期
- key 虽然创建了，但还没有在 `warehouse` 绑定任何目录

在当前 `warehouse` 语义里：

- “创建 access key” 不等于“已经能访问目录”
- 必须额外完成目录绑定

### 12.3 为什么保存读凭证或绑定时报 `403 Forbidden`

通常表示：

- `warehouse` 已识别这把 key
- 但当前访问路径不在它绑定的目录范围内
- 或权限位不够

要检查：

- `root_path` 是否真的落在已绑定目录内
- 读 key 是否具备 `read`
- 写 key 是否具备 `create/update`

### 12.4 为什么浏览能成功，但绑定失败

可能原因：

- 浏览时你用的是写凭证
- 绑定时要求的是读凭证
- 这两把凭证覆盖路径不同

当前控制台里“浏览使用的凭证”和“binding 使用的凭证”是两套选择，不应混为一谈。

### 12.5 为什么上传成功，但后续导入失败

常见原因：

- 只配置了写凭证，没有配置覆盖该文件路径的读凭证
- 导入任务后台解析不到 binding 对应读凭证
- 后台只能回退到写凭证，而这条路径或当前模式不适合长期依赖该回退

实践上：

- 上传成功不代表读链路已经完整
- 对长期稳定流程，应补一把覆盖上传目录的读凭证

### 12.6 为什么 binding 看起来正常，但任务执行时报凭证失效

可能原因：

- binding 创建时 key 还有效
- 后续 key 被撤销、权限被改、绑定目录被改
- 后台真正读取文件时遇到 `401/403`

结果：

- 本地凭证状态会被打成 `invalid`
- binding 工作台会转成失败态

### 12.7 为什么 `preview` 或 `binding` 的请求看起来比想象中多

因为它们底层不是单步：

- `preview` 通常是 `browse + read_file`
- `binding` 的存在性检查通常会尝试目标路径和父目录

这在真实 WebDAV 环境里会放大请求数。

### 12.8 为什么 source scan 很慢

因为它会：

- 递归列目录
- 每层都做 browse
- 目录越深，请求越多

如果目录结构非常深或文件很多，扫描成本会明显上升。

### 12.9 为什么 evidence build 还会回源读取文件

因为当前实现没有把原始全文当成长期可信缓存复用。

evidence build 的真实语义是：

- 重新从 `warehouse` 读源文件
- 重新解析
- 重新切块
- 重建 evidence

### 12.10 为什么改 KB 的 chunk 配置也会影响 `warehouse`

因为 `PATCH /kbs/{kb_id}` 不是纯元数据更新。

只要影响 chunking / embedding 的配置发生变化，当前代码就会：

1. 视情况排一个 `reindex` 任务
2. 重扫 source
3. 重建 evidence

因此它会间接重新访问 `warehouse`。

### 12.11 为什么 `Source` 没有 `credential_id`，却还能扫描

因为当前权限锚点不是 `Source`，而是：

- 显式任务参数里的 `credential_id`
- 或 `SourceBinding.credential_id`
- 或自动匹配到的 active 读凭证

所以 `Source` 更像领域对象，真正访问授权仍由 binding 与 access service 决定。

## 13. 容易漏掉的行为

### 13.1 `/tasks/process-pending` 不只是“队列按钮”

它会直接触发 worker 执行一次 pending 任务。

只要执行到了 import / reindex / evidence rebuild，就会真实访问 `warehouse`。

### 13.2 `delete` 任务和 `import` 任务的 `warehouse` 访问量不同

- `import` / `reindex` 需要 browse + read_file
- `delete` 更偏本地索引清理

所以“任务接口都一样会打仓库”这个理解不完全准确。

### 13.3 当前读路径已经不再回退到写凭证

当前后台的 browse / preview / source scan / task / evidence 读链路已经收口到：

- 显式读凭证
- binding 绑定的读凭证
- 自动匹配路径范围内的 active 读凭证

也就是说，写凭证现在只承担写路径和显式写浏览，不再作为后台读路径兜底。

长期推荐仍然是：

- 浏览 / 绑定 / 后台读取走读凭证
- 上传走写凭证

### 13.4 `warehouse_bridge.js` 不是当前控制台主路径

当前控制台 bootstrap 主流程走：

- 前端请求 `knowledge` 后端 challenge
- 浏览器签名
- 后端代理调用上游 `warehouse`

`warehouse_bridge.js` 现在更像保留的浏览器侧 helper，而不是主链路执行器。

## 14. 推荐操作规范

### 14.1 推荐最小可用组合

至少准备：

- 一把写凭证，覆盖 app 根或上传目录
- 一把读凭证，覆盖你要绑定和导入的目录

### 14.2 推荐目录规划

建议把用途拆开：

- `/apps/<app_id>/uploads`
  - 临时上传
- `/apps/<app_id>/library/...`
  - 长期绑定与同步目录
- `/apps/<app_id>/exports`
  - 后续导出用途

### 14.3 推荐权限规划

- 读 key 尽量缩到具体目录
- 写 key 只给当前 app 自己的最小写范围
- 不要把宽权限写 key 同时当通用读 key 复用

### 14.4 推荐排障顺序

出现问题时先检查：

1. 当前路径是否还在 app 根下
2. 当前动作到底用的是哪把凭证
3. 该凭证状态是否已变成 `invalid`
4. 该凭证 `root_path` 是否覆盖目标路径
5. 上游 key 是否仍存在、是否仍绑定目录
6. 如果是后台任务，是否其实走了 binding 或 write fallback

## 15. 一份最简心智模型

如果只保留一句话，可以这样理解当前系统：

`knowledge` 并不拥有用户资产，它只是用当前 app 边界内的一组受限 `warehouse` 凭证，把原始文件转成可运营、可发布、可授权、可检索的知识快照。

而所有 `warehouse` 访问问题，本质上都可以回到四个问题来排查：

1. 路径是否在当前 app 根内
2. 当前动作最终用了哪把凭证
3. 这把凭证是否真的覆盖该路径
4. 当前动作是只发生在控制面，还是已经进入后台递归读取链路

## 16. 相关代码入口

判断当前实现时，优先看这些文件：

- `backend/knowledge/api/routes_warehouse.py`
- `backend/knowledge/services/warehouse_access.py`
- `backend/knowledge/services/warehouse.py`
- `backend/knowledge/services/warehouse_scope.py`
- `backend/knowledge/services/warehouse_bootstrap.py`
- `backend/knowledge/services/asset_inventory.py`
- `backend/knowledge/services/source_sync.py`
- `backend/knowledge/services/ingestion.py`
- `backend/knowledge/services/evidence_pipeline.py`
- `backend/knowledge/static/js/app.js`
