# Mattermost 入站协议：兼容解码与尚未开放的接收边界

2026-11-16。Octop 基线 `c79978e9c71ec3cab997f9819b2c90c9da25063f`；
对照 Mattermost `4790d295` 的
[PROTOCOL.md](https://github.com/jhzhfree/mattermost/blob/4790d295/server/cmd/aitagent-plugin/PROTOCOL.md)、
[proof.go](https://github.com/jhzhfree/mattermost/blob/4790d295/server/cmd/aitagent-plugin/internal/handoff/proof.go)、
`inbox.go`、`state.go` 和公共测试向量。该文件本身把协议标为草案/BLOCKED，
不是双方已部署接口。此次没有修改 Mattermost 仓库。

## 已核对的现状

Octop 当前 Mattermost intent/readiness 与 publication 路由仍依赖 `current_user`，
用于用户配置、已验收报告的出站预览及审批，不接受插件 `{payload,signature}`。
发布批准仍是 `blocked_not_connected`，生产身份探测仍有 MockTransport 硬门禁。
这些入口不能改作入站 intake，也不能把用户 JWT 改称 broker 服务身份。

缺少可信服务认证、独立凭据边界及当前 source 查询实现时，开放一个只接收 proof
的 POST 仍不满足草案的授权条件。**本轮交付的是接收前置解码/验签，不是 HTTP
受理成功路径**。没有注册生产路由、创建 pending 行、迁移数据库或新增可启动任务。
查询与取消尚未实现。没有用测试专用模拟服务器伪装成已交付的双服务接口。

## 已实现的最小兼容部分

`infra/digital_workers/mattermost_proof.py`：

- 严格解析 Go 有序 JSON、信封/payload 的规范 Base64、HTML 转义与 U+2028/U+2029；
  拒绝额外/重复字段、不同顺序、空白、整数替代编码和大于 32768 字节输入。
- 用接收方独立提供的固定公钥验证
  `mattermost-octop-intent-proof/v2\0` 加原 payload 字节。固定 v2，不进行算法协商、
  动态密钥发现、JWT fallback 或网络访问。此 proof 与巡检回执签名属于不同协议域。
- receiver-owned Policy 明确绑定 instance、audience、源组织/员工、MM user/team/
  channel/bot 与本地 actor/organization/employee；源 ID 保留原 UTF-8 字节，不做
  trim/Unicode 归一化，不凭显示名匹配。映射 actor 是资源归属，不是 broker 登录。
- 精确校验 source 毫秒时间、proof 秒时间、最大 120 秒窗口、int64 范围和
  user_requested；声明该模式不证明真人点击。goals 仍是不可信文本，不是指令。
- 计算与 Go 一致的 delivery key、fingerprint、source commitment。Candidate 不携带
  执行许可，不保存密钥，不消费幂等 key。重新签名/换 key ID 不生成新业务身份；
  改变目标映射或 intent 时 fingerprint 改变，应由未来存储拒绝覆盖。

公开向量保存在 `tests/fixtures/mattermost_handoff_vectors.json`，来自上述提交的
`internal/handoff/testdata/vectors.json`，Git blob
`947a8b2b978449b228366f0f2b31d1eefb765bce`。包含公开合成 proof 和公钥，不含私钥。
测试将 10 个向量交给 Octop 实现，而不是运行对方参考检查器后宣称本实现兼容。
本轮没有执行下载的 Go/Python 源码，未调用 OpenSSL 外部进程。

## 必须先落地的服务身份边界

1. 单独部署/受控认证层验证 broker 服务身份和 TLS，凭据具备签发、轮换、撤销及
   隔离；具体 profile 尚未定稿，不默认共享静态密钥、环境变量或真人 JWT 可用。
2. 接收端当前注册表把该服务主体绑定允许的 instance/audience、源和本地组织/
   员工/账号及 Bot/team/channel。客户端 actor、proof 签名或 SourceRecord 字段
   都不能替代认证。审计需分开记录已认证 broker、源账号声明和本地资源归属。
3. 逐次受理与重复请求均重验当前映射/账号启用/组织成员/岗位/Agent/员工版本及
   服务权限；需要在提交事务中锁定适用授权事实。查询和取消也独立认证、授权。
4. 当前源 active/cancellation reader 必须来自已认证 source 主库确认，不能由请求
   回显或缓存旧签名充当。远端观察与本地提交不原子，受理始终不授予执行许可。
5. Go v2 未包含本地 worker/config revision、mapping epoch 或 restore epoch；不能
   偷加字段破坏签名字节契约，需双方另定映射/恢复规则后才可上线。

## 受理、查询、取消和幂等的目标边界（未实现路由）

| 草案操作 | 必须满足的行为 |
| --- | --- |
| POST `/api/integrations/mattermost/v1/intakes` | octet-stream，按流限制 32768 字节；服务认证先于业务处理；验证 proof、当前 source、当前本地授权；同事务写原 key/fingerprint、pending、source version 与审计；201 新记录、200 相同已保留身份，不启动任务 |
| GET 同路径 `/{delivery_key}` | 不接收 actor/org 声明；服务身份及原记录决定范围；独立于已过期 proof 查询；no-store，仅返回身份/状态元数据，不返回 goal/proof/密钥；404 不能证明未提交 |
| POST `/{delivery_key}/cancellations` | 严格 operation_id 小写 64 hex、expected_revision 1–8；独立取得当前源取消证据并重验本地权限；CAS 与审计同事务，保留 cancelled/expired 终态；不能冒充已经停止外部任务 |

公共响应 `protocol_version=1`、原 key/fingerprint/state/revision/source_version、
`execution_authorized=false`；source_version 用十进制字符串传递，验证
1..9223372036854775807。终态压缩摘要要显式标示，不能伪称仍有已清除的事件明细。

同 key 不同 fingerprint 返回冲突；取消/过期不得重签后复活，不通过删除记录腾出
重放空间。源版本回退拒绝，原身份长期保留需容量策略。提交/取消丢应答属于未知，
只查询原 key 对账，不自动再发 intake 或换号。取消先到、尚未观察到收件行时也不
把 404 当未投递证据。源取消与本地 pending 的跨系统竞态必须保留为明确限制。

401/403 必须停止或拒绝，不能降级真人身份；409 先区分身份冲突与版本冲突；410
保留终态；429/503/超时不能推断没写入；422 脱敏且不无限重试。以上是目标约束，
不能因 Octop 现有发布 API 使用相同状态码就宣称已满足。

## 验证与限制

```bash
UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 uv run pytest \
  tests/unit/digital_workers/test_mattermost_proof.py \
  tests/unit/digital_workers/test_mattermost_verification.py \
  tests/unit/digital_workers/test_mattermost_intent.py \
  tests/unit/scenes/test_mattermost_publication.py \
  tests/unit/scenes/test_mattermost_publication_http.py \
  tests/integration/test_mattermost_publication_api.py -q
UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all
```

首轮新测试 37 通过、1 失败：无映射员工在配置模型构造阶段被拒绝，测试原先仅捕获
验签层异常；调整为覆盖这两个拒绝阶段后复测。首次 lint 发现测试 import 顺序及
格式化风格，已修正。最终定向回归 159 项通过，2 条既有 ldap3/pyasn1 弃用提示，
无失败/跳过；lint-all/typecheck-all 通过，mypy 667 文件，ESLint 69 条既有 warning。

解码器测试禁止 DNS、连接和子进程；签名扩展测试仅用内存合成密钥。现有发布回归
继续验证原功能。没有真实 broker/TLS、source reader、生产数据库、重启后 intake
幂等、双服务 HTTP 或真实消息联调。没有前端改动，不重复前端构建。未运行受限遥测
或全量 make all，未合并/部署；Docker/bubblewrap 本轮未重新探测，不据历史版本
宣称部署可用。真实执行与外发继续关闭，仍不满足 1.0。

后续阶段：[隔离持久化接收器](mattermost-isolated-ingress.md)。提供真实 ASGI 路由和
事务的合成测试装配，仍未开放生产路径或完成双服务联调；本文结果保留为历史记录。
