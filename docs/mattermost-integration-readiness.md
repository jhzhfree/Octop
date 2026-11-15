# Mattermost 集成准备：本地检查，不是已接入

当前只提供 `POST /api/organizations/{organization_id}/digital-workers/{worker_id}/mattermost-readiness`，请求体为 `{"expected_worker_version": 1}`。需要登录、组织 owner/manager（系统管理员沿用现有规则）及对应 Agent 访问权。每次重新检查组织和 Agent 访问，版本变化返回 409。

返回数据库中的 organization/worker/agent ID 和登录主体的 `human_actor_id`；不接受调用者指定人类身份、bot ID、运行 ID、操作模式或凭据。检查产生 `digital_worker.mattermost_readiness` 本地审计，actor 是实际登录用户 ID。审计 payload 只含固定结构的本地身份与未接入状态，不含姓名、聊天内容或凭据。请求校验失败固定返回 422，不回显字段名或值。

所有报告均为 `identity_status=unbound`、`mattermost_bot_id=null`、`connected=false`；人工触发和自动发送权限均为 false。`operation_mode=human_initiated_local_check` 只描述本次本地检查，不表示人工代 bot 发过消息。`scope=local_observation_not_atomic` 明确这不是发送时授权或状态锁。没有连接 Mattermost、创建 bot、绑定身份、发送消息、启用调度或验证真实运行环境，也不修改数字员工配置。

## 下一阶段必须实现的信任边界

- 用经服务端核验的 Mattermost 稳定 user/bot ID 绑定数字员工，禁止用户名、显示名或前端的 `is_bot` 声明充当身份认证。校验服务实例、bot 身份、禁用状态和最小权限。
- 人类用户与数字员工 bot 分离。无论人工触发还是自动执行，数字员工发言都保留原生 BOT 标记；明确标注人工发起还是自动执行，禁止冒充人类。
- bot 密钥须由部署侧独立权限域的外部 secret provider 管理（当前未实现）；不能依赖通用 Connector 的同库加密抵御整库泄漏。现有通用 channel detail 返回 config，因此不得把密钥直接放 channel config。不得将密钥发到浏览器、模型或审计。
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

## 只读身份核验内核（未接入产品路由）

新增 `infra/digital_workers/mattermost_verification.py` 是安全基础，不是可用的
“连接 Mattermost”功能。现有 readiness/intent 响应与数据库状态保持不变，仍未验证。
没有新增 API、UI、配置读取、凭据保存、调度、发送、建 Bot 或生产调用点。

内核只允许部署者固定配置的规范 HTTPS **公网 IP origin**，不接受域名、
URL 路径、userinfo、query/fragment、私网、loopback、link-local、保留/多播地址；
固定只读 `GET /api/v4/users/me`，TLS 必须验证 IP SAN，不能关闭验证。
不跟随重定向、不使用环境代理，不提供用户自选请求目标或 HTTP client。
当前刻意不支持企业私网部署：下一阶段须加入管理员精确出站目标策略与可靠
TLS 身份验证，不能以 `verify=False` 或仅校验域名替代。

响应只接受 200 JSON、未压缩且至多 16 KiB，整次操作 10 秒总时限。
要求稳定 ID 精确匹配、`is_bot` 严格为 true、`delete_at` 严格整数 0，
roles 严格为 `system_user`；普通人类 token、禁用 Bot、错误 ID 或带管理权限均拒绝。
返回固定结果码，不包含上游 profile、响应内容、异常、地址或 token。
凭据字段不参加对象 repr/序列化；这不是外部 Secret Manager 或完整凭据管理方案。

网络前后必须调用授权快照复核回调。快照携带组织、员工、实际操作者、员工版本、
意向 revision、配置 revision 和实例别名。**内核不内置数据库授权，阶段 4 仅在离线编排中接入**：
生产适配必须以组织+实例确定配置，读取当前账号启用/组织管理/Agent权限、
同组织员工、draft 意向、实际 Bot ID 和所有版本，并复核凭据/实例撤权。
不得使用恒真回调或将用户提交的 scope 作为授权证明。前后复核仍不能消除网络
请求在途撤权的竞态，所以结果明确 `observation_not_atomic`，所有发送/执行权限
始终 false。团队/频道成员资格、发送目的地授权也尚未核验。

现有 Connector 的 Fernet 主密钥与密文在同一数据库，因此不能声称可抵抗整库泄露；
通用 decrypt 也不是组织授权边界。生产接入前需实现只给后端的凭据配置/轮换/
撤销及上述数据库复核，补 SQLite 与 PostgreSQL 运行测试和真实实例验收。
本轮只增加数据库无关内核与 MockTransport 测试，没有修改 SQL 或迁移，
不声称实现了两种数据库的生产授权一致性。

## 阶段 4：部署方实例元数据与离线核验编排（schema 040）

本阶段交付的是**本地配置管理与 Mock 核验链路**，不是 Mattermost 连接交付。
原有 Octop、意向配置 API 和 readiness API 不变；未新增真实核验 HTTP 路由。
`configured` 仅说明部署元数据存在，不表示 `connected`、身份已验证或允许发送。

### 部署方如何配置 / 撤销

仅具有 Octop 部署主机 / 数据库访问权限的运维人员使用 CLI。此权限等价于
现有离线管理 CLI 的本地信任边界：`--actor-id` 是审计归属，不是登录凭证。
禁止向租户或不可信数字员工提供与 Octop 部署者同权限的 shell 或数据库凭据。
`actor-id` 只声明审计归属，不能证明实际操作人身份。
命令额外从数据库检查该 ID 当前为未禁用的平台管理员；组织 owner / manager
不能以组织角色配置目标。不要把此命令包装成面向组织用户的远程执行接口。

准备**不含 token** 的本地 JSON manifest：

```json
{
  "organization_id": "替换为已有组织ID",
  "instance_key": "operations-bot",
  "origin": "https://8.8.8.8",
  "bot_id": "bbbbbbbbbbbbbbbbbbbbbbbbbb",
  "credential_ref": "operations-bot-v1",
  "status": "configured"
}
```

上面的地址和 ID 都只是格式示例，**不是 Mattermost 服务或测试目标**。
此命令不会 DNS 查询、读取密钥或发起请求。origin 仅支持全局单播公网 IP 的
规范 HTTPS origin（需要匹配 IP SAN 的证书）；域名、内网和路径尚不支持。

```sh
# 使用既有 OCTOP_HOME；先在测试副本验证，遵守现有离线 CLI 运维窗口要求。
octop mattermost-instance --manifest ./mattermost-instance.json --actor-id 1 --expected-revision 0
```

成功只输出 revision、status 和 `connected: false`。更新时使用上次 revision；
撤销时把 manifest 的 `status` 改成 `revoked`，再提交当前 revision。
恢复 / 轮换同样递增 revision，不能删除再重建来复用旧 revision。CLI 没有删除命令。
别名按组织隔离，每个别名绑定一个明确 bot 和密钥引用；不同岗位 bot 应使用不同别名。
意向中的 bot ID 必须与该配置一致。目标、bot、引用或状态任何变更都会递增 revision。
本地管理员和可写 DB 的运维人员属于信任边界，不宣称能防恶意主机管理员修改 SQL。

`credential_ref` 是外部密钥系统中的非敏感、不透明别名，**不是 token、文件路径、
环境变量名或可自动读取任意密钥的指令**。现阶段只保存引用，不内置生产解析器。
配置表和审计中没有 token。未使用通用 connector 的同库 Fernet：其主密钥也在
同一数据库，不能抵御完整数据库泄漏。未来外部 provider 必须独立授权并绑定
组织、别名、origin、bot、credential_ref、status 和 revision 全部字段，不能简单
用数据库里的引用去读取环境变量或把既有凭据发送给新目标。

### 离线编排与撤权边界

`observe_configured_identity` 必须显式提供 `httpx.MockTransport`，不接受 `None`
或真实 transport。只有部署侧可信代码可注入 credential resolver；目前没有服务
启动接线或用户调用入口。测试假 token 只驻内存，不序列化、不写入数据库、不输出日志。

编排在解析凭据前、模拟 GET 前、响应后以及记录观察结果时回查：

- 当前用户未禁用，且数据库当前平台管理员，或组织 owner / manager 且可访问 Agent；
- Agent 启用、数字员工 active、岗位仍属于组织且 Agent 任命仍存在；
- worker version、意向 revision、配置 revision 全部相符；
- 意向 draft、配置 configured、组织和别名一致，bot ID 精确匹配。

结果与审计都不是可重用授权。SQLite 审计事务串行化本次最终读取和写入；PG
使用语句快照，不提供跨所有权限表的全局撤权锁。撤销后检测到变化会返回
`access_changed`，但网络前后检查本身无法阻止检查之后发生的瞬时撤销，也不能
收回已发出的请求；“撤销再恢复且所有版本未变”的通用权限 ABA 仍不具备 epoch
保证。当前因此**保持离线，不发布生产核验入口**。发送、执行、连接状态均未开启。

### 验证与下一道交付门

`tests/unit/digital_workers/test_mattermost_instances.py` 阻断 socket connect 和 DNS，
覆盖部署管理员、组织隔离、CAS 并发、撤销 / 恢复单调 revision、各阶段权限变化、
错误凭据绑定、脱敏、事务审计回滚、CLI、真实 v39→v40 SQLite 升级和重复迁移。
040 SQLite / PostgreSQL DDL 同时提供；本阶段 PG 运行时、真实 TLS / Mattermost、
浏览器和部署验证尚未完成，不能用 Mock 结果替代。

下一阶段生产接入仍需：部署方受控外部密钥供应与精确目标绑定；明确撤权一致性
契约（如必须零窗口撤权需跨权限变更的序列化协议，而非多查一次）；PG 集成验证；
真实固定 IP HTTPS Mattermost 的受控只读验收。之后才能考虑启用核验入口。
核验 `/users/me` 仍不能证明团队/频道成员资格，任何消息发送须另走授权、审批与审计设计。

## 生产核验硬关闭与执行器秘密隔离（阶段 5 安全修正）

`observe_bot_identity` 内核现在也要求显式、精确类型的 `httpx.MockTransport`，
不再允许省略 transport 后走真实网络。`None`、真实 transport 和 Mock 子类在
创建 HTTP client 或执行授权回调前即被拒绝。上层离线编排保持相同限制。
这是当前版本运行时门禁，不是一个可通过环境变量或管理员布尔开关启用的功能。
Mock handler 仍属于受信任测试代码，本限制不构成对任意 Python 插件的沙箱。

目前不能把真实 Bot token 放进 API 进程：默认本地 Agent backend 可以使用
host-root，已安装 harness 的本地 shell 默认继承环境且子进程未降 UID。
所以 `chmod 0600`、文件路径隐藏、SecretStr 或“isolated=true”部署声明均不能
证明 Agent 无法读取 API 的秘密。此版本不新增生产 secret resolver 或核验路由，
也不读取真实秘密、访问真实实例。配置元数据与 Mock 测试不等于已连接。

后续启用必须先交付独立权限域的 verifier/broker 和可重复运行的隔离验收：
执行器无法访问秘密卷、环境、API/broker 内存和管理接口；不能共享高权限 UID、
宿主 PID 命名空间、Docker socket 或可提升权限的宿主挂载；验证受信任 provider
独立绑定完整组织/实例/origin/bot/ref/revision，而非依赖请求声明。
此外仍须提供真实 JWT + 当前数据库授权、请求并发/速率限制、审计失败关闭、
SQLite/PG 撤权一致性说明以及受控 TLS/Mattermost 验收。移除硬门禁属于另一次
必须评审的代码改动，不能因离线测试通过而自动启用。身份观察结果也绝不授予发送权。

本次内核测试自动阻断 socket connect/connect_ex/create_connection 与 DNS，
并断言绕过编排直接调用内核也不能构建真实 HTTP client。
