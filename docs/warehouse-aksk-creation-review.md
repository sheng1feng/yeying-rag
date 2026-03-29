# Warehouse `ak/sk` 创建流程评审

本文档只讨论真实 `bound_token` 路径下的 `warehouse` `ak/sk` 创建与接入流程，不讨论 `mock` 模式。

对应的正式改造计划见：

- `docs/warehouse-aksk-remediation-plan.md`

目标不是复述“现在能跑”，而是从工程、产品和安全角度批判性地评估：

- 当前 `ak/sk` 到底是怎样创建出来的
- 真实上游调用链是什么
- 这个流程里有哪些设计问题
- 哪些问题是高优先级，哪些只是体验或架构优化项

## 1. 先说结论

当前仓库里的 `ak/sk` 创建实际上有两条路：

1. 用户在 `warehouse` 外部手工创建并绑定目录，然后再导入 `knowledge`
2. `knowledge` 控制台通过 bootstrap 代理到上游 `warehouse`，替用户创建、绑定并回填 `ak/sk`

如果只看当前仓库内“真正自动创建 `ak/sk`”的链路，那么答案是：

- 当前自动创建流程已经不是浏览器直连 `warehouse`
- 而是“浏览器签名 + `knowledge` 后端代理创建 access key + 回填本地凭证”

从批判角度看，当前流程最大的几个问题是：

- bootstrap 不是原子事务，存在明显的“上游已创建 / 本地已部分落库 / 接口却报失败”的中间态
- 重复 bootstrap 会不断在上游累积新 key，没有回收和复用机制
- 创建、绑定、建目录、回填、重新校验被拆成多步，调用链偏长且重复
- 写 key 与读 key 的权限和生命周期策略是硬编码的，不够可控
- 当前系统里同时保留了“外部手工创建”和“后端代理创建”两种模型，但没有统一的 key 生命周期治理

## 2. 当前存在的两种创建模式

## 2.1 模式 A：在 `warehouse` 外手工创建

这是 README 和现有使用文档里默认鼓励的主模型。

流程是：

1. 用户去 `warehouse` 创建 access key
2. 用户在 `warehouse` 给这把 key 绑定目录
3. 用户把 `ak/sk/root_path` 手工填进 `knowledge`
4. `knowledge` 用 Basic Auth 校验路径是否可访问
5. 校验通过后，本地保存为读凭证或写凭证

这个模式里，`knowledge` 不负责真正创建 key，只负责导入和校验。

优点：

- `knowledge` 不需要负责上游 key 生命周期的全部治理
- 权限边界比较清晰

问题：

- 用户操作成本高
- 冷启动体验差
- 容易出现“只创建 key 但没绑定目录”的错误
- 很依赖用户理解 `warehouse` 本身的权限模型

## 2.2 模式 B：在 `knowledge` 控制台里 bootstrap 创建

这是当前仓库里真正自动创建 `ak/sk` 的流程。

流程是：

1. `knowledge` 前端请求后端 `/warehouse/bootstrap/challenge`
2. 后端向上游 `warehouse` 请求 challenge
3. 浏览器使用当前登录钱包签 challenge
4. 前端把签名发给 `/warehouse/bootstrap/initialize`
5. 后端向上游 `warehouse` verify，换取 Bearer token
6. 后端调用上游 access key create 创建 write key
7. 后端调用上游 access key bind 把 write key 绑定到目标路径
8. 后端使用 Bearer token 在 WebDAV 侧创建目录链
9. 后端把 write key 回填成本地写凭证
10. 如果模式是 `uploads_bundle`，再额外创建一把 read key
11. 再把 read key 绑定到同一路径
12. 再把 read key 回填成本地读凭证

这条链路是真正的“创建 `ak/sk`”实现。

## 3. 真实上游调用链

这里只看真实环境下的 `bound_token` 路径。

### 3.1 认证与 token 获取

后端先调：

- `POST /api/v1/public/auth/challenge`
- `POST /api/v1/public/auth/verify`

返回 Bearer token 后，后端再继续后续操作。

### 3.2 创建 access key

后端调：

- `POST /api/v1/public/webdav/access-keys/create`

当前实现写死了：

- `expiresValue = 0`
- `expiresUnit = "day"`

权限策略：

- write key：`["read", "create", "update"]`
- read key：`["read"]`

### 3.3 绑定目录

后端调：

- `POST /api/v1/public/webdav/access-keys/bind`

当前逻辑是先 create，再 bind。

### 3.4 创建目录链

后端随后并不是调用一个“目录初始化 API”，而是直接走 WebDAV：

- `PROPFIND`
- `MKCOL`

认证是 Bearer token，不是刚创建出来的 `ak/sk`。

### 3.5 本地回填与再校验

真正容易被忽略的是，后端在拿到新 `ak/sk` 之后，并不会直接信任它们。

它会继续走本地凭证服务：

- write key 走 `upsert_write_credential`
- read key 走 `create_read_credential`

而这两个服务又会再次用 Basic Auth 去探测路径，并在某些情况下再次建目录。

这意味着 bootstrap 的真实链路不是一次 create/bind 就结束，而是：

1. Bearer create
2. Bearer bind
3. Bearer WebDAV 建目录
4. Basic 再探测
5. Basic 可能再建目录
6. 本地落库

## 4. 当前流程的关键优点

先说优点，否则批判会失真。

### 4.1 冷启动体验比手工导入强很多

用户不需要先跳到 `warehouse` UI 手工建 key，再回到 `knowledge` 填表。

### 4.2 权限至少做了最小预设

当前只有两种 bootstrap 模式：

- `uploads_bundle`
- `app_root_write`

相比“无约束任意 key”，这已经把默认路径限制在 app 边界内。

### 4.3 后端代理绕开了浏览器直连 CORS 问题

当前主流程已经不是前端直接调上游 `warehouse` 全链路。

这减少了浏览器跨域、前端持有过长生命周期 token 的问题。

## 5. 我认为当前设计最值得批判的点

下面按严重程度来排。

## 5.1 最高优先级问题：bootstrap 不是原子流程

这是我认为当前最危险的问题。

原因很简单：

- `initialize_credentials()` 里会依次创建 write key、bind、建目录、回填本地 write credential
- 如果模式是 `uploads_bundle`，还会继续创建 read key、bind、回填本地 read credential
- 但是本地读写凭证服务内部自己会 `commit`
- 路由外层虽然有 `db.rollback()`，但回滚不了这些已经提交过的内部状态

因此会出现一种很糟糕的中间态：

1. write key 已经在上游创建并绑定成功
2. 本地 write credential 也已经保存成功
3. read key 创建或绑定失败
4. 接口整体返回失败
5. 用户以为“这次初始化失败了”
6. 但系统实际上已经处于“部分成功”状态

这会带来三类后果：

- 用户认知错乱：页面显示失败，但本地和上游已经发生真实变更
- 幂等性很差：用户再次点击 bootstrap，会继续创建更多新 key
- 运维排查困难：失败不是失败，成功也不是完整成功

这不是“小瑕疵”，而是 bootstrap 设计上的结构性问题。

## 5.2 第二个高优先级问题：上游 key 会泄漏和堆积

当前 bootstrap 是“每次都新建 key”，但看不到任何：

- 复用逻辑
- 删除旧 key
- 撤销失败中间态 key
- 按 app/path 查找已有 key 并继续使用

结果是：

- 重复初始化会一直在 `warehouse` 里生成新 key
- 覆盖本地写凭证并不等于清理上游旧 key
- 一旦中间步骤失败，会留下孤儿 key

这对真实系统是很糟糕的：

- 安全面扩大
- 审计难度增加
- 用户在 `warehouse` 后台看到越来越多历史 key

当前仓库甚至已经有 `listAccessKeys` 的浏览器 helper 思路，但主流程并没有做任何复用或清理。

## 5.3 第三个高优先级问题：流程拆得过长且重复校验

当前真实链路里，至少有三层“验证或初始化”在重复发生：

1. 上游 create key
2. 上游 bind key
3. Bearer token WebDAV 建目录
4. 本地再用 Basic Auth 探测路径
5. 本地 write credential 保存时可能再做目录 bootstrap

这会带来：

- 网络往返增多
- 失败窗口增多
- 任一中间步骤出错都可能进入不一致状态

从工程角度看，这说明当前流程没有把“上游已创建并绑定成功”当成一等事实，而是又回到了“重新验证一遍”。

重新验证不是错，但这里重复得太重了。

## 5.4 权限与生命周期策略写死，缺乏产品级控制

当前实现把这些都写死了：

- write key 永远是 `read/create/update`
- read key 永远是 `read`
- `expiresValue=0`
- `expiresUnit=day`

这至少有几个问题：

- `0 day` 的真实语义不够明确
- 即便它在上游表示“永不过期”，这也不是一个安全上合理的默认值
- 权限模型没有根据场景动态收缩
- 没有让用户或系统决定 TTL、用途、轮换策略

如果系统要走生产化，key 的默认不过期是明显偏激进的做法。

## 5.5 写 key 被设计成“弱读写混合”，边界不够干净

当前 write key 权限包含 `read`，而且系统在多个场景里允许用 write credential 作为浏览或读取回退。

这带来的问题是：

- 权责边界被模糊
- 一旦读凭证配置不完整，系统很容易默默回退到写凭证
- 最终使用者会误以为“写凭证就够了”

这对短期落地有帮助，但对长期治理不友好。

从批判角度看，这是一种“为了流程顺滑而牺牲权限清晰度”的设计。

## 5.6 `app_root_write` 模式对用户很容易产生误导

当前 `app_root_write` 只会创建一把写凭证，不会自动创建读凭证。

但在用户心智里，“初始化 app 根写凭证完成”很容易被理解成：

- app 已经接通了
- 后续应该能绑定、导入、读取

实际上不一定。

如果没有对应读凭证，后续很多读路径仍然会不完整，或者只能依赖写凭证回退。

这说明当前产品命名和真实能力之间存在错位。

## 5.7 系统里同时存在两套创建模型，但缺少统一治理

当前系统既支持：

- 用户在 `warehouse` 外手工创建
- `knowledge` 后端代理创建

这本身不是问题，问题在于没有统一的生命周期策略：

- 哪种是主路径
- 哪种只用于冷启动
- 如何迁移
- 如何回收旧 key
- 如何审计“这把 key 是谁创建的、给哪个 app 用的”

结果是：

- 手工路径和自动路径只是“都能用”
- 但没有形成一个可治理的体系

## 5.8 仍然保留浏览器直连 helper，说明集成层没有收拢干净

当前控制台主 bootstrap 已经走后端代理。

但前端仍然保留了一套 `warehouse_bridge.js`，里面包含：

- 浏览器直连 challenge / verify
- 浏览器直连 create key / bind key
- 浏览器直连 MKCOL

即使当前主流程没在用，这仍然说明：

- 集成方案没有完全收敛
- 维护成本会提高
- 未来很容易出现“前后端各自维护一套上游协议”的漂移问题

## 6. 优化空间

这里分成两类：

- 不改上游 `warehouse` API 也能做的
- 需要上游配合改 API 才能做好的

## 6.1 不改上游 API 也应该尽快做的

### 6.1.1 把 bootstrap 改成“显式阶段结果”，不要伪装成全-or-无

当前最不该继续维持的是“接口报错，但前面其实已经成功了一半”。

至少应该做到：

- 返回明确阶段状态
- 告诉调用方 write key 是否已经成功
- 告诉调用方 read key 是否失败
- 告诉调用方哪些本地凭证已经落库

即便短期做不到真正原子事务，也要让失败变成“可解释的部分成功”。

### 6.1.2 增加失败后的补偿清理

如果中间步骤失败，至少应该尽力：

- 撤销或删除刚创建的上游 key
- 或在本地明确记录“这把 key 是失败中间态，需要清理”

现在是直接把孤儿 key 留在上游，这个做法不合格。

### 6.1.3 增加幂等与复用

bootstrap 不应该每点一次就创建一批新 key。

最少可以做到：

- 先查当前 app/path 下是否已有由 `knowledge` 创建的活动 key
- 如果已有，可提示复用或覆盖
- 如果覆盖，要给出清理旧 key 的策略

### 6.1.4 把内层 `commit` 从凭证服务里拿掉

这是事务一致性问题的根源之一。

如果 `create_read_credential` 和 `upsert_write_credential` 不在内部自行提交，而由外层 bootstrap 流程统一提交，就能显著降低“部分成功不可回滚”的风险。

### 6.1.5 把“创建成功后的再次验证”压缩

当前 read/write credential 回填后又走一遍 Basic 校验，是可以理解的，但现在太重。

可以考虑：

- bootstrap 场景给凭证服务增加“上游刚创建成功”的快速路径
- 减少重复 PROPFIND / MKCOL

### 6.1.6 把 key 的来源标记清楚

建议在本地增加元数据：

- 这把 key 是手工导入还是 bootstrap 创建
- 创建模式是 `uploads_bundle` 还是 `app_root_write`
- 上游 access key id 是什么
- 是否需要后续清理或轮换

这对后续治理非常重要。

## 6.2 需要上游 `warehouse` 配合才能做好的

### 6.2.1 提供原子化的 create-and-bind API

当前 create 和 bind 是两步。

最好让上游提供类似：

- `create access key and bind path`

这样至少可以减少一类中间失败窗口。

### 6.2.2 提供 delete / revoke API，并让 `knowledge` 可补偿调用

如果上游能明确支持删除或撤销刚创建的 key，bootstrap 的失败补偿就能真正落地。

### 6.2.3 提供面向 app 的预配置 key 模型

比起让 `knowledge` 每次都自己拼：

- create
- bind
- mkdir
- 回填

更合理的上游能力可能是：

- 为某个 app 一次性生成受限凭证模板
- 或提供标准的 app bootstrap endpoint

那样产品和权限模型会更稳定。

### 6.2.4 提供更明确的 TTL 语义

当前 `expiresValue=0` 的策略很粗。

如果上游能提供：

- 明确的“不失效”
- 明确的“7 天 / 30 天 / 90 天”
- 明确的可续期策略

系统才能做成真正可治理的密钥体系。

## 7. 我建议的优先级

如果只做最值钱的事情，我会这样排：

### P0

- 修掉 bootstrap 非原子、部分成功不可解释的问题
- 去掉内层凭证服务的自行 `commit`
- 给 bootstrap 增加失败补偿和阶段化返回

### P1

- 增加上游 key 复用 / 清理 / 幂等策略
- 为本地凭证记录来源与上游 key id
- 重新定义 `app_root_write` 的产品语义，避免误导

### P2

- 让权限和 TTL 可配置，而不是硬编码
- 减少重复校验和重复 WebDAV 往返
- 收拢浏览器侧遗留 helper，避免双轨集成

## 8. 最终判断

如果只回答“现在 `ak/sk` 是怎么创建的”，答案并不复杂：

- 真实自动创建链路是 `knowledge` 后端拿用户钱包签名换上游 token，再替用户在 `warehouse` 创建、绑定并回填 `ak/sk`

但如果问“这个设计是否成熟”，我的判断是：

- 现在能用
- 但还谈不上一个稳健、可治理的生产级 key provisioning 方案

最大短板不是功能缺失，而是：

- 一致性不够
- 生命周期治理不够
- 重复创建和失败补偿做得不够

如果这些问题不收口，系统会在真实环境里逐步积累：

- 孤儿 key
- 难解释的失败
- 权限边界模糊
- 运维排障成本上升

## 9. 相关代码入口

与本文最相关的文件：

- `backend/knowledge/services/warehouse_bootstrap.py`
- `backend/knowledge/services/warehouse_access.py`
- `backend/knowledge/services/warehouse.py`
- `backend/knowledge/api/routes_warehouse.py`
- `backend/knowledge/static/js/app.js`
- `tests/test_app.py`
