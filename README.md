# knowledge

钱包知识运营与发布服务，围绕 `warehouse` 作为唯一资产中心构建来源、证据、知识项、发布、服务授权与检索审计能力。

## 当前实现范围

- 钱包 challenge/verify 登录，签发 `knowledge JWT`
- 多知识库 CRUD + 基础统计
- `warehouse` 读凭证 / 写凭证管理
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
- `docs/README.md`: 文档索引
- `docs/api-integration.md`: 外部服务 API 接入文档
- `docs/control-plane-api.md`: 控制台 / 测试常用控制面接口文档
- `docs/console-operations.md`: 控制台操作手册
- `docs/warehouse-auth-refactor.md`: `warehouse` 鉴权与绑定重构说明
- `docs/warehouse-credential-usage.md`: `warehouse` 读写凭证使用说明
- `docs/warehouse-current-status-summary.md`: `warehouse` 改造当前状态总览
- `docs/warehouse-migration-guide.md`: 旧绑定 / 旧本地数据迁移说明
- `docs/todo-warehouse-auth-refactor.md`: `warehouse` 鉴权收口 TODO
- `docs/prd-bot-knowledge.md`: bot/chat 历史 PRD 参考
- `docs/technical-design-m1-m2.md`: M1 / M2 历史技术方案参考

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
- 当前 `service search` 仍以已发布知识项 / 证据的轻量词面匹配为主，不把语义向量召回作为当前版本保证
- 不把不同向量后端的相似度分值或排序完全一致作为当前版本保证

## 关键环境变量

- `DATABASE_URL`
- `JWT_SECRET`
- `WAREHOUSE_GATEWAY_MODE`
- `WAREHOUSE_BASE_URL`
- `WAREHOUSE_WEBDAV_PREFIX`
- `WAREHOUSE_APP_ID`
- `WAREHOUSE_APPS_PREFIX`
- `WAREHOUSE_MOCK_ROOT`
- `VECTOR_STORE_MODE`
- `WEAVIATE_URL`
- `MODEL_PROVIDER_MODE`
- `MODEL_GATEWAY_BASE_URL`
- `MODEL_GATEWAY_API_KEY`
- `EMBEDDING_MODEL`
- `EMBEDDING_DIMENSIONS`
- `WORKER_TASK_CONCURRENCY`
- `WORKER_MAX_ACTIVE_TASKS_PER_USER`
- `WORKER_TASK_HEARTBEAT_INTERVAL_SECONDS`
- `WORKER_NAME`
- `WORKER_RUN_LEASE_TTL_SECONDS`

## `warehouse` 代理约定

当前代码支持两种资产网关：

1. `mock`：本地目录模拟用户当前 `Knowledge App` 资产空间，便于开发测试
2. `bound_token`：`knowledge` 代理访问上游 `warehouse`，当前主流程使用 bootstrap 初始化或手工导入的 WebDAV `ak/sk` 凭证

当前默认 app-only 配置：

- `WAREHOUSE_APP_ID=knowledge.yeying.pub`
- `WAREHOUSE_APPS_PREFIX=/apps`

当前控制台默认流程为：

1. 用户先登录 `knowledge`
2. 用户先使用 bootstrap 初始化读/写凭证，或手工导入一把写凭证和一把或多把读凭证
3. 浏览 / 预览时显式选择读凭证，或显式切到写凭证浏览
4. 绑定只使用读凭证
5. 上传只使用写凭证
6. 导入 / 重建 / 删除任务按显式读凭证或 binding 绑定的读凭证执行，手工任务不再接受写凭证显式透传

兼容说明：

- 旧 `/warehouse/auth/*` JWT / UCAN 绑定接口已经从当前仓库删除
- 当前仓库只保留基于读凭证 / 写凭证的 `warehouse` 访问主路径

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

- 文档索引：`docs/README.md`
- 外部服务接入：`docs/api-integration.md`
- 控制面接口：`docs/control-plane-api.md`
- 控制台操作手册：`docs/console-operations.md`
- `warehouse` 鉴权设计：`docs/warehouse-auth-refactor.md`
- `warehouse` 凭证使用：`docs/warehouse-credential-usage.md`
- `warehouse` 当前状态总览：`docs/warehouse-current-status-summary.md`
- `warehouse` 迁移说明：`docs/warehouse-migration-guide.md`
- `warehouse` 收口 TODO：`docs/todo-warehouse-auth-refactor.md`
- 历史产品 PRD：`docs/prd-bot-knowledge.md`
- 历史技术方案：`docs/technical-design-m1-m2.md`
