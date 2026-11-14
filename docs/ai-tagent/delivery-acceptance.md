# 第 4 轮：运行交付的人工验收领域底座

## 语义与范围

执行 `succeeded` 仅表明 Cron/模型执行成功，不代表业务目标完成。独立验收记录缺省为 pending；人工可决定 accepted 或 changes_requested。记录不可修改，一个 run 仅一条记录；修订交付必须创建新 run，不自动重跑、不调用模型/工具、不写入 AKB、不改变目标完成度。

本轮提供领域服务、仓储、SQLite/PostgreSQL 迁移、独立 API 路由及离线测试。主应用挂载与 UI 由整合方完成，整合契约见 delivery-acceptance-api.md；未经挂载和验收不声称用户端闭环已上线。

## 接入契约

`SceneAcceptanceService(scenes, SceneAcceptanceRepo(db), evidence_loader)`；loader 是服务端闭包，使用现有 `scene_run_evidence`，只接受服务验证过的 run，不能使用请求正文充当证据。不要把 loader 或仓储直接暴露给模型工具。

- `review(org, run)` 返回当前摘要、evidence_sha256、snapshot_sha256 和已有验收。
- `decide(org, run, decision=..., expected_evidence_sha256=..., expected_snapshot_sha256=...)` 重新服务端提取证据，再比对用户见过的两个摘要标识。
- 不接收百分比、模型自报完成标记、任意审核人或客户端答案作为验收依据。
- 接口层须使用真实登录用户、现有认证/CSRF边界，不可授权后台代理自动点击人工验收。

## 权限、并发及版本

必须同时满足当前组织 owner/manager、原有证据访问权限（原请求者/原批准者/admin），再排除请求者自己。因而普通新 manager 不因职责扩大而获得敏感交付；原批准者失去组织角色后不能验收。非请求者的有效平台 admin 仍可审核，与现有证据范围一致。

服务前后检查权限及快照；仓储在写事务中重新读取有效用户、组织成员和 run。SQLite BEGIN IMMEDIATE；PostgreSQL FOR UPDATE 锁住用户/已有成员/run，序列化角色撤销和同 run 并发决定。唯一 run 主键是最后防线。完全相同的审核人、决定与摘要重试返回原记录，不产生新审计；不同决定/审核人/证据返回 409。即使重试，当前权限仍必须有效。

snapshot_sha256 绑定 run、组织、scene_version、plan_id、运行时 plan_snapshot、Cron、请求/批准者、执行状态及更新时间，不引用后来修改的当前计划。证据哈希由 UTF-8 显示摘要计算；提交时重新提取。无法锁住外部会话历史，因此它证明所审阅摘要版本而不是整段历史永远不变。

## 隐私、审计与完成度防伪造

数据库只存决定、reviewer_id、时间、两类哈希、摘要长度及场景版本，不存答案/提示词/自由备注。审计与验收同一事务，审计失败整个决定回滚。组织普通成员最多应看到独立验收状态，详细记录仅通过证据权限交集的服务暴露。数据库哈希是版本标识，不是匿名化保证或内容真实性证明。

现有 evidence 最长 20,000 字符并仅提取最后可见模型答案，UI 必须称为“交付摘要人工验收”，不要称为“全部产物验证”。accepted 是人的判断，不证明事实正确、全部工具副作用合规、全部成功标准满足或企业目标完成。后续增强应加入独立产物清单、逐项成功标准和审计链签名，但不得用模型自评自动补齐。

组织/run 删除会级联删除验收记录；现有 audit_log 有独立清理能力。本实现不构成永久合规归档或防数据库管理员篡改账本。

## 验证

仅运行 `tests/unit/scenes/test_acceptance.py` 的离线定向测试：执行成功仍 pending、自审及无证据权限 manager 拒绝、禁用/移除/降级审核人拒绝、跨组织与状态/快照变化拒绝、同请求幂等、并发一胜、审计失败回滚、证据变化/缺失拒绝、管理员审核及 changes_requested 分离语义。

PostgreSQL 迁移提供相同约束，必须在真实 PostgreSQL 进行迁移及并发/撤权集成测试后才声称该数据库已验证；SQLite 测试不能替代 PostgreSQL 验证。五轮内可以安全交付此保守领域底座，API/UI 以及真实 PostgreSQL 的验收不可冒报。
