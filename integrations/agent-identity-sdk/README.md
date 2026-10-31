> 简化第一版：SDK 0.4.0 新增受保护 PEM 导入与简化连接发现，仍使用现有 V2 逐请求签名。独立密钥生成工具位于 `tools/agent-keygen`，可完全脱离 SDK 使用。新的 OpenCode 配置使用 configVersion 3，见适配器 README；旧 configVersion 2 与挑战登记流程保留兼容。
>
> `GET /api/v1/external/identity/configuration?agentId=<UUID>` 只返回公开认证坐标，携带共享 Key，不携带私钥，不能读取知识。真正身份必须由后续签名状态测试验证。配置发现禁止重定向，并校验返回 resource 与配置地址一致。

# Agent Identity SDK 0.3.2

平台无关的身份 SDK。每安装共享一个 AKB Service Key，每个 Agent 使用独立 RSA 密钥，AKB 按 Agent 绑定公钥。支持 AKB Agent Proof V2、MCP 和 REST，不需要插件托管 JWKS，也不要求每 Agent 配置独立 MCP 服务。

## 构建和模块

```sh
npm ci
npm test
npm pack
```

Node 22+。当前提供单个 npm 包的稳定子入口，避免部署多个服务；包尚未发布到公共 npm，可通过本地产生的 TGZ 安装。

| 入口 | 职责 |
|---|---|
| `@akb/agent-identity-sdk/core` | 平台无关协议、WebCrypto 摘要、公钥指纹和证明构造；不依赖 OpenCode/Effect/文件系统 |
| `@akb/agent-identity-sdk/keystore-node` | 独立 Agent RSA 3072 密钥、受保护存储、签名句柄和轮换 |
| `@akb/agent-identity-sdk` | createRuntime、平台适配类型与调用编排 |
| `@akb/agent-identity-sdk/client` | AKB MCP/REST、请求绑定、身份探针、超时和错误处理 |
| `@akb/agent-identity-sdk/node` | 受保护文件配置加载和 Node Runtime 装配 |
| `@akb/agent-identity-sdk/registration` | 管理员挑战的专用持钥证明 |
| `@akb/agent-identity-sdk/types` | 类型与可替换 Provider 接口 |

## 插件最小适配

只实现同步 capture、异步 authorize，并注册三个业务工具。capture 必须发生在工具执行的第一个 await 之前。

```ts
import { loadNodeRuntime } from "@akb/agent-identity-sdk/node";

const adapter = {
  capture(host) {
    const context = Object.freeze({
      platformId: "your-platform",
      runtimeAgentKey: host.executingAgent.immutableKey,
      sessionId: host.session.id,
      workspaceRoot: host.directory,
      abortSignal: host.signal,
    });
    return { context, authorize: summary => host.permissions.request(summary) };
  },
};
// host 是说明接口，需替换为平台的实际可信 API。
const runtime = await loadNodeRuntime(configFile, approvedDirectory, adapter);
// 工具 execute 内直接调用，不能先排队 await 后才 capture。
const result = await runtime.invoke(host, { operation: "search", query: args.query });
```

模型工具参数不含 agentId、kid、Header、路径或 origin。Runtime 匹配受管理绑定，等待审批后打开签名器；每个 HTTP 请求使用新 jti，绑定方法、路径/query、正文、资源 origin 和 Service Key 摘要。取消或拒绝不降级、不重试。

其他语言实现可复用同一协议和公共向量，无需运行 Node sidecar。SDK 不负责推断没有可信执行上下文的平台身份。

## 初始化和登记

复制 config.example.json，所有占位 ID 需由管理员登记实际值。文件 Provider 在 POSIX 使用目录 0700、文件 0600、当前运行账户所有、不可被其他账户写入的父目录；在 Windows 使用 icacls 把目录和文件收紧为仅当前账户（含其登录会话与 SYSTEM）的 ACL。配置和密钥均位于 Agent 工作区之外；不支持 ACL 的文件系统会明确拒绝运行。

```sh
# 以下目录与描述文件应由部署管理员创建并保护。
node dist/cli.js identity create /etc/akb/builder-owner.json opencode office-01 engineering-01
node dist/cli.js agent init /var/lib/akb/keys /etc/akb/builder-owner.json
```

第一条只创建一次稳定 UUID，文件已存在则拒绝覆盖；第二条生成候选密钥，重复运行返回一致记录，不替换丢失或损坏的私钥。新钥未登记前不会自动用于业务。

1. AKB 管理员创建 APPLICATION 与 AGENT，配置应用上限及各 Agent 的 KB/标签授权。
2. 创建 `AGENT_KEY_RS256_V2` 策略，设置 platformId、installationId、workspaceId、resourceOrigin；无需 jwksUri。
3. 登记 runtimeAgentKey → externalAgentId → AGENT Principal 映射，签发一个严格共享 Service Key。
4. 导出目标公钥包，管理员通过 `/api/external/admin/workload/policies/{p}/agents/{b}/keys/challenges` 提交 publicJwk。
5. 将管理员返回的 challenge.json 和受保护的 binding.json 交部署端签名，再由管理员完成 `/keys/registrations`。
6. 执行 key select；该命令先向 AKB 发候选钥身份探针，确认归属与 ACTIVE 后才本地选中。

```sh
node dist/cli.js key export-public /var/lib/akb/keys /etc/akb/builder-owner.json '<kid>'
node dist/cli.js key prove-registration /var/lib/akb/keys /etc/akb/builder-binding.json '<kid>' /etc/akb/challenge.json
node dist/cli.js key select /etc/akb/identity.json builder '<kid>'
node dist/cli.js doctor /etc/akb/identity.json
```

binding.json 是 config.bindings 中该 Agent 的完整条目。挑战及 binding 文件使用 0600。公钥包、诊断输出不含私钥；登记证明仅用于管理员本次挑战。普通插件不保存 AKB 管理员 Token。

## 轮换及恢复

```sh
node dist/cli.js key rotate /var/lib/akb/keys /etc/akb/builder-owner.json
```

生成 K2 候选不会切换 K1。登记 K2、探针并 select 后，管理员将 K1 设 RETIRING，配置 UTC `validUntil`（未来一天内），等待所有部署及请求窗口后设 REVOKED。Agent ID 不变。REVOKED 不能恢复，紧急泄漏可直接撤销。

本地 `key retire|revoke <root> <owner-file> <kid>` 只改变本地状态，仍须通过管理员 API 撤销服务端记录。服务端每次检查密钥状态，不受 JWKS 缓存影响。

中断操作可能保留 .lock 或未选中的候选文件；模块不自动抢过期锁或覆盖密钥。操作员先确认无活跃进程，再检查并恢复 manifest/备份；doctor 不会自动修复或注册身份。

## 配置刷新与兼容

活动密钥选择在每次调用读取，Service Key 在每个 HTTP 请求读取一次；新旧调用保留各自快照。更改平台绑定/工作区/origin 配置后重启宿主。只支持显式 V2，不自动回退 V1/LEGACY。原 OpenCode 插件保留独立 V1 兼容路径。

首版只读：bases/search/query。任意 shell/curl 不自动继承身份；用户委托、任务父子权限链、写入及 Memory 不在此版本内。

## 验证与边界

`npm test` 包含独立平台适配、并发身份、审批取消、请求绑定、密钥生命周期、公钥挑战等测试。`npm run test:live` 需要受保护的 `AKB_PLUGIN_CONFIG` 和 `AKB_LIVE_CASES` 文件，见 test/live.mjs。

完整插件指南位于同仓库 `integrations/agent-identity-spec/`；AKB 接入与管理文档位于 AKB 仓库 docs/api/AGENT-IDENTITY-V2.md。

每 Agent 独立密钥不能隔离同 UID 任意 shell、恶意插件或已被攻陷的宿主。需要强隔离时，将工具执行置于独立 UID/容器，保护宿主/签名器，并校验代理调用者身份；只把任意 agentId 交给 KMS 签名不会解决冒充。

## Dashboard host bridge (0.3.0)

The optional `akb-agent-identity-bridge` executable provides stdin/stdout IPC for trusted platform hosts such as Octop's dashboard backend. It reuses the same protected key store, registration proofs and AKB client. It is not an HTTP server or a model tool; only the host may supply the owner and runtime identity. Credentials are sent on stdin and never returned. Key selection requires a successful remote identity probe.

See [dashboard setup and deployment](../agent-identity-spec/DASHBOARD-SETUP.md) for host installation, UI enrollment, security boundaries and frontend rebuild instructions.

## Public onboarding export (0.3.1)

`akb-agent-identity setup export /absolute/path/identity.json > akb-setup.json` exports only the public platform, installation, workspace, issuer, audience and canonical AKB origin for the AKB connection wizard. It works before Agent bindings and keys are enrolled. The protected V2 configuration stays on the host; credentials, key paths, private keys and Agent bindings are excluded. The command does not initialize keys, change registration or enable authentication.


0.3.2 的 `setup export` 可从现有 bindings 读取一致的 issuer/audience，也支持顶层填写这两项的空绑定引导配置（`config.bootstrap.example.json`）。已有绑定与顶层或彼此不一致时明确拒绝导出。两种示例都随包发布，运行配置在完成登记前仍不可用于调用。

## Local key tool and host generation

RSA generation is provided by the bundled `@akb/agent-keygen` package. Its offline CLI and Node API work independently of Octop and can be used by other platform management commands. Trusted bridge `generate` takes `keyScope` and an optional externalAgentId and returns only publicJwk, kid, externalAgentId and keyReference. `connect` resolves that reference only inside the same scope and verifies its Agent ID before importing it. Do not expose bridge management operations as model tools. Installed TGZs include the runtime key generator; sibling source directories are not required.
