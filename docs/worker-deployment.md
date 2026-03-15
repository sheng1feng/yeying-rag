# Worker 部署与扩缩容建议

本文档描述如何把 `knowledge` 的导入 worker 独立部署，并在需要时扩容到最多 4 个 worker 实例。

## 默认策略

当前建议的默认部署策略是：

- **只常驻 1 个 worker：`knowledge-worker@1`**
- `knowledge-worker@2~4` 默认不启用
- 只有在排队明显增长时，才临时启用额外实例

这样做的好处：

- 日常资源占用更低
- 测试环境和中小流量场景更稳定
- 出现问题时排查更简单
- 后续仍然保留扩容到 4 个实例的能力

## 1. 前提建议

- 生产 / 测试环境优先使用 `PostgreSQL`
- 如果仍使用 `sqlite`，只建议保留 **1 个** worker 常驻
- API 与 worker 必须共享同一个 `DATABASE_URL`
- API 与 worker 必须使用同一份 `backend/.env`

原因：

- 当前 worker 已支持任务级 claim/heartbeat，可安全多实例消费
- 但 `sqlite` 更适合作为单实例、低并发测试数据库
- 多 worker 的吞吐上限不仅取决于 worker 数量，还取决于：
  - `warehouse` WebDAV 延迟
  - embedding / model gateway 吞吐
  - Weaviate 写入能力

## 2. 推荐起步配置

建议按以下顺序扩容，而不是一开始就常驻 4 个 worker：

- 常驻：`knowledge-worker@1`
- 低负载：仅保留 `@1`
- 中等负载：启用 `@2`
- 高负载：启用 `@3`
- 峰值压测：再启用 `@4`

推荐默认参数：

- `WORKER_TASK_CONCURRENCY=2`
- `WORKER_MAX_ACTIVE_TASKS_PER_USER=1`
- `WORKER_TASK_HEARTBEAT_INTERVAL_SECONDS=15`

建议解释：

- 先用 **1 个 worker + 每 worker 2 个并发任务**
- 如果排队明显增长，再加第 2 个 worker
- 优先“增加 worker 实例数”，其次再考虑继续提高单 worker 并发
- 如果上游网关或向量库开始变慢，可把 `WORKER_TASK_CONCURRENCY` 降回 `1`

## 3. 启动脚本

仓库已提供独立 worker 启动脚本：

- `backend/scripts/run_worker.sh`

作用：

- 自动加载 `backend/.env`
- 为每个实例生成唯一 `WORKER_NAME`
- 以 `python -m knowledge.workers.runner` 启动 worker

注意：

- 即使 `.env` 中存在固定的 `WORKER_NAME=knowledge-worker-1`
- 脚本仍会按实例号覆盖为：
  - `knowledge-worker-1`
  - `knowledge-worker-2`
  - `knowledge-worker-3`
  - `knowledge-worker-4`
- 如确实需要自定义单个实例名，可显式传入 `WORKER_NAME_OVERRIDE`

示例：

```bash
cd /srv/knowledge/backend
./scripts/run_worker.sh 1
./scripts/run_worker.sh 2
```

## 4. systemd 部署

仓库已提供模板服务文件：

- `deploy/systemd/knowledge-worker@.service`

建议安装到：

```bash
sudo cp deploy/systemd/knowledge-worker@.service /etc/systemd/system/
sudo systemctl daemon-reload
```

注意：模板中的默认路径是：

- 工作目录：`/srv/knowledge/backend`
- 启动脚本：`/srv/knowledge/backend/scripts/run_worker.sh`
- 环境文件：`/srv/knowledge/backend/.env`

如果你的部署路径不同，请先修改模板中的路径。

## 5. 常驻 1 个 worker，按需扩到 4 个

### 启动常驻 worker

```bash
sudo systemctl enable --now knowledge-worker@1
```

这就是建议的默认形态，先不要额外启用 `@2~@4`。

### 扩容到 2~4 个

```bash
sudo systemctl enable --now knowledge-worker@2
sudo systemctl enable --now knowledge-worker@3
sudo systemctl enable --now knowledge-worker@4
```

### 负载下降时缩回 1 个

```bash
sudo systemctl disable --now knowledge-worker@4
sudo systemctl disable --now knowledge-worker@3
sudo systemctl disable --now knowledge-worker@2
```

这样可保留：

- `knowledge-worker@1` 常驻
- `knowledge-worker@2~4` 仅在高峰时开启

建议在缩容前先确认：

- `GET /ops/workers` 中待关闭实例的 `active_tasks_count` 为 `0`
- 或至少确认当前排队任务已明显下降

这样可以减少“处理中的任务被中断后等待 stale reclaim 再重跑”的情况。

## 6. 如何判断是否需要加 worker

优先看以下接口：

- `GET /ops/overview`
- `GET /ops/workers`
- `GET /tasks`

建议关注：

- `tasks_pending` 是否持续增长
- `tasks_running` 是否长期顶满
- `avg_task_wait_ms` 是否明显升高
- `/ops/workers` 中每个 worker 的 `active_tasks_count`

扩容判断建议：

- `tasks_pending` 持续增加，且 `avg_task_wait_ms` 明显上升：增加 worker 实例
- `tasks_running` 已较高，但 API / Weaviate / 模型网关响应明显变慢：不要继续扩 worker，先排查上游瓶颈

## 7. 4 个 worker 的建议上限

“最多 4 个 worker”是一个合理的第一阶段上限，但建议理解为：

- **上限配置**：最多准备 4 个实例模板
- **不是默认常驻数**：默认不要同时常驻 4 个

推荐策略：

- 默认常驻 `1`
- 常见高峰开到 `2`
- 压测或高峰时段开到 `3~4`

如果 4 个 worker 仍然排队严重，下一步优先排查：

- `warehouse` 响应慢
- embedding gateway 限流
- Weaviate 写入慢
- 单文件过大 / PDF 过多

而不是继续无上限加 worker。
