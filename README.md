# knowledge

钱包知识运营与发布服务，围绕 `warehouse` 作为唯一资产中心构建来源、证据、知识项、发布、服务授权与检索审计能力。

## 当前实现范围

- 钱包 challenge/verify 登录，签发 `knowledge JWT`
- 多知识库 CRUD + 基础统计
- `knowledge` 代理浏览当前 `Warehouse App` 目录
- `knowledge` 上传文件到 `/apps/<warehouse_app_id>/uploads/`
- 手动导入 / 重建 / 删除的轻量异步任务
- 按知识库绑定源批量创建导入 / 重建 / 删除任务
- 绑定源状态管理（启用/停用、同步状态、最近任务、索引覆盖摘要）
- Source / Asset / Evidence / Candidate / Item / Release / Grant / Search Lab 主链路
- 导入治理：任务明细、重试、未变更跳过
- 运维能力：worker 心跳、数据库租约协调、运行概览、存储健康检查
- 文档解析、Evidence 构建、知识项治理、发布快照、服务授权
- `service search`、`retrieval logs`、`source governance` 与 search lab
- 长期记忆与短期记忆 CRUD（兼容模块，不再作为主产品叙事）
- 产品化前台管理台（仍在向知识运营台收口）

## 目录

- `backend/knowledge`: FastAPI 应用
- `tests`: 后端测试
- `docs/api-integration.md`: 外部服务 API 接入文档
- `docs/console-operations.md`: 控制台操作手册
- `docs/prd-bot-knowledge.md`: bot/chat 产品重构 PRD
- `docs/technical-design-m1-m2.md`: M1 / M2 技术方案

## 运行

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn knowledge.main:app --reload
```

默认打开：

- API: `http://127.0.0.1:8000`
- 控制台: `http://127.0.0.1:8000/`
- OpenAPI: `http://127.0.0.1:8000/docs`

## Worker

```bash
cd backend
source .venv/bin/activate
python -m knowledge.workers.runner
```

当前 worker 采用按任务 claim/heartbeat 的调度方式：

- 多个 worker 可共享同一数据库协同消费导入任务
- 同一用户默认最多并发执行 1 个任务，避免单用户大任务挤占全部处理能力
- `sqlite` 环境会自动退回串行处理；生产建议使用 PostgreSQL 以启用更稳的并发处理
- 默认部署建议只常驻 `1` 个 worker，其余实例按需启停
- 独立 worker 的 systemd 部署与扩缩容建议见 `docs/worker-deployment.md`

## 本地开发默认值

为了方便本地开发，默认配置并不强依赖真实的 `warehouse`、`Weaviate` 或模型网关：

- `warehouse` 默认走 `mock` 模式，本地目录模拟用户资产
- 向量检索默认走 `db` 模式，在数据库中保存向量并做 Python 侧相似度计算
- embedding 默认走 `mock` 模式，使用确定性伪向量

生产环境可切换为：

- `WAREHOUSE_GATEWAY_MODE=bound_token`
- `VECTOR_STORE_MODE=weaviate`
- `MODEL_PROVIDER_MODE=openai_compatible`

当前测试与验证口径：

- 已覆盖 `db` / `weaviate` 在过滤语义上的一致性验证
- 已覆盖 `mock` / `openai_compatible` embedding provider 的调用契约验证
- 不把不同向量后端的相似度分值或排序完全一致作为当前版本保证

## 关键环境变量

- `DATABASE_URL`
- `JWT_SECRET`
- `WAREHOUSE_GATEWAY_MODE`
- `WAREHOUSE_BASE_URL`
- `WAREHOUSE_WEBDAV_PREFIX`
- `WAREHOUSE_AUTH_MODE`
- `WAREHOUSE_APP_ID`
- `WAREHOUSE_APPS_PREFIX`
- `WAREHOUSE_SERVICE_BEARER`
- `WAREHOUSE_FORWARD_WALLET_HEADER`
- `WAREHOUSE_MOCK_ROOT`
- `VECTOR_STORE_MODE`
- `WEAVIATE_URL`
- `MODEL_PROVIDER_MODE`
- `MODEL_GATEWAY_BASE_URL`
- `MODEL_GATEWAY_API_KEY`
- `EMBEDDING_MODEL`
- `EMBEDDING_DIMENSIONS`
- `RERANK_ENABLED`
- `RERANK_MODEL`
- `RERANK_API_BASE`
- `RERANK_API_KEY`
- `WORKER_TASK_CONCURRENCY`
- `WORKER_MAX_ACTIVE_TASKS_PER_USER`
- `WORKER_TASK_HEARTBEAT_INTERVAL_SECONDS`
- `WORKER_NAME`
- `WORKER_RUN_LEASE_TTL_SECONDS`

## `warehouse` 代理约定

当前代码支持两种资产网关：

1. `mock`：本地目录模拟用户当前 `Knowledge App` 资产空间，便于开发测试
2. `bound_token`：用户先在 `knowledge` 中绑定当前 `Knowledge App` 的 `warehouse` 访问凭证，后端加密保存后代理访问上游

当前默认 app-only 配置：

- `WAREHOUSE_APP_ID=knowledge.yeying.pub`
- `WAREHOUSE_APPS_PREFIX=/apps`
- `WAREHOUSE_AUTH_MODE=split`

线上 `warehouse` 当前采用 app UCAN + 钱包签名的绑定流程，`knowledge` 的默认流程为：

1. 用户先登录 `knowledge`
2. `knowledge` 前台自动尝试为当前钱包建立 `warehouse` app 目录访问
3. 前端再次使用钱包对 app UCAN bootstrap message 签名
4. `knowledge` 后端保存当前 app 的 UCAN 凭证，后续浏览 / 上传 / 导入时按路径自动使用
5. 如需兼容 JWT 绑定，后端仍保留 `warehouse /auth/verify` 能力，但产品主路径不再面向 `personal`

当前默认线上配置：

- `WAREHOUSE_BASE_URL=https://webdav.yeying.pub`
- `WAREHOUSE_WEBDAV_PREFIX=/dav`

该模式不要求修改 `warehouse` 代码。

## 服务检索主入口

当前主服务接口已切到：

- `POST /service/search`
- `POST /service/search/formal`
- `POST /service/search/evidence`
- `GET /service/grants`
- `GET /service/kbs`
- `GET /service/releases/current`

已下线的旧主叙事接口：

- `POST /kbs/{kb_id}/search`
- `POST /retrieval-context`
- `POST /retrieval/context`
- `POST /bot/retrieval-context`
- `POST /retrieval/*`

## 文档

- 外部服务接入：`docs/api-integration.md`
- 控制台操作手册：`docs/console-operations.md`
- 产品重构 PRD：`docs/prd-bot-knowledge.md`
- M1 / M2 技术方案：`docs/technical-design-m1-m2.md`
