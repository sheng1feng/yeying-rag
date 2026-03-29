# Warehouse 鉴权收口 TODO

本文档只列当前方案必需的收口项，不列未来增强项。

如果要看 `ak/sk` 创建链路的正式改造方案，参考：

- `docs/warehouse-aksk-remediation-plan.md`

## 已完成

- `WarehouseAccessCredential` 已补足 bootstrap 来源、上游 key 映射、provisioning 元数据
- 已新增 provisioning attempt 记录与 cleanup 状态
- 已补充本地 `revoke-local` 显式管理动作
- browse / preview 的显式 `credential_id` 已限定为读凭证
- source scan / task / evidence 已移除写凭证读回退

## 仍待确认

- 是否需要为 `SourceBinding` 增加绑定时确认过的根路径快照
- 是否要给本地 revoke 增加恢复动作，而不是只保留 `revoke-local`
- 是否要彻底移除 `warehouse_bridge.js`

## API 与状态文档

- 持续维护 `docs/control-plane-api.md`，避免控制台接口散落在各文档里
- 保持 `/warehouse/status` 作为当前唯一状态查询接口
- 当前 `/warehouse/status.read_credentials_count` 已按 active 读凭证计数

## 绑定与任务

- 继续保证 binding 与 `credential_id` 的显式关系不丢失
- 当前手工任务里显式 `credential_id` 已限定为读凭证
- 当前内部读链路已不再回退到写凭证

## 前端 UI

- 保持“先导入读/写凭证，再浏览/上传/绑定”的主叙事
- 明确旧钱包绑定状态只作为兼容信息，不作为主入口
- 当前控制台已提供：
  - bootstrap cleanup 入口
  - 读凭证 / 写凭证的 `revoke-local` 入口
- 继续统一提示文案：
  - 未配置写凭证
  - 绑定缺少读凭证
  - 凭证失效
  - 路径超出凭证范围

## 迁移与清理

- 旧 UCAN/JWT 用户的迁移说明已补到 `docs/warehouse-migration-guide.md`
- 清理本地旧数据库中的历史绑定表数据时，优先采用手工删库重建或显式脚本

## 测试项

- 导入读凭证成功
- 导入读凭证失败：路径超出 app 目录
- 导入读凭证失败：认证错误
- 导入写凭证成功
- 写凭证覆盖更新成功
- 删除被绑定引用的读凭证失败
- 创建绑定时路径超出读凭证范围失败
- 创建绑定时目标路径不存在失败
- 上传在无写凭证时失败
- 绑定任务在缺少 `credential_id` 时失败
- 凭证认证错误时状态转为 `invalid`
- 工作台能反映 `credential_status`
- 旧 `/warehouse/auth/*` 路由返回 `404`
