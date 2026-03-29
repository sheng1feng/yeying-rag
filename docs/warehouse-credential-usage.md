# Warehouse 凭证使用说明

本文档面向测试、运营和开发同学，说明如何在当前 `knowledge` 控制台中使用 `warehouse` 的读凭证与写凭证。

## 核心原则

- 先在 `warehouse` 创建受限 `ak/sk`
- 再把 key 手工导入 `knowledge`
- 读写分离
- 路径始终限制在当前 app 目录下

默认 app 目录：

- `/apps/knowledge.yeying.pub`

## 两类凭证

### 读凭证

用途：

- 浏览源目录
- 预览文件
- 创建绑定源
- 导入、重建、evidence 构建时读取文件

建议：

- 一把读凭证对应一个绑定源或一类目录
- 不要把同一把读凭证复用到无关目录

### 写凭证

用途：

- 上传文件到当前 app 目录
- 在控制台里作为写入口浏览凭证

建议：

- 单独配置一把 app 级写凭证
- 不要把写凭证当通用读凭证来发放

## 在 `warehouse` 中准备 key 的建议

建议至少准备两把 key：

1. 一把只读 key
   - `root_path` 指向某个需要绑定的目录或文件上级目录
2. 一把读写 key
   - `root_path` 指向当前 app 根目录或上传目录

安全建议：

- 读 key 尽量缩小到单个业务目录
- 写 key 只覆盖 `knowledge` 自己需要写入的目录
- 不要把宽权限 key 同时用于多个项目或多个 app

## 在 `knowledge` 中导入凭证

### 临时初始化（推荐冷启动）

如果当前还没有可用的 `ak/sk`，并且 `warehouse` 中也还没有 `knowledge` 对应的 app 目录，可先在 `knowledge` 控制台里使用：

- “初始化 uploads 读写凭证（推荐）”
- 或“只初始化 app 根写凭证（高级）”

该流程会在浏览器里临时连接 `warehouse`，自动完成：

1. 以当前钱包登录 `warehouse`
2. 创建 access key
3. 将 future path 绑定到 key
4. 在 `warehouse` 中创建 `/apps/knowledge.yeying.pub` 或 `uploads/` 目录
5. 把生成的 `ak/sk` 自动回填到 `knowledge`

说明：

- 临时 `warehouse` token 只保存在当前浏览器 `sessionStorage`
- 不会落到 `knowledge` 后端数据库
- 推荐优先使用 `uploads` 模式，它会同时回填一把 uploads 写凭证和一把 uploads 读凭证
- `app 根写凭证` 模式只保证 app 根目录的写入口，默认不会补读凭证；后续绑定、浏览现有源目录和导入任务仍建议按目录最小权限单独创建读凭证
- 当前 bootstrap 响应已经带 `attempt_id`、`status`、`stage`
- 如果看到 `partial_success`，通常表示写凭证已经回填，但读凭证未完整完成；此时不应把这次初始化当成完全成功
- 如果看到 `manual_cleanup_required`，说明本次 bootstrap 仍保留远端 access key；当前控制台已经提供撤销入口
- 如果看到 `cleanup_completed`，说明本次 bootstrap 关联的远端 key 已由 `knowledge` 后端代理调用 `warehouse` revoke 接口撤销，本地关联凭证也会被收口为 `revoked_local`

### 导入读凭证

控制台位置：

- “知识库”页中的“读凭证管理”

需要填写：

- `ak`
- `sk`
- `root_path`

成功后列表会显示：

- `ak`
- 掩码后的 `sk`
- `root_path`
- `status`
- 最近校验时间
- 最近使用时间

支持：

- Reveal `sk`
- 删除读凭证
- 设为浏览/绑定默认选择项

### 配置写凭证

控制台位置：

- “资产仓库”页中的“写凭证 / 上传”

需要填写：

- `ak`
- `sk`
- `root_path`

成功后可用于：

- 浏览 app 目录
- 上传文件

当前保存行为：

- 保存成功后，后端会立即尝试对当前写凭证的 `root_path` 做最小 bootstrap
- 如果 `root_path` 是 app 根目录，会确保 app 根目录可用
- 如果 `root_path` 是 `uploads/` 或其下子目录，只会尝试处理该写口范围内的最小目录链，不再额外创建无关目录
- 如果这把 key 连自己的 `root_path` 都无法访问或创建，保存会直接失败

## 浏览、上传、绑定的区别

### 浏览

浏览 `warehouse` 时需要一个可用凭证。

控制台支持两种来源：

- 选中某把读凭证
- 使用当前写凭证

适用场景：

- 想查看某个绑定目录，用读凭证
- 想查看上传目录或 app 根目录，用写凭证

### 上传
约束：

- `credential_id` 当前只接受读凭证
- 如果要用写凭证浏览，必须显式选择“使用当前写凭证”


上传只走写凭证。

如果没有写凭证：

- 上传会直接失败
- 不会再回退到旧钱包绑定方式

### 绑定

绑定源使用读凭证。

当前规则：

- 路径必须位于当前 app 目录内
- 路径必须在读凭证 `root_path` 范围内
- 创建绑定时会立即做一次真实访问校验

如果当前钱包只有一把读凭证，后端可以自动补齐 `credential_id`；但在控制台和接口调用上，仍建议显式选择。

## 推荐操作顺序

1. 登录 `knowledge`
2. 配置写凭证
3. 导入一把或多把读凭证
4. 用写凭证浏览并上传文件，或用读凭证浏览现有源目录
5. 在“知识库”页创建绑定源
6. 发起导入 / 重建 / 删除任务
7. 在工作台查看绑定状态与任务结果

## 常见场景

### 场景 1：上传文件后立即导入

推荐做法：

1. 配置写凭证
2. 上传到 `/apps/knowledge.yeying.pub/uploads/...`
3. 导入一把覆盖 `uploads/` 的读凭证
4. 绑定文件或目录
5. 创建导入任务

说明：

- 当前上传和读取是两套能力，导入前仍需要读凭证或 binding
- 如果写 key 只覆盖 `uploads/`，推荐把 `root_path` 直接填成 `/apps/knowledge.yeying.pub/uploads`
- 如果希望第一次保存写凭证时就自动建立整个 app 根目录，写 key 需要覆盖 `/apps/knowledge.yeying.pub`

### 场景 2：绑定现有目录做长期同步

推荐做法：

1. 为目标目录创建一把只读 key
2. 把该 key 导入为读凭证
3. 绑定目录
4. 后续使用“按绑定源创建任务”

## 凭证状态说明

- `active`
  - 最近校验与访问正常
- `invalid`
  - 最近访问时发生了认证错误，通常是 key 失效、权限变化或远端拒绝
- `revoked_local`
  - 本地已视为撤销，当前代码会拒绝继续使用

如果绑定显示 `failed`，优先检查：

- 绑定所依赖的读凭证是否仍为 `active`
- `root_path` 是否仍覆盖当前 `source_path`
- 远端 key 是否被修改或失效

## 常见报错排查

### 导入或保存凭证时报 `401 Unauthorized`

这在当前 `warehouse` 语义下通常不是“目录权限不足”，而是下面几类问题之一：

- `ak` 或 `sk` 填错
- 这把 key 已过期或已撤销
- 这把 key 在 `warehouse` 里还没有绑定任何目录

注意：

- 在 `warehouse` 里“创建访问密钥”后，默认 `bindingPaths=[]`
- 只创建 key 不够，必须再把目录绑定到该 key

### 导入或保存凭证时报 `403 Forbidden`

这通常表示：

- `warehouse` 已经识别出这把 key
- 但当前 `root_path` 不在它已绑定目录范围内，或权限位不够

推荐检查：

- 绑定目录是否覆盖当前 `root_path`
- 写 key 是否具备 `create/update`
- 读 key 是否具备 `read`

## 删除规则

### 删除读凭证

如果该读凭证还被绑定引用：

- 删除会被拒绝

正确顺序：

1. 先解绑或改绑对应 binding
2. 再删除读凭证

### 删除写凭证

删除后：

- 上传会立即不可用
- 浏览 app 写路径时也会失去默认写入口

## 面向测试的最小准备

测试代码已经有一个辅助函数：

- `tests/helpers.py`

它会：

- 创建一把写凭证
- 创建一把读凭证
- 返回对应的 `credential_id`

这也是当前端到端测试的推荐准备方式。
