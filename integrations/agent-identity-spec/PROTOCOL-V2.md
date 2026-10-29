# AKB Agent Proof V2 线协议

状态：已实现于 AKB V75 和 SDK 0.2.0。首版仅 RS256、只读知识库。MUST 表示必须遵守，SHOULD 表示默认应遵守。

## 1. 显式 profile

AKB 策略新增 `proofProfile`：

- `INSTALLATION_RS256_V1`：现有 JWKS 认证。
- `AGENT_KEY_RS256_V2`：本协议，Agent 公钥由 AKB 管理登记。

共享 Key 仍是 `APPLICATION_WORKLOAD`，且只绑定一个策略。一个策略里的多个 Agent 可共用 Key；不同平台/安装默认使用各自策略及 Key，可以归属同一 APPLICATION。

服务器 MUST 按已认证 Key 的策略选择 profile，不能按 JWT 内容自由选择验证路径。V2 策略拒绝 V1；V2 客户端遇到不支持时停止，不回退旧模式。注册包版本、SDK SemVer 和协议版本独立。

## 2. HTTP 契约

```http
X-API-Key: <共享服务 Key>
X-AKB-Workload-Assertion: <compact JWS>
Content-Type: application/json
```

不得同时携带 Authorization 或 X-Octop-Identity。HTTPS 必须开启正常证书验证；显式 loopback HTTP 仅供隔离开发环境。插件固定 AKB origin，禁止模型指定 origin、路径或任意 Header，禁止重定向。

CredentialSource 可在读取凭据文件时移除文件末尾换行，但返回值必须已经是无空白、无控制字符的 ASCII token。客户端用同一返回值计算 credential_sha256 并发送 X-API-Key；服务端拒绝含首尾空白的值，不能一处 trim 后匹配、另一处按原值计算摘要。

JWS Header：

```json
{"alg":"RS256","typ":"akb-agent-proof+jwt","kid":"<RFC7638 public key thumbprint>"}
```

拒绝 `alg=none`、算法混淆、未知 `crit`，以及 `jku,x5u,jwk` 等外来密钥来源。仅使用管理员登记且归属当前绑定的公钥。Header 和 Payload 必须是无重复字段的 JSON 对象。

## 3. Payload

| 字段 | 规则 |
|---|---|
| `ver` | JSON 整数 2 |
| `iss` | 精确匹配策略 issuer，不能作为动态密钥发现地址 |
| `aud` | 首版要求单个字符串，精确匹配策略 audience |
| `resource` | AKB 管理员配置的公共 origin，例如 `https://akb.example.com`，无路径、query、fragment；不信任客户端 Host/Forwarded 自行决定 |
| `policy_id` | 当前 Key 策略 ID 的十进制字符串 |
| `azp` | Key 所属 APPLICATION Principal ID 的十进制字符串 |
| `sub` | `agent:<externalAgentId>`；首版 externalAgentId 使用规范小写 UUID |
| `platform_id` | 精确匹配策略 platformId |
| `installation_id` / `workspace_id` | 精确匹配批准安装与工作区 |
| `runtime_agent` | 可信宿主执行身份映射键，精确匹配绑定 |
| `session_id` | 宿主会话 ID，1–128 字符；平台没有消息 ID 时不得伪造已有消息关联 |
| `message_id` | 可选，存在时 1–128 字符，当前宿主实际消息 ID |
| `invocation_id` | 每次工具执行生成 UUID，多 HTTP 请求可共用 |
| `iat` / `exp` | 整数秒；`0 < exp-iat <= 60`，`exp > now`，`iat <= now+30`；验证方不因库默认 clock skew 再放宽过期限制 |
| `jti` | 每 HTTP 请求独立 UUID，原子消费；不能用 invocationId 替代 |
| `credential_sha256` | 该请求 X-API-Key 实际值 UTF-8 字节的 SHA-256 小写十六进制，不含 Header 名及 Bearer 前缀 |
| `request_binding.method` | 首版 GET 或 POST，大写 |
| `request_binding.target` | 实际后端绝对路径 + 可选原始 query，最多 2048 字符 |
| `request_binding.body_sha256` | 发送的准确 body 字节 SHA-256，小写十六进制；GET 为空字节哈希 |

适配器标识字段遵循 `[A-Za-z0-9_.:-]{1,128}`；平台原生 ID 若不符合，应在受控配置建立无歧义别名，而不是截断或有损替换。session/message 字段可使用已校验的无控制字符字符串；具体映射需通过平台适配验收。

请求正文不超过 1 MiB，完整证明不超过 8192 字符。hash 不使用平台默认字符串编码；必须 UTF-8。HTTP 客户端 MUST 在发出前固定私有字节副本，用同一副本计算摘要并发送，之后不得重排 JSON、改换换行或压缩正文。

target MUST 保留 query 顺序和百分号编码，不进行 decode/re-encode。当前 SDK 要求独立 origin 下保持同一路径，反向代理不能重写被签名路径或正文；带外部路径前缀的部署需要先扩展受管理路由配置并测试，不能临时信任任意代理 Header 来绕过检查。

资源 origin 采用 URL 的规范 origin 表示，HTTPS 默认端口省略；AKB 端使用受控部署配置。多个公共 origin 默认使用不同策略。自定义业务 Header 不在签名范围，不能被用作扩大权限的授权依据。

## 4. 服务器验证顺序

1. 限制 Header/body 大小，执行网关级 IP/连接限流。保存精确请求字节。
2. 验证共享 Key 哈希、状态、到期、APPLICATION 状态，取得固定策略。
3. 检查策略 ACTIVE、profile 为 V2，解析有界 JWS。未验签字段只允许作为有界数据库查找提示，不得授权、写“已验证身份”审计或读取任意网络地址。
4. 使用策略 ID、声明的稳定 Agent ID 和 runtimeAgentKey 定位映射，再按该映射的 kid 查公钥。必须检查公钥归属、算法及状态。A 的 kid 不得用于 B。
5. 以登记公钥验签，然后再次核对所有声明、时间、public resource 和 Key 摘要。不能因为查到了映射就略过验签。
6. 比对请求方法、target、body SHA-256。严格拒绝缺字段、错误类型、重复字段和未知协议版本。
7. 原子消费 jti。推荐唯一键 `(policy_id,kid,jti)`，保留至 exp 加安全清理余量；多副本使用同一原子存储。数据库写入失败不能放行。
8. 建立不可变请求身份，执行应用/Agent 状态、配额、只读 scope、KB grants 和标签约束。
9. 记录已验证 Agent、profile、kid、公钥登记 ID、session/message/invocation。不得记录完整断言、服务 Key 或私钥。

新表与 V1 重放表分开或明确做版本化；不能直接改变现有 V1 唯一键而破坏旧认证。

## 5. 错误与重试

| HTTP | 对外稳定错误 | 行为 |
|---|---|---|
| 401 | `AKB_WORKLOAD_PROOF_INVALID` | 缺失、签名/时效/绑定错误或重放；不回显内部密钥细节 |
| 403 | `KB-403-001` | 已知 Agent/密钥被停用、映射不允许、scope 或资源未授权 |
| 413 | `REQUEST_TOO_LARGE` | 正文超过限制 |
| 429 | `KB-429-001` | 受控限流，可提供 Retry-After |
| 503 | `AKB_IDENTITY_UNAVAILABLE` | 身份存储不可用，拒绝继续 |

未知绑定/未知 kid 可统一 401，避免枚举；只有可信验证后的状态才返回有区分度的 403。MCP 的 HTTP 认证错误和 JSON-RPC 业务错误都必须检查，不以 HTTP 200 代表业务成功。

首版 SDK 不自动重试网络请求。将来重试必须生成新 jti 和签名，不能复用证明；业务写入还需独立幂等机制。取消后不再签名或发送新请求。

## 6. 公钥登记挑战

管理 API 见 AKB 配套文档。挑战响应必须包含候选公钥指纹、绑定及随机 nonce；签名端校验本地 owner/kid 与挑战一致，只接受专用登记用途，不能把任意请求当成登记挑战签名。

登记 JWS 的 `typ=akb-key-registration+jwt`，声明至少包含：

```text
ver=2
purpose=akb.agent-key.register
aud=akb-agent-key-registration
challenge_id, nonce, policy_id, binding_id
sub=agent:<externalAgentId>
jkt=<RFC7638 thumbprint>
iat, exp
```

挑战使用 CSPRNG 至少 32 字节 nonce，5 分钟到期，登记证明 exp 不超过挑战到期。服务端核对创建挑战的管理员及完整上下文，验证持钥后在同一事务中消费挑战、登记公钥、写管理审计。重复完成同挑战不得再次登记或改变权限。

## 7. 安全能力与未覆盖部分

V2 增加 Agent 公钥所有权、公共资源 origin 与具体 Service Key 绑定，降低安装共享私钥和跨资源/凭据搬运风险。它仍是应用自定义的请求证明，不宣称是 OAuth DPoP、用户 OIDC、硬件证明或平台进程完整性证明。

合法断言被截获后仍存在首次抢先发送的竞态；HTTPS、端点保护和单次 jti 共同降低风险。单次消费仅约束认证，不保证业务恰好执行一次。
