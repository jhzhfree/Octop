# V1 安装级身份兼容说明

仅用于已部署的 V1 策略。新接入请按 README 的 V2 步骤操作。不要把 V1 安装私钥 / JWKS 初始化用于 V2。

## 构建及安装

需要 Node 22+ 进行构建/测试。插件运行宿主已固定核验 OpenCode **1.14.30**，依赖 `@opencode-ai/plugin@1.14.30` 和 `effect@4.0.0-beta.57`，不要未经测试替换成 2.x SDK。

```sh
cd integrations/opencode-identity
npm ci
npm test
npm pack
```

可直接从构建后的文件加载；不需要发布 npm 包。在受管理的 OpenCode 全局配置中添加：

```json
{
  "plugin": [[
    "file:///opt/akb-opencode/dist/index.js",
    {"configFile": "/etc/akb-opencode/identity.json"}
  ]],
  "permission": {
    "akb_search": "ask",
    "akb_query": "ask",
    "akb_bases": "ask"
  }
}
```

将路径换成实际构建/安装位置。两个 Agent 都使用这一份配置；权限也可按 OpenCode 的正常规则设为 allow/deny，不要用跳过权限的命令来验证插件。

## 保护配置与签名材料

复制 `config.example.json` 为工作区之外的受保护文件，填写真实值：

- `workspaceRoot` 必须匹配 OpenCode 的实际启动/执行 `directory`，不要用非 Git 项目可能为 `/` 的 `worktree` 代替。
- `agents` 的 key 为 OpenCode 实际配置名，value 为管理员批准的稳定 Agent UUID；必须与 AKB 登记一致。未知 Agent 直接拒绝。
- `serviceKeyFile` 保存一个由 AKB 工作负载策略 API 签发的共享 Key；`privateKeyFile` 保存安装实例的 RSA 私钥。
- `applicationPrincipalId` 为共享 Key 所属 APPLICATION 主体的 ID（字符串），不是 Key ID。
- 文件采用当前运行账户所有、0600 权限，并放在批准工作区之外；Windows 需要额外使用 ACL 保护。
- 原点必须 HTTPS，无 URL 凭据/查询/片段；仅隔离开发可显式设置 `allowInsecureLoopback: true` 使用 localhost/127.0.0.1。

```sh
node dist/cli.js keygen /etc/akb-opencode/signing.pem
node dist/cli.js jwks /etc/akb-opencode/signing.pem > /etc/akb-opencode/jwks.json
chmod 600 /etc/akb-opencode/identity.json /etc/akb-opencode/service.key
```

通过受管理 HTTPS 服务发布**公开** JWKS。也提供仅监听 loopback 的辅助服务，可在其前面使用 HTTPS 反向代理：

```sh
node dist/cli.js serve-jwks /etc/akb-opencode/signing.pem 18082
# GET /.well-known/jwks.json；其他路径一律 404
```

AKB 策略中的 issuer/audience/installation/workspace/JWKS 与插件保持一致。私钥不发送 AKB；AKB 只获取公开密钥。Key/私钥更换后重启 OpenCode 和公钥辅助服务；生产公钥发布应保留覆盖旧断言有效期的轮换窗口。

## 使用

```text
akb_bases({})
akb_search({query: "报销规则", knowledgeBaseCode: "HR-POLICY", topK: 3})
akb_query({query: "报销截止时间？", knowledgeBaseId: 12})
```

模型只传业务参数。MCP 下插件在每次 initialize/tools-call HTTP 请求前分别签发 RS256 断言；REST 下有库编码时先按当前 Agent 身份发现库，再选取对应数字 ID。每个请求有独立 jti，绑定方法、路径（含 query）及原始 body 字节，不使用共享可变 Header。

v1.14.30 的 `context.ask` 返回 Effect。插件在任何 await 之前捕获宿主 fiber context，运行并等待真正的权限审批；拒绝、取消、身份错误都不会退回无身份请求。无自动网络重试；再次工具执行产生新 invocation ID，避免误称具备写入幂等语义。

## AKB 前置配置

使用 AKB 的 `docs/api/OPENCODE-WORKLOAD-IDENTITY.md`：建立 APPLICATION、各 AGENT、授权上限与独立 KB grants，再创建工作负载策略、Agent 映射及严格共享 Key。不要拿旧 LEGACY Key 代替。

首版只读。`INGEST`、Memory、写入及用户委托未开放，新模式在服务端拒绝这些 scope。用户身份不是从 Agent 名、OS 用户名或 session ID 推断出来的。

## 验证

```sh
npm test
# 需已部署真实 AKB，并配置至少两个具有不同私有库授权、且有匹配已索引内容的 Agent：
AKB_PLUGIN_CONFIG=/etc/akb-opencode/identity.json npm run test:live
```

`test:live` 用真实 MCP 和 REST 分别检查发现、各自检索有结果、交叉访问被拒绝。真实宿主验证还应运行 `opencode run --agent builder ...` 与 reviewer，并核对 AKB 审计相同 `keyId`、不同 `agentPrincipalId`。模型供应商可替换为确定性的测试服务，但 OpenCode、插件、AKB、数据库和权限判断必须真实运行。

## 信任边界

插件证明的是**受信 OpenCode 运行时声明的执行身份**。部署者需信任宿主及其插件，并保护全局配置。文件放在工作区之外和 0600 能阻止普通工作区访问，并不抵御同 UID 的任意 shell、恶意插件或已被攻陷的 OpenCode 进程。需要防御此类威胁时，将模型 shell/读文件执行放到独立 UID/容器，保护 OpenCode 管理进程及签名材料；不要把此版本称为硬件证明或跨恶意进程的强隔离。

任意 shell 直接 curl 不会自动获得 Agent 身份；必须走插件入口，AKB 的严格 Key 缺少签名时拒绝。配置 Agent 名可被模型修改的部署不属于受信配置；同名重新定义应经过管理员核验映射和批准配置。
