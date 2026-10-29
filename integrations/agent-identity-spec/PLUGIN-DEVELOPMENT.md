# 第三方 Agent 平台插件开发文档

适用：Agent Identity SDK 0.2.0、AKB V75 Agent Proof V2。实现位于相邻 agent-identity-sdk；当前以单包稳定子入口交付，未发布公共 npm，可本地构建 TGZ 安装。OpenCode 插件支持 V1/V2 显式配置。

## 1. 最小交付范围

新平台插件只完成四件事：

1. 注册 `akb_bases`、`akb_search`、`akb_query` 三个业务工具。
2. 从宿主的真实执行上下文取得运行 Agent、会话、工作区、取消信号。
3. 将平台审批机制包装成 `authorize()`；审批拒绝后不发送 AKB 请求。
4. 调用通用 Runtime，将结果映射回平台工具结果。

不需要实现 RSA/JWT、托管 JWKS、运行本地 HTTP 身份服务，或给每个 Agent 配置独立 MCP 服务。每个 Agent 仍需独立登记身份和公钥，这不等于每 Agent 单独配置连接。

## 2. 接入能力判断

| 平台能力 | 适配策略 |
|---|---|
| 自定义工具可获得可信执行 Agent ID | 直接做薄插件 |
| 有可信逐调用 HTTP/MCP 拦截上下文 | 可做 transport 适配，仍调用同一核心 SDK |
| 工具可得到 Agent，但原生 MCP hook 不提供 Agent | 采用自定义工具代理，OpenCode 1.14.30 属此类 |
| 只提供静态 MCP 配置/共享 Key，没有执行身份上下文 | 无法可靠区分 Agent；需扩展宿主、受控 launcher 或执行通道 |
| 只有模型能填写 Agent 名/环境变量/业务参数 | 不满足可信身份来源，不能用来签发身份 |

不能将全局“当前 Agent”变量、最近一次消息的 Agent、会话默认 Agent 或模型自报名字作为并发调用的身份依据。环境变量只有在受保护宿主按隔离执行实例注入、且执行者不能替换或借用时才可能成为受控通道的一部分。

## 3. 开发目录建议

```text
adapter-your-platform/
  package.json                  # 仅此包依赖平台 SDK
  src/platform-adapter.ts       # capture + authorize
  src/tools.ts                  # 工具 schema 和结果转换
  src/index.ts                  # 装配通用 SDK
  test/host.test.ts             # 实际宿主测试

通用 SDK 独立维护：
  agent-identity-core/
  agent-keystore-node/
  agent-identity-runtime/
  knowledge-client/
```

Node SDK 参考运行时 Node 22+；其他语言实现必须遵守同一线协议和测试向量，不必通过 Node sidecar。第一版不新增常驻服务，避免把模块解耦变成部署耦合。

## 4. 统一接口

[contracts.ts](contracts.ts) 是可类型检查的接口草案。平台只实现：

```ts
interface PlatformAdapter<NativeContext> {
  capture(native: NativeContext): CapturedExecution;
}

interface CapturedExecution {
  readonly context: HostExecution;
  authorize(summary: ApprovalSummary): Promise<void>;
}
```

`capture()` 必须同步执行。这里既捕获身份，也捕获平台审批所依赖的运行上下文；不能先 await 其他工作，再去读“当前执行 Agent”。返回的身份在该调用期间不可变。

输入字段：

| SDK 字段 | 平台适配要求 |
|---|---|
| platformId | 插件固定平台标识，非模型输入 |
| runtimeAgentKey | 当前实际执行 Agent 的不可变 ID；平台只有受控配置名时用批准配置名 |
| sessionId | 当前宿主会话 ID |
| messageId | 有则填写实际消息 ID；没有则省略，不伪装为可追溯消息 |
| workspaceRoot | 当前真实执行目录，Runtime/Provider 根据平台语义规范化并核验批准范围 |
| abortSignal | 平台当前工具调用的取消信号 |

Runtime 将平台键查到只读 `AgentBinding`，获取稳定 externalAgentId，再打开对应密钥。插件和模型不传 `agentId` 给业务工具，不接受业务参数覆盖上述字段。

`BoundRequestProofIssuer.issue()` 也没有 agentId 参数：签名器在创建时已绑定批准身份。这个接口减少误用，不是对同进程恶意插件的安全沙箱。

## 5. 平台无关的最小插件示例

完整示例见 [examples/platform-adapter.ts](examples/platform-adapter.ts)，其中宿主接口是演示接口，不能当成某个实际平台 API。

```ts
const adapter = {
  capture(host) {
    const context = Object.freeze({
      platformId: "your-platform",
      runtimeAgentKey: host.executingAgent.immutableKey,
      sessionId: host.session.id,
      messageId: host.session.messageId,
      workspaceRoot: host.approvedDirectory,
      abortSignal: host.signal,
    });
    return {
      context,
      authorize: summary => host.permissions.request(summary),
    };
  },
};

// createRuntime 来自 @akb/agent-identity-sdk；类型见 contracts.ts。
const runtime = createRuntime({ adapter, bindings, keyStore, credentials, origin, transport: "mcp", validateWorkspace });

registerTool("akb_search", businessSchema, (args, host) =>
  runtime.invoke(host, {
    operation: "search", query: args.query,
    knowledgeBaseId: args.knowledgeBaseId,
    knowledgeBaseCode: args.knowledgeBaseCode, topK: args.topK,
  }));
```

注册前必须验证业务 schema；SDK 还要二次白名单构造请求，避免工具 schema 被平台跳过后任意字段被带到 AKB。operation 由具体工具固定，不允许 args 中同名字段覆盖。

工具参数约定：

```json
{
  "query": "报销截止时间？",
  "knowledgeBaseCode": "HR-POLICY",
  "topK": 3
}
```

query 长度 1–4000，topK 为 1–50 整数，库编码长度 1–128，库 ID 为安全正整数；`akb_bases` 参数为空。未知字段应拒绝或明确丢弃，绝不用于身份、路由、Header 或签名控制。ID/编码同时存在时必须指向同一授权库。

## 6. OpenCode 1.14.30 适配注意事项

已核验原有插件 SDK：`context.agent` 是执行 Agent 配置名；`context.directory` 是项目执行目录；`context.ask()` 返回 Effect，不能按普通 Promise await。

适配器的关键实现应保留现有已验证做法：

```ts
import { Effect, Fiber } from "effect";

const adapter = {
  capture(context) {
    // 必须在任何 await 之前、仍处于宿主工具执行 fiber 时捕获。
    const runtimeContext = Fiber.getCurrent()?.context;
    const execution = Object.freeze({
      platformId: "opencode",
      runtimeAgentKey: context.agent,
      sessionId: context.sessionID,
      messageId: context.messageID,
      workspaceRoot: context.directory,
      abortSignal: context.abort,
    });
    return {
      context: execution,
      async authorize(summary) {
        const effect = context.ask({
          permission: `akb_${summary.operation}`,
          patterns: ["*"], always: ["*"], metadata: { operation: summary.operation },
        });
        await Effect.runPromise(
          runtimeContext ? effect.pipe(Effect.provide(runtimeContext)) : effect,
          { signal: context.abort },
        );
      },
    };
  },
};
```

Effect 与 OpenCode 依赖留在平台适配包，不进入 core。此版本不要以 worktree 替代 directory，非 Git 项目 worktree 可能为 `/`。新平台版本需重新验证上下文和审批语义，不能仅更新包版本号。

由于以上示例依赖宿主 fiber，工具入口必须直接调用 `runtime.invoke(context, request)`；Runtime 要在自身首次 await 前调用 capture。将 invoke 包进异步队列后才 capture，可能丢失宿主运行上下文。

## 7. 配置与密钥初始化

配置示例见 [examples/config-v2.json](examples/config-v2.json)。配置与密钥根目录受管理、位于 Agent 工作区之外；路径只是示意，Windows 需要受保护账户目录和 ACL。

```text
bootstrap 管理流程：
1. 操作员确认安装 ID、工作区和运行 Agent 映射。
2. 为新 Agent 生成并持久化稳定 UUID。
3. 调用 keyStore.initialize(owner)，生成候选公私钥。
4. 导出只含公钥的登记包，管理员在 AKB 完成审批与持钥挑战。
5. 使用候选钥请求 AKB 身份探针，核对 agentId/kid/profile。
6. 选中已登记 kid，启动普通业务插件。
```

普通插件启动只加载已登记身份。不能在首次业务调用时静默生成 Agent、请求管理员权限或自动注册。密钥丢失时应明确报错并走恢复流程。

SDK CLI 已实现 identity create、agent init、key rotate（仅 stage）、key export-public、key prove-registration、key select、doctor；完整参数见 SDK README。原插件 CLI 保留 V1 keygen/jwks。

## 8. 通用 Runtime 的必要行为

```text
同步 capture 宿主上下文
  → 校验平台、Agent、工作区，匹配只读绑定
  → 创建不可变调用身份和 invocationId
  → await 平台 authorize
  → 检查取消
  → 打开当前 Agent 的签名器
  → 调用业务客户端
      每个 HTTP 请求：
      固定业务字节 → 读取一次 Service Key → 计算摘要
      → 创建新 jti/短期断言 → 签名 → 发送相同字节与 Key
  → 校验协议/业务结果 → 输出安全结果与追踪元信息
```

不得使用可变全局 agent/header 对象。审批前不发送知识库发现或 initialize 请求。Provider 可以在启动加载本地受保护材料，但远程签名及业务请求应在批准后进行。

MCP 客户端当前目标为 AKB `/api/mcp` 的请求响应式 JSON-RPC，初始化与 tools/call 分别签名；不是对任意有状态、SSE 或其他厂商 MCP 服务的通用兼容承诺。将来连接复用必须保证每个请求保留自己的认证证明。

REST 客户端支持库发现、search、query；按库编码发现时仍使用当前 Agent 身份。成功收到 HTTP 不代表成功，需同时检查 AKB envelope/JSON-RPC error/isError。响应应有流式大小上限，默认沿用现有 2 MB 限制。

## 9. 错误、日志和取消

稳定错误代码见 contracts.ts。推荐映射：身份缺失、未登记、审批拒绝、取消、密钥不可用、协议不支持、认证失败、资源拒绝、限流、上游不可用。

- 审批拒绝/取消：零业务上游请求，不尝试其他 Agent 或旧凭据。
- 401/403：明确拒绝，不换 Key、不猜 Agent、不自动重注册。
- 429/503：显示可重试状态；首版不自动发起重试。
- 日志只记录平台、非敏感身份 ID、invocationId、kid、错误码和耗时；不记录私钥、Service Key、完整断言或原始上游错误体。
- AbortSignal 同时控制审批、签名和 fetch；KMS 不支持取消底层签名时也必须丢弃取消后的结果，不能继续发送。

## 10. 插件验收清单

| 维度 | 必须验证的结果 |
|---|---|
| 并发 | 同一配置、共享 Key 下 A/B 并发，身份和 Header 不串用 |
| 上下文 | 宿主真实执行身份传入，模型填入假 Agent ID 不改变身份 |
| 审批 | 真正执行宿主审批，deny 后 0 上游请求；不只 mock 一个 Promise |
| 取消 | 审批前、签名中和网络中取消，均不再发送新请求 |
| 持久化 | 重启保留同一 Agent ID 与 key；并发初始化不会生成两个活动身份 |
| 公钥绑定 | A 的私钥签 B 的 sub，即使 A/B 共用 Key 也拒绝 |
| 请求绑定 | 改正文、方法、路径、query、resource 或 Key，断言失效 |
| 重放 | 同一证明重复、跨 AKB 副本同时提交，仅一次认证可成功 |
| 授权 | 自有私有库有真实结果，访问另一 Agent 私有库拒绝 |
| 生命周期 | A 撤销不影响 B；新钥切换后旧钥按退休窗口退出 |
| 兼容性 | V2 不支持时明确失败，不回退 V1/LEGACY |
| 实际宿主 | 在目标平台和声明支持的具体版本运行真实工具调用，并核对 AKB 审计 |

发布前还需跑密钥模块和 HTTP 客户端的共享一致性测试。公共向量只验证线协议样例；SDK、AKB 与 OpenCode 的实际测试另行运行。

## 11. 分阶段落地

1. 从现有 OpenCode 插件提取平台无关模块，保持 V1 行为及已有测试不变；暂不改变身份信任模型。
2. 实现 AgentKeyStore、稳定 UUID、独立密钥、登记包和管理 CLI；不向业务工具开放管理操作。
3. AKB 增加 V2 profile、公钥绑定、挑战登记、身份探针和审计；使用新迁移，不重写已应用 V74。
4. Runtime/Client 支持显式 V2，完成 A/B 双密钥、同 Key 的实际端到端测试。
5. OpenCode 适配包迁移到通用 Runtime；由另一平台按本文独立完成适配，验证 SDK 没有残留 OpenCode 依赖。
6. 按安装逐步切换新严格策略，撤销不再需要的旧 Key；不自动原地降低旧策略要求。
