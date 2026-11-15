# 巡检报告的 Mattermost 发布预览与独立审批

本轮交付的是**可通过 API 操作的本地发布审批闭环**，不是 Mattermost 消息发送。
第一场景：数字员工完成企业服务巡检，同一运行的报告已经由独立人员验收；
运行发起者准备发布预览，另一位授权管理者审阅内容、频道和 Bot 后独立批准。
批准结果保存在本地 outbox，状态明确为 `blocked_not_connected`。

## 前提与操作

先完成已有场景运行、原生巡检报告和独立验收，并配置数字员工的 Mattermost
意向以及部署侧实例元数据。这里只支持同一 run 的原生 inspection report；
不接受拼接其他 run 的手工报告附件。报告最大 64 KiB（UTF-8 字节）。
`instance_key`、频道、Bot 等均从当前服务器配置解析；客户端不能提交正文、
目标 URL、secret ref、token、actor 或已发送状态。

基础路径为 `/api/organizations/{organization_id}/scene-runs/{run_id}/mattermost-publication`。
最终路径以 OpenAPI 中 “Mattermost publication” 路由为准。

1. 发起者创建发布预览：`POST`，提交四个预期值：
   `expected_report_sha256`、`expected_worker_version`、`expected_intent_revision`、
   `expected_configuration_revision`。响应包含报告内容、目标频道/Bot、实际发起人、
   `publication_sha256` 和 `pending_approval`。
2. 授权人员 `GET` 阅读当前预览。此读取也实时验证身份、组织/Agent 访问和源数据。
3. 原运行验收者或当前平台管理员 `POST .../approve`，提交
   `{"expected_publication_sha256":"上一步返回的64位摘要"}`。不能自己批准自己
   发起的运行或提议。成功为 `blocked_not_connected`，不是“已发布”。
4. 发起者或授权审阅者可 `POST .../revoke` 撤销尚未发送的本地发布意向。
   已撤销记录不可恢复。当前合法用户可在配置/源报告或历史审批人权限变化后撤销；
   撤销响应的 content 为空，避免回显变更后的内容。审批/读取仍拒绝陈旧绑定。

每个 run 只有一条不可变预览。相同请求的创建/同人审批/撤销可幂等重试，
不会重复记录审批审计；目标或内容改变后不能覆盖旧记录，需要新的运行与验收。
输入陈旧返回 409、当前权限不足 403、缺少资源 404。存储/审计故障返回脱敏 503，
事务不留下半完成的成功记录。请求错误不会回显秘密字段。

## 绑定、权限与存储

发布摘要绑定组织、run、报告 hash、运行快照、独立验收者、提议人、员工及版本、
实例别名、team/channel/bot、意向 revision 和实例配置完整摘要。每次读取和审批
重新验证报告 hash、同run验收、数字员工任命、当前配置和相关账号权限，
包括原发起者、验收者和已经批准发布的人员。
审批后若权限/配置被撤销，旧审批不构成可复用授权。无 `dispatch`、`send`、
`retry` 或修改任意 outbox 状态的 API，也没有后台消费此队列的 worker。
`connected`、`dispatch_enabled`、`execution_authorized` 恒为 false。

数据库只保留绑定元数据与 hash，不再复制报告正文；正文来自原有受控报告表。
实例 URL、credential ref、token 不进入预览响应；审计仅保留组织、发布 hash、
状态、实际 actor 与动作，不保存报告内容或目标秘密。删除所属 run 会级联移除
outbox，但已经产生的审计不级联删除。此处沿用平台管理员访问策略，不宣称
能防御可直接修改数据库的恶意主机管理员。

`unknown` 是保留的拒绝重试终态，仅用于未来外部投递不确定结果的安全约束；
当前版本没有发送器，因此不会自行产生此状态。离线测试人为写入 unknown，
验证不能 approve/revoke/recreate 让它重新执行。这不等于实现了真实超时恢复。
以后启用发送前必须另外设计幂等远端回执和人工对账；超时不能直接重发。

## 一致性及交付边界

SQLite 使用真实事务，串行化写入、重复审批和同事务审计。PostgreSQL 提供同结构
041 DDL，并按现有 repo 习惯锁住已存在的身份、运行、源报告和配置行；这不是
所有权限表的全局 epoch，也不保证撤销再恢复的 ABA 可见。锁竞争/死锁回滚应由
调用者重新读取当前状态，不得按旧审批直接外发。真实 PostgreSQL 未运行验收。

本轮测试覆盖真实 SQLite 迁移和本地状态链、重复审批、并发、审计失败回滚、
改报告/频道/版本/撤权拒绝、跨组织访问、JWT API 边界以及未知状态禁止重放。
测试明确禁用网络，不连接 Mattermost，不验证真实 TLS、Bot 身份或频道成员关系。
生产核验硬关闭和同 UID 执行器秘密隔离阻塞详见
[mattermost-integration-readiness.md](mattermost-integration-readiness.md)。
