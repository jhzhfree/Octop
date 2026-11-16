# Mattermost 隔离接收器：服务认证、持久化受理、查询与取消

2026-11-16，Octop 基线 `f61793cd178e2e7eba8cdefd3562173bca3227ec`。
对照 Mattermost `4790d295` 的入站草案，沿用上一阶段 Go v2 proof 字节契约。
本阶段提供可在临时 ASGI 应用装配的真实路由、领域服务和 SQLite 事务；**没有挂载
到 Octop 生产 build_app，没有生产注册/启用开关或真实 source 查询实现**。

## 已交付及复现

`api/routers/mattermost_ingress.create_router(service)` 提供隔离装配的三个路径：

- POST `/api/integrations/mattermost/v1/intakes`：octet-stream proof，最多 32768 字节。
  独立服务认证、proof 验签、当前源查询、本地事务授权后，仅写 pending。
- GET 同路径 `/{delivery_key}`：独立服务认证和当前本地权限，读取持久化身份与历史
  状态，不依赖已过期 proof，不查询远端、不返回 goal/receipt/内部 actor。
- POST `/{delivery_key}/cancellations`：JSON，最多 1024 字节；严格 operation_id 和
  expected_revision，拒绝重复/额外字段。独立源取消证据及本地授权后，CAS 和审计
  同事务写终态。取消需比原受理更高的 source version；重复终态不再次写审计。

成功响应始终含 `execution_authorized:false`、协议版本 1、原 key/fingerprint、
state/revision 和字符串 source_version。所有响应 no-store。201 为新 pending，
200 为重复身份、查询或取消；不创建目标、方案、场景运行、消息、模型调用或任务。

可复现入口是 `tests/integration/test_mattermost_ingress.py`：从临时空库运行迁移，
建立合成用户/组织/岗位/Agent/员工，用内存生成的两个不同 Ed25519 私钥分别签
broker 请求和 proof；仅把公钥存入临时注册表。测试经实际 FastAPI ASGI 路由调用
真实领域服务与仓储，未覆盖任何认证依赖或替换仓储。source reader 是明确的
合成替身；没有真实 Mattermost、网络 socket、TLS、堡垒机或外部消息。

```bash
UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 uv run pytest \
  tests/integration/test_mattermost_ingress.py \
  tests/unit/digital_workers/test_mattermost_proof.py \
  tests/unit/backup/test_recovery_image.py \
  tests/unit/scenes/test_mattermost_publication.py \
  tests/integration/test_mattermost_publication_api.py -q
UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all
```

只在测试中创建 ASGI app 并 include_router。默认 `create_router()` 或未提供 source
reader 均 503，且不触达数据库写入/远端。没有新增用户 JWT 授权路径，也没有为了
服务认证修改现有 JWT middleware 的安全白名单。

## 独立服务身份 profile（Octop 提案，Mattermost 尚未实现）

此 profile 是传输请求认证，**不同于** `mattermost-octop-intent-proof/v2`，不能把
proof 签名或用户 JWT 当服务认证。无私钥加载、环境变量凭据或自动注册功能。

| Header | 内容 |
| --- | --- |
| `X-Octop-Broker` | 服务器预注册 broker ID，ASCII 字母数字、下划线、短横线，1–128 字符 |
| `X-Octop-Broker-Time` | UTC Unix 秒十进制，无前导零；接收时间须在 [issued, issued+30) |
| `X-Octop-Broker-Nonce` | 每次 HTTP 尝试唯一的小写 64 hex；重新查询也用新 nonce |
| `X-Octop-Broker-Signature` | 固定 Ed25519 签名的 Base64 |

签名原文为以下字节，不带最后一个换行：

```text
b"octop-mattermost-broker-request/v1\x00" + UTF8(
  broker_id + "\n" + timestamp + "\n" + nonce + "\n" +
  HTTP_METHOD + "\n" + exact_path + "\n" + lowercase_sha256(exact_body)
)
```

方法/路径/正文变化即拒绝，query 参数禁止，GET 正文禁止。公钥只能由当前数据库
注册表解析；调用者不能携带自己的公钥获得权限。proof 的 key ID 和公钥另行固定在
同一受控映射中；若两者公钥实际相同则在消费 nonce 前拒绝。不同公钥仍不证明
部署已隔离，真实私钥托管/签发/轮换/吊销、TLS
及 broker/source 独立秘密权限域仍需双方实现和验收。本 profile 不等于双方已经协商
完成；Mattermost 当前签名器没有这组服务认证 Header，仍不能直接联调现有两个进程。

## 持久化与当前授权

SQLite/PG 配对迁移 045 增加公钥/定向绑定、已消费 nonce、待授权 intake 三张表。
所有注册默认 disabled，无管理路由/CLI；只有隔离测试显式种入合成配置。一个 broker
记录绑定一个源/本地目标映射。没有动态凭证明文、用户 JWT 或 goal/proof 持久化。

每次认证在事务中重验注册表完整快照、启用状态、本地资源账号、组织 owner/manager
成员及 tasks 权限（管理员沿用既有管理员语义）、员工 active/版本/组织、启用 Agent
的访问权及岗位任命，然后消费 nonce。真正受理、查询、取消还会在自己的事务中再次
核对这些事实。broker 才是审计 actor；本地 actor 是被授权映射的资源归属，不声称
broker 以真人身份登录。proof 的 user_requested 不证明真人本人点击。

相同 delivery key/fingerprint 重试不新增行或审计，但仍要求新请求 nonce、有效
proof 和当前 source/本地权限；不同 fingerprint 返回 409。已取消/过期身份不被新
proof 复活。另一已认证 broker 即使能访问同一组织，也不能查阅/取消原 broker 的
记录；存储的源 binding 也必须与当前绑定精确相同，改绑另一实例不能读旧身份。
已过期 proof 不阻止独立查询；查询展示持久历史状态，不声称当前源仍有效。
没有后台过期扫描；取消时已过源有效期写 expired，受理时过期原身份拒绝。

受理和状态审计复用 `audit_log`，同一事务提交。SQLite 使用既有 BEGIN IMMEDIATE，
PG 路径用行锁但未真实验证。nonce 事务先于业务事务：后续失败不返还 nonce，未知
提交用新的已签名查询核对原 key，不能重放原认证或推断没有写入。源观察在本地事务
之前，不具有跨系统原子性；取消/撤权仍可能在远端观察后发生，因此 pending 永不
成为执行许可。

容量目前保守封顶：每个 broker 4096 个 nonce、128 个永久 intake 身份，满额 429，
不自动清理或删除终态身份。本阶段没有安全 nonce 清理、容量运维、跨恢复 epoch
或管理工具，**不适合长期生产运行**。这些限制不是静默跳过验证的理由。

## 故障及恢复边界

401：缺少/错误服务认证；403：当前授权、映射或源状态不符；404：当前范围不可见，
不证明未提交；409：认证重放、身份/CAS/source 版本冲突；410：拒绝复活原终态；
413/415/422：有界内容类型/格式拒绝；429：容量；503：未装配、源不可用或提交结果
未知。错误脱敏且不自动重试。认证成功不允许用客户端取消声明替代源取消查询。

恢复镜像在既有用户隔离事务中额外禁用所有 broker，随后复核。保留身份/nonce
历史用于审查，不把备份中的 enabled 状态恢复成服务授权；镜像启动/激活仍关闭。
未实现可信旧库回滚检测、密钥 epoch 或安全重新注册，不宣称跨恢复防重放完成。

## 测试证据与未完成事项

覆盖缺少认证、JWT/proof 冒充、错误密钥/方法/路径/正文、过期/未来签名、nonce
重放、同身份重复受理、目标映射/成员/员工版本/账号/任务权限/岗位撤销、源错误与
过期/时钟回退、另一 broker 越权、取消需要新源版本、终态不复活、受理/取消审计
失败回滚、双 SQLite 连接竞争及重开查询、恢复镜像禁用 broker。

初轮 61 项通过。扩展回归 178 通过、2 失败，均为恢复测试将最新 schema 固定为44；
改为动态迁移基线后复测。专门验证 044 单项迁移的测试保持原固定断言，没有改为
绕过迁移检查。修正后组合 230 项通过，2 条既有 ldap3/pyasn1 弃用提示。随后加固源绑定并复测
入站/恢复 146 项通过；再增加提交丢应答、并发取消和同公钥拒绝测试，最终入站
文件 37 项通过（这些结果重叠，不累加）。当前无失败/跳过。
`lint-all/typecheck-all` 通过，mypy 670 文件、ESLint 69 条既有 warning；最后小改动
另做 Ruff 与目标 mypy 检查。

真实 source active/cancellation 服务、broker 的隔离部署与 Header 签发、生产密钥
注册/轮换、TLS、PG/跨进程压测、容量与重启运维、真实两端进程联调仍未完成。
没有前端改动，不重复前端构建；受限遥测和全量 make all 未运行。不连接生产、不
执行真实任务、不外发、不合并部署。Docker/bubblewrap 本轮未重新验证。
