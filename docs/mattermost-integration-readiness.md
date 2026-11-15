# Mattermost 集成准备：本地检查，不是已接入

当前只提供 `POST /api/organizations/{organization_id}/digital-workers/{worker_id}/mattermost-readiness`，请求体为 `{"expected_worker_version": 1}`。需要登录、组织 owner/manager（系统管理员沿用现有规则）及对应 Agent 访问权。每次重新检查组织和 Agent 访问，版本变化返回 409。

返回数据库中的 organization/worker/agent ID 和登录主体的 `human_actor_id`；不接受调用者指定人类身份、bot ID、运行 ID、操作模式或凭据。检查产生 `digital_worker.mattermost_readiness` 本地审计，actor 是实际登录用户 ID。审计 payload 只含固定结构的本地身份与未接入状态，不含姓名、聊天内容或凭据。请求校验失败固定返回 422，不回显字段名或值。

所有报告均为 `identity_status=unbound`、`mattermost_bot_id=null`、`connected=false`；人工触发和自动发送权限均为 false。`operation_mode=human_initiated_local_check` 只描述本次本地检查，不表示人工代 bot 发过消息。`scope=local_observation_not_atomic` 明确这不是发送时授权或状态锁。没有连接 Mattermost、创建 bot、绑定身份、发送消息、启用调度或验证真实运行环境，也不修改数字员工配置。

## 下一阶段必须实现的信任边界

- 用经服务端核验的 Mattermost 稳定 user/bot ID 绑定数字员工，禁止用户名、显示名或前端的 `is_bot` 声明充当身份认证。校验服务实例、bot 身份、禁用状态和最小权限。
- 人类用户与数字员工 bot 分离。无论人工触发还是自动执行，数字员工发言都保留原生 BOT 标记；明确标注人工发起还是自动执行，禁止冒充人类。
- bot 密钥仅放后端加密 Connector 凭据存储。现有通用 channel detail 返回 config，因此不得把密钥直接放 channel config。不得将密钥发到浏览器、模型或审计。
- 自动执行记录可信 run ID；人工操作关联登录 actor；审批、channel allowlist、授权版本、发送结果独立记录。外发前再验证授权，不能拿本地检查结果当凭证。
- 外部消息内容不可授予权限。防 bot 循环、重复发送、重试和超时未知结果需独立设计及验证。

## 本轮验证边界

真实临时 SQLite 迁移与审计、ASGI 路由、组织隔离、版本冲突、组织降级/移除和 Agent 共享撤销、拒绝伪造身份、拒绝额外字段与错误响应脱敏。测试拦截 socket connect 和 DNS，不接触真实 Mattermost。路由测试替换登录依赖，不能代替真实 JWT 生命周期、部署、PostgreSQL、浏览器或 Mattermost 端到端验收。

## 持久化对接意向（本轮新增）

`GET/PUT /api/organizations/{organization_id}/digital-workers/{worker_id}/mattermost-intent`
保存可恢复的配置草稿，不是验证过的 Bot 绑定。请求仅允许：

```json
{
  "expected_worker_version": 1,
  "expected_revision": 0,
  "instance_key": "enterprise-mm",
  "team_id": "tttttttttttttttttttttttttt",
  "channel_id": "cccccccccccccccccccccccccc",
  "bot_id": "bbbbbbbbbbbbbbbbbbbbbbbbbb",
  "status": "draft"
}
```

上述 ID 仅为格式示例。客户端提交的三个远端 ID 均为**待验证声明**，不能用来证明 Bot 身份、团队/频道关系或权限。`instance_key` 是本地操作员管理的实例别名，不是 URL、凭据引用或已配置的连接。此轮不保存服务地址、不进行 DNS/HTTP、不收取 token，不将配置写入会回显的通用 channel config。响应恒为 `identity_status=unverified`、`connected=false`、`execution_authorized=false`，两类消息发送权限均为 false。原 readiness 接口仍表示尚无已验证绑定，与意向草稿并不矛盾。

首次创建要求 `expected_revision=0`，更新使用上一响应的 revision；并发或陈旧版本返回 409。`status=revoked` 保留记录和递增 revision，恢复为 draft 仍须提供当前 revision，不能用旧版本覆盖撤销。不存在硬删除接口；删除所属数字员工将级联删除意向，审计记录保留。读取需提供 `expected_worker_version` 查询参数，且重新检查组织管理与 Agent 权限。服务端保存从登录身份解析的 `updated_by`，客户端不能声明人类操作主体、自动操作模式或已验证状态。写入 SQL 再检查当前账号未禁用、组织角色/Agent访问权和数字员工版本，配置与审计同事务提交；错误不回显请求值。

迁移 039 同时提供 SQLite/PostgreSQL SQL。此轮通过临时 SQLite 和真实 ASGI 路由的隔离测试，不代表真实 Mattermost 联调或 PostgreSQL 运行验证。尚无 Dashboard 配置页、服务器凭证接入、消息入口/出口或自动执行路径。

### 企业场景衔接

第一场景为企业运营团队的数字分析员：管理员先在 Octop 选定数字员工、保存拟用的 Mattermost 实例/团队/频道/Bot ID，审阅草稿后再由后续独立流程配置服务端凭据。下一道验收门是只读确认当前 token 对应真实 Bot、Bot 未禁用、团队/频道归属与成员资格，禁止将普通用户 token 作为 Bot。真正发送还需要明确目的地、人工授权或已批准任务执行身份、幂等记录与审计。草稿或本地 readiness 不能替代这些验收门。

一致性边界：SQL 在配置写入语句快照中重新检查权限，不声明 PostgreSQL 下成员/Agent 权限撤销与本配置提交完全串行化。此表没有任何执行能力；未来验证或发消息必须在执行时重新授权，不能信任旧草稿或旧 readiness。当前回归覆盖双 SQLite 连接同 revision 仅一写入成功，以及审计落库失败时意向回滚。
