# R5：组织运行进展汇总（领域基础）

本阶段只观察数据库已保存的运行及人工验收状态。HTTP 路由已在主应用挂载，组织页面已接入汇总组件；这只是代码整合状态，不等于真实用户 ACL、浏览器或 PostgreSQL 已验收。它不是业务目标完成率，也不证明外部模型、AKB 或生产服务已联通。

## 数据口径

- `OrganizationProgressService.inspect(organization_id, start=..., end=...)`：时间为 Unix 秒，区间 `[start, end)`，按运行创建时间筛选，最多 90 天。
- `scope=persisted_runs_by_creation_time`，`unit=run`，返回 `observed_at`。这个时间是查询开始附近的观察时间，不是每个状态的最后同步时间。
- 既有运行状态仅有 awaiting_approval、rejected、queued、succeeded、failed。queued 不能翻译为已证实正在执行。汇总不触发 Cron 状态同步，因此状态可能滞后。
- 执行和人工验收分开统计。成功但无验收记录为 `unreviewed_success`，不保证已经存在可读取的证据；人工 accepted 也不自动完成目标。
- 同一计划的多次运行分别计数。不得将这些数量除以计划数量生成完成率。
- `denominator` 是窗口内运行总数；未知/不一致状态计入 `inconsistent_records`，不隐藏为正常状态。`counts.execution` 和 `counts.delivery` 是不同维度，不应相加。
- 只有当前组织的验收记录可与该组织运行连接。不会返回提示词、回答、备注、哈希、审阅者或原始来源内容。

## 范围与性能

新仓储使用参数化 SQL，通过既有 `(organization_id, created_at)` 索引选定窗口；CTE 最多取 10001 条运行后聚合，与唯一 run_id 的验收记录连接，没有逐条查询或证据读取。

超过 10000 条时返回 `limit_exceeded=true`、`counts=null`、`denominator=null`。界面应提示缩小时间窗，不得将部分结果当作完整汇总或将 null 显示为 0。少于或等于上限的空集合则合法返回零计数。

查询结果与内存有界不等于所有数据库执行成本恒定；真实 PostgreSQL 查询计划仍需单独验证。

## 权限与一致性

仓储读取当前数据库用户 enabled/disabled 状态与实际平台角色，要求当前组织 owner/manager 或平台管理员。不信任前端提供的管理员标志，也不为普通成员自动赋予领导看板权限。未授权组织和不存在组织对非管理员均返回同一权限错误。

查询前后重新检查权限，捕获明显撤权；这不是跨资源串行化权限快照，不保证消除所有并发窗口。汇总本身始终 `execution_authorized=false`、`external_systems_verified=false`。

本服务不调用已有 `list_runs`，因为该方法会逐项同步 Cron 并写状态。此处不写数据库、不调用模型、不写 AKB。

## 验收

离线 SQLite 定向测试：`uv run pytest tests/unit/scenes/test_progress.py -q`。

覆盖执行/交付分离、重复计划按运行计数、跨组织拒绝、普通成员及禁用用户拒绝、查询期间撤权、时间边界、空集合、超限拒绝部分统计、错误组织验收不污染结果、不一致记录可见。测试使用最小兼容表，不是 PostgreSQL 实测或生产端到端验收。

依赖 R4 `scene_run_acceptances` 迁移 034；必须在 R4 之后发布。未实现对历史删除记录的永久统计，父资源删除可能减少历史结果。

## HTTP / UI 接入状态

`api/routers/organization_progress.py` 已在 `api/app.py` 通过
`_RouterMount(organization_progress.router, "/api", ["organizations"])` 注册。
`dashboard/src/pages/Organizations/index.tsx` 在当前组织 ID 与已加载组织一致时挂载
`OrganizationProgress`，传入真实 `organizationId`、组织 owner/manager 派生的
`canManage`，并以组织 ID 为 key 隔离切换状态。组件入口筛选不能替代后端授权。

`GET /api/organizations/{organization_id}/progress?start=<epoch>&end=<epoch>`
返回 `as_of`（读取开始时刻，不是状态同步时间）、半开创建时间窗口、
`persisted_state_may_lag=true` 和 `external_systems_verified=false`。
HTTP 403 表示无管理权限，422 表示窗口无效，503 表示数据库未准备好。
响应 `Cache-Control: no-store`；响应模型白名单丢弃额外字段。
前端明确区分未请求、读取中、失败、真实空数据与超限。错误不展示后端原文或假零。
组织变化、权限撤销、卸载后旧请求结果不再展示；时间窗口变化清空旧统计。
组件只显式读取，不轮询、不触发 reconcile。窗口选择 1/7/30/90 天。
中文及英文文案在独立组件内，不改公共 locale。状态代码与后端保持一一对应。

定向验证：`uv run --frozen pytest tests/unit/scenes/test_progress.py -q`；
`cd dashboard && npx vitest run src/pages/Organizations/OrganizationProgress.test.tsx`；
`npx tsc -b`。这些是离线适配器/组件验证，不代表实际部署或外部联通。

时间戳限制在 0..253402300799，避免 SQL 整数绑定溢出；HTTP 越界返回 422。
聚合输入最多 10001 条，超过阈值不发布部分数值。查询不要求排序，因为超过阈值
后的具体样本不会展示。此限制不是数据库物理扫描条数或响应时延的严格保证，
仍需 PostgreSQL 数据分布、索引执行计划和负载验证。业务 403/422/503 错误也带
`no-store`；框架参数校验的 422 为默认框架响应且不含统计数据。

## 2026-11-16：权限变化后不恢复旧统计

本阶段基线 6270de216c3b742d717443893aa496af38d175f9。复核发现原组件仅以
organizationId/canManage 标记缓存，canManage 从 true→false→true 后可能重新显示
撤权前统计。现在复用 SensitiveAccessBoundary，访问状态未就绪时卸载统计子组件；
组织、管理权限、用户 ID、角色或页面权限改变均创建新会话。恢复访问后需主动读取，
不恢复旧计数，迟到响应也不能写回；没有增加自动查询或执行权限。

这依赖既有 AccessSession 检测结果及当前 props，不保证远端撤权立即清屏。
权限轮询、浏览器冻结、离线检测的时效边界保持原样，不能声称固定实时上限。

新增回归覆盖组织/权限/身份/访问状态往返、迟到响应和刷新失败清除旧统计。
独立 SQLite 写连接验证查询期间撤权拒绝交付，以及验收并发更新后下一次查询显示
新统计；单次查询可以合理保留此前的完整快照。合成阶段状态验证待审批、排队、失败、
成功与要求修改的区分，不代表这些合成记录来自真实堡垒机。

本阶段定向验证：前端 OrganizationProgress、AccessSession.sensitive、RunQueue.delivery
共 27 通过；后端 test_progress 和 fresh-install inspection delivery 共 41 通过。
使用临时合成数据和 HTTP 替身，不是实际机器巡检。未运行受限遥测/完整 make all，
也未运行真实浏览器、PostgreSQL 或生产验收；浏览器沙箱与 bubblewrap 既有限制未绕过。
make lint-all typecheck-all 与 NODE_ENV=production NODE_OPTIONS=--max-old-space-size=2048
npm run build 均通过；保留已有 lint、构建体积与 Browserslist 提示，无新增依赖。
