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
