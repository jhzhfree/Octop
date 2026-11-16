# 旧 broker 迁移：撤回不安全启用路径

**当前不提供安全 resume 成功路径。** c590d088 的人工迁移方案有两项发布阻断，
已撤回“放弃旧身份后即可安全继续接收”的结论：

1. 原 schema 047 在普通升级时永久隔离所有 disabled broker 的旧记录，早于 plan 确认，
   也无法区分禁用原因。后续确认不能追认这次未明确授权的永久操作。
2. 新代码认识隔离表，但旧 0e950452 接收进程不认识；它可以读取旧 key。
   提升 revision 仅拒绝缓存 mapping，不能阻止旧进程重新读取新 mapping。

## 当前行为与升级

修订后的 schema 047 **仅创建空隔离表**，不写任何 retirement，不改变 enabled。
没有 intake 因普通升级而新增永久隔离。schema 046 的既有禁用保护仍保留。

新增 schema 048 对已有隔离记录、或有历史 `mattermost.maintenance.retire_and_resume`
审计的 broker 执行保护性禁用（仅影响当前 enabled 的匹配项），同时提升 revision、记录
`schema-migration` 主体的 `mattermost.maintenance.resume_blocked` 审计。
迁移审计 ts=0，payload 明示 timestamp_unknown，不伪造管理员身份或精确发生时间。
禁用使用旧接收器已经检查的 enabled 字段；修复提交后的下一次仓库授权会拒绝。
不影响无相关记录的正常 enabled broker，也不创建新的永久隔离记录。

已经执行过旧 047 的数据库不会重新执行修订后的 047。
048 **保留既有隔离记录与所有业务数据**，不擅自撤销既有 fence，也不将它们视为获得了
用户追认。既有隐式隔离如何解除仍待单独、明确授权的修复设计，不能直接清表或恢复旧备份。
SQLite 的 047/048 各自使用显式事务，故障时回滚，成功后可重复启动迁移而不重复审计。
提供配对 PG SQL，但未做真实 PG 验证。

`retire-and-resume` CLI 和直接仓库方法现在无条件拒绝：
`MAINTENANCE_PROCESS_ISOLATION_UNPROVEN`。不会新增 fence、启用 broker 或记录成功审计。
旧 c590d088 维护方法要求 schema 047，在 048 上也会拒绝；已用原始归档代码验证。
不接受 `old_processes_stopped=true` 一类自报值作为隔离证明。

## 仍可用的诊断与停用

以下是隔离验收库示例，不是生产操作授权：

```sh
uv run octop mattermost-ingress --database /tmp/fixture/octop.db --broker fixture-broker plan
uv run octop mattermost-ingress --database /tmp/fixture/octop.db --broker fixture-broker status
uv run octop mattermost-ingress --database /tmp/fixture/octop.db --broker fixture-broker suspend \
  --revision 2 --reason '停用存在旧进程隔离风险的接收能力'
```

`plan` 仅诊断：列出旧 key、已有 fence、当前映射摘要与保留 nonce 数量；digest 不授予隔离或启用权限。
`status` 显示 enabled/revision 和最近 20 条维护/迁移审计。
`suspend` 在同一事务按 revision 禁用并审计；失败保持原状态，不增加永久隔离。
操作者权限来自本机数据库写权限，主体标记 local-maintenance，不冒充远程用户或 broker。
工具不读取默认配置/私钥、不联网、不自动迁移；SqlitePool 可能建立 WAL 或获取写锁，
不能称为文件级只读工具。恢复隔离镜像仍不能通过此工具激活。

## 解除 resume 阻断所需的最低条件

必须先建立可强制且可验证的部署隔离机制，例如由受信任监督器独占控制所有接收进程、
数据库访问凭据/存储范围及启动世代，能阻止旧二进制重新访问该数据库；或有经过验证的
存储层强制围栏，使旧读写路径同样受约束。当前共享本机 SQLite 的任意同 UID 写入者
不受应用协议强制约束，既有代码没有这种机制，因此不能实现安全 resume。
仅检查进程列表、改变 revision、要求管理员勾选、创建旧进程不遵守的锁均不足。

048 的禁用是数据库内的止血，不是上述完整进程隔离证明：不会撤回迁移提交前已经读出的
敏感数据，也不能停止之前已授权/发出的任务或外部操作。迁移失败会回滚到旧状态；
不能据此宣称旧接收进程已停止。需在受控部署维护窗口停止接收并验证迁移成功。
本轮没有连接任何真实服务或执行生产维护。真实接收器仍未挂载，真实执行/外发未开放。

## 验证方式

`tests/fixtures/mattermost_ingress_0e950452.py.txt` 是 0e950452 的原始仓库模块，
SHA-256 `f17aaa5077ff45d0c9851f9a120bf574ee3cec0d55693c8ca1df118323be162b`。
`mattermost_maintenance_c590d088.py.txt` 是 c590d088 的原始维护模块，
SHA-256 `85241db099555ff37a6027e9db990db08898a9bdaef65ea92284be13f5ed14e8`。
归档代码只在临时合成 SQLite 上加载，不运行旧服务器或外部服务。

回归覆盖普通升级不隔离（enabled/disabled）、047/048 故障回滚与重试、
新代码 410/旧代码可读的原始缺陷、持有旧连接/缓存 mapping 跨迁移后读取/受理/取消/nonce
均拒绝、独立旧进程在迁移前可读且提交后缓存/新 mapping 均拒绝、旧维护方法 schema 拒绝、当前 CLI/直接 resume 无副作用拒绝、停用审计原子性。
这证明特定旧仓库代码在禁用后的行为，不是整个部署不存在旧进程的证明。

本阶段验证记录：

- 维护、入站、签名源、数据库池、恢复镜像、proof、发布 API 组合回归：278 passed，2 条既有 ldap3 弃用警告。
- 最终维护专项：10 passed，与上述回归有重叠，包含独立旧进程和历史审计无 fence 的修复分支。
- `make lint-all typecheck-all` 通过；最终后端 `make lint typecheck` 通过，mypy 674 文件。
- 没有运行全量 make all、受限遥测、真实 PG、真实双服务或生产部署。

产品流程只读检查另发现：待审批待办详情尚无审批按钮，目前仍需使用原场景运行列表审批。
本阶段未改动该 UI。真实浏览器验收未完成：已安装 Playwright/Chromium，但启用沙箱启动
时因环境 SUID sandbox 配置失败而退出；没有关闭沙箱或更改系统设置。最低解除条件是
提供可正常运行 Chromium 沙箱的受控测试环境，然后对接隔离服务完成多角色浏览器验收。
