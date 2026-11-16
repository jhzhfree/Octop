# Mattermost 当前源状态：受控注入的签名 HTTP 适配器

2026-11-16，Octop 基线 `247b4567f2feb20756f79cc9a461b2daa118c116`。
上一阶段接收器的 `SourceReader` 只规定返回对象，未提供 HTTP 解析、响应认证或
逐次请求关联。本阶段增加 `OfflineMattermostSourceReader`，直接满足该回调接口，
接入既有受理/取消服务和事务；不增加另一套 intake 或放开执行。

## 硬边界与当前可用范围

- 只接受精确类型 `httpx.MockTransport`，拒绝真实 AsyncHTTPTransport、None、
  MockTransport 子类；调用时再次检查。请求地址固定为
  `https://mattermost-source.invalid/api/integrations/octop/v1/source-observations`。
  没有 URL 配置、生产 provider、注册 API 或绕过门禁的布尔开关。
- `resolve(mapping)` 与 `sign(target, message)` 必须由可信装配方显式注入，无默认值。
  resolver 必须读取当前源公钥与启用授权，校验 broker/mapping revision/精确源绑定；
  signer 只接收已构造的只读查询签名原文。适配器不读取文件、环境变量或真实凭据。
- SourceTarget 只持有公钥，不持有私钥；源响应公钥不得复用 broker/proof 公钥。
  发出的请求签名还会先用当前 broker 公钥本地核验，错误 signer 在 dispatch 前拒绝。
- HTTP client 禁止环境代理和重定向，保留 TLS 校验设置，拒绝压缩/非 JSON/非200，
  响应流最多 16384 字节，无重试。整个回调有 2 秒 asyncio 超时，HTTP timeout 1秒；
  这是合作式取消，不是对不合作回调、阻塞进程或实际 TLS 的隔离/超时验收。

传输硬门禁不是进程沙箱：同进程注入的任意 Python callback 仍属于可信代码。
测试禁止 socket/DNS，实际 source handler 是合成替身；不能据此宣称网络源、密钥
隔离或真实 Mattermost 两进程联调已经可用。生产接收器仍未挂载。

## 源查询协议提案（Mattermost 当前未实现）

本协议是 Octop 的 source-observation HTTP 提案，**不是**对方已有 Go v2 proof
端点。原 proof 字节/身份语义保持不变；本提案的 JSON 不要求 Go 有序重编码，
签名验证都使用收到的原始字节，禁止通过重新序列化改变签名原文。

请求方法固定 POST、Content-Type application/json，正文：

```json
{
  "protocol_version": 1,
  "broker_id": "fixture-broker",
  "nonce": "<逐次随机64位小写hex>",
  "issued_at": 101,
  "binding": {"instance":"…","audience":"…","organization":"…","employee":"…","user":"…","team":"…","channel":"…","bot":"…"},
  "receipt": "<原64位hex回执ID>"
}
```

不发送 goal、原 proof、用户 JWT、凭据或本地 actor 声明。Header：
`X-Octop-Source-Broker` 绑定 broker ID，`X-Octop-Source-Signature` 是 broker 对
`b"octop-mattermost-source-query/v1\x00" + exact_request_body` 的 Ed25519 签名 Base64。
它不能替代源端的当前服务授权、请求时限和防重放检查。目标固定只读查询端点；
源端必须拒绝将同一签名用于其他方法/用途。

源端必须在已认证且授权的主库读取中确认原 receipt 的当前 actor/Bot、组织/员工、
team/channel、允许的 broker/audience、取消状态、原收件身份预留及版本。不能仅
把请求字段、旧 proof 或缓存对象回显后重新签名。查询应返回取消后的原身份，供
接收端对账；404/错误/缺失不能被解释为没有提交或取消已完成。

响应为 JSON envelope，仅三个字段：`key_id`、`payload`、`signature`。后二者为
严格 Base64；key_id 必须等于 resolver 已固定的源 key ID，不能从响应发现公钥。
签名原文：`b"mattermost-octop-source-observation/v1\x00" + decoded_payload_bytes`。
payload 严格拒绝重复/额外键，格式：

```json
{
  "protocol_version": 1,
  "request_sha256": "<本次原始请求正文SHA256>",
  "observed_at": 101,
  "expires_at": 106,
  "observation": {
    "delivery_key": "<原收件key>",
    "fingerprint": "<原收件fingerprint>",
    "commitment": "<原intent commitment>",
    "state": "awaiting_octop_authorization",
    "receipt_expires_at": 160999,
    "version": "1"
  }
}
```

state 仅允许 `awaiting_octop_authorization` / `cancelled`；receipt_expires_at 是原
毫秒时间，其他时间为秒。version 为无前导零的十进制字符串，范围1..9223372036854775807，
禁止 JSON number/布尔值，避免 JavaScript 精度误差。内部转换为已有 SourceObservation。

请求每次新 nonce，不使用响应缓存；request_sha256 绑定它及原绑定/receipt。
观测时间不得早于本次查询开始、晚于本地收到时间；响应有效期最多5秒，请求过程
也不得达到5秒。发送前和响应后重新解析当前目标/公钥，变化、撤销或时钟回退拒绝。
签名正确的旧 envelope 不能用于新挑战。

## 与现有接收事务的关系

适配器验证响应来源与本次查询关联后，已有 `_match` 仍核对 key/fingerprint/
commitment/原 expiry/所需状态；已有 repo 再次在事务中检查本地当前身份/组织/
员工/岗位/Agent、映射快照、版本及永久幂等身份。签名本身不能授予 pending，更
不能授予任务执行。cancel 仍需要源版本推进；GET 独立对账不调用源查询。

源端主库确认与本地提交仍不是分布式事务。前后 resolver 查询也不是跨进程原子
撤权锁，尤其源密钥状态尚未接入可锁定的生产信任存储。签名与挑战不能证明源端
真的读取了最新主库，不能检测源数据库/密钥整体回滚；restore epoch 仍未定稿。
因此不放开生产入口、真实执行或外发。错误只返回脱敏不可用，失败不自动重试。

## 可复现验证

`tests/integration/test_mattermost_source_adapter.py` 用实际 HTTP request/response
序列化及三组独立内存密钥，mock source 先核验 broker 查询签名，再签合成响应。
响应经新适配器进入真实 ASGI intake、SQLite 事务和取消链路；本地真实主库授权
变化通过现有 repo 拒绝。没有替换 intake 仓储或 broker 验签，source 事实明确为模拟。

```bash
UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 uv run pytest \
  tests/integration/test_mattermost_source_adapter.py \
  tests/integration/test_mattermost_ingress.py \
  tests/unit/digital_workers/test_mattermost_proof.py \
  tests/unit/digital_workers/test_mattermost_verification.py -q
UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all
```

首轮组合70项通过；扩展后定向组合159项通过，随后补充真实 HTTP transport 拒绝
用例，适配器文件36项复测通过（重叠不累加）。无失败/跳过；首次 Ruff 的 with
语法/import 风格提示已修正。静态门禁通过，mypy 671 文件、ESLint 69 条既有 warning。
覆盖正常受理/重复/取消/独立查询，错误签名/域/key ID、请求摘要错配、缓存重放、
目标/key 变化、撤权、时钟回退/过期、数值格式、重定向、401/403/404/429/503、
超时、超限、错误 content-type/encoding、额外/重复字段、签名器失败及真实传输拒绝。

没有前端或 schema 变化，不重复构建/迁移测试；没有真实源端、TLS、PG 或两进程
故障演练。未运行全量 make all、受限遥测、生产部署或真实外发。Docker/bubblewrap
本轮未探测，不据历史能力宣称生产隔离通过。

## 真实联调前的具体前提

1. Mattermost 实现并评审源查询路径、服务签名验证、查询权限、主库当前状态/原身份
   预留读取、取消查询语义及有界响应签名；当前4790d295只有内部 reader 契约。
2. 双方定稿本提案与上一阶段 broker 入站 Header profile，不把不同签名域混用，
   提供跨语言请求/响应向量和真实源授权负例。
3. 部署独立受控 signer、公钥注册/撤销与当前 resolver；不得从客户端或模型接受
   key/URL/actor。不同公钥不等于秘密权限域隔离，需独立部署验收和轮换/撤销测试。
4. 明确 TLS/允许目标/网络和源查询超时约束、nonce维护/容量、恢复 epoch、时钟策略，
   再单独评审生产 transport 及 app 挂载；不能直接放宽本适配器的 MockTransport 门禁。
