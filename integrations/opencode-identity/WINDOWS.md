# Windows 终端 SDK 与 OpenCode 插件制作工具

需要 Node.js 22+、OpenCode **1.14.30**、支持 ACL 的 NTFS 卷，以及本次更新后的 AKB 服务端。制作工具是终端 CLI，无需启动 Octop。生成插件使用现有 `akb_search`、`akb_query`、`akb_bases` 工具，身份取自 OpenCode 执行上下文。不会自动替换其他原生 MCP、shell 或 curl 工具。

## 1. 安装

在可信、不会移动的目录安装交付的 SDK 和插件 TGZ（插件会记录安装入口的绝对路径）。从源码构建时，先在 `../agent-identity-sdk` 执行 `npm ci; npm test; npm pack`，再在本目录执行 `npm ci; npm test; npm pack`。

```powershell
New-Item -ItemType Directory "$env:USERPROFILE\akb-tools"
Set-Location "$env:USERPROFILE\akb-tools"
npm init -y
npm install C:\downloads\akb-agent-identity-sdk-0.5.0.tgz C:\downloads\akb-opencode-identity-0.4.0.tgz
.\node_modules\.bin\akb-plugin-builder.cmd inspect
```

`inspect` 实际执行 `opencode --version` 与 `opencode agent list`，返回版本、是否核验过及 Agent 名称。Windows 默认执行 `opencode.exe`；如果它不在 PATH，在最后追加真实 exe 的绝对路径。仅有 npm 的 `.cmd` 包装器时，请指定其底层 `opencode.exe`，不通过 shell 执行不受控字符串。`inspect` 可在 exe 参数后追加工作区目录来读取项目 Agent；生成插件时会自动在 `workspaceRoot` 执行检测，包含该项目配置。其他版本可检测，但不能生成插件。

## 2. 受保护目录与服务 Key

在 AKB 的 Agent 接入中创建服务并获取共享 Service Key。配置、私钥、审计文件均放在 Agent 工作区外。以下 PowerShell 通过交互输入保存 Key，不把值写入命令参数或历史。目录必须为新目录，父目录不得允许其他普通账户写入：

```powershell
$secureRoot = "$env:USERPROFILE\akb-secure"
if (Test-Path $secureRoot) { throw '请选择新的受保护目录，避免覆盖现有身份' }
New-Item -ItemType Directory $secureRoot | Out-Null
$accountSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls $secureRoot /inheritance:r /grant:r "*${accountSid}:(OI)(CI)F"
if ($LASTEXITCODE -ne 0) { throw '目录 ACL 设置失败' }
$secret = Read-Host 'AKB Service Key' -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
  [IO.File]::WriteAllText("$secureRoot\service.key", [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr), [Text.UTF8Encoding]::new($false))
} finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
icacls "$secureRoot\service.key" /inheritance:r /grant:r "*${accountSid}:(F)"
if ($LASTEXITCODE -ne 0) { throw '服务 Key ACL 设置失败' }
```

运行时还会回读权限，不能保护的文件系统会拒绝使用。不要把私钥上传至 AKB。

## 3. 填写参数并生成插件

复制随包的 `builder.example.json`，修改真实工作区、HTTPS 地址、服务 Key 路径、审计路径和 Agent 名称。Agent 名称必须来自 `inspect`。`agents[].agentId` 可填写已有的稳定互信 UUID；省略时生成新 UUID。已有密钥可以同时填写 `agentId` 和 `keyDirectory`，目录应包含独立 keygen 工具生成的 `identity.json`、`public.jwk` 和 `private.pem`；不会覆盖它们。

默认要求 HTTPS；如使用既有 SDK 支持的可信局域网部署，可显式填写 `allowPrivateHttp: true`，仅允许私网 IP / loopback，不能用于公网明文 HTTP。

`assertionHeader`、`authenticationPath`、`mcpPath` 是可填写的协议坐标，必须与示例一致，不能改成任意 Header/URL。`auditFile` 可省略，默认输出目录下的 `audit.jsonl`；显式填写时也必须直接位于该新目录下。

```powershell
.\node_modules\.bin\akb-plugin-builder.cmd generate .\builder.json "$secureRoot\opencode-bundle"
# 可选指定底层 OpenCode exe：在上述命令最后追加 C:\tools\opencode.exe
```

输出目录必须不存在。任何一步失败会清理本次新建的目录，引用的已有密钥不会删除。产物包括：

| 文件 | 用途 |
| --- | --- |
| `agent-1/identity.json` | Agent 稳定互信 UUID |
| `agent-1/public.jwk` | 在 AKB 为该 UUID 登记的公钥 |
| `agent-1/private.pem` | 仅终端保存的 RSA-3072 私钥 |
| `identity.json` | 插件受保护配置与宿主 Agent 映射 |
| `plugin.mjs` | 已绑定配置路径的可加载插件 |
| `opencode.fragment.json` | 合并到 OpenCode 全局配置的片段 |
| `enrollment.json` | Agent 名称、UUID、公钥文件位置；不含私钥 |
| `audit.jsonl` | 调用开始/完成/失败记录；不记录问题、答案、Key 或签名 |

多个 Agent 分别对应 `agent-2` 等目录。把 `enrollment.json` 中每个 Agent 的 UUID 和对应 `public.jwk` 登记到 AKB，并启用接入及权限。不要把 `enrollment.json` 当成公钥文件直接导入。

## 4. 服务端验签与 MCP 身份确认

```powershell
.\node_modules\.bin\akb-plugin-builder.cmd verify "$secureRoot\opencode-bundle\identity.json" 'C:\work\engineering' build
```

本命令使用填写的 Agent、UUID、私钥路径、服务 Key 路径与 AKB 地址，先发现公开认证坐标，再分别请求：

1. `GET /api/v1/external/identity/status`：确认已登记 Agent、公钥 kid、策略和 ACTIVE 状态。
2. `POST /api/mcp` 的 `get_agent_identity` 工具：确认 MCP 工具收到相同 UUID、公钥、服务端绑定及签名中的终端名称。

两项均通过才返回 `verified: true`。旧服务端没有身份工具时明确失败，不把公开配置发现视为验签成功。私钥不会发送到服务端。该命令直接使用管理端输入验证持钥身份，不能代替真实 OpenCode 宿主调用验收。

合并 `opencode.fragment.json` 到现有全局配置：保留已有插件与权限，将本插件 URI 添加到 `plugin`，再合并三个 `akb_*` 权限项，不要用片段覆盖整个配置。重启 OpenCode 后：

```powershell
opencode run --agent build '使用 akb_search 查询我的知识库'
opencode run --agent review '使用 akb_search 查询我的知识库'
```

按宿主正常流程批准调用，并在本地审计与 AKB 审计中核对不同 Agent UUID / 签名 kid。拒绝授权或审计不可写时不能发出业务请求。`completed` 表示远端调用和本地记录都已完成；如果请求成功后审计写入失败，工具会报错，排查时结合服务端审计确认请求是否到达，不要据此自动重试。

## SDK API

```javascript
import { generateTrustUUID, generateKeyPair, verifyTerminal } from '@akb/agent-identity-sdk/terminal';
const agentId = generateTrustUUID();
const publicInfo = await generateKeyPair('C:\\Users\\Alice\\akb-secure\\new-agent', agentId);
// publicInfo 仅返回 agentId/kid/directory/publicJwk，私钥在受保护的 private.pem。
const verified = await verifyTerminal({
  agentName: 'build', agentId,
  privateKeyFile: 'C:\\Users\\Alice\\akb-secure\\new-agent\\private.pem',
  serviceKeyFile: 'C:\\Users\\Alice\\akb-secure\\service.key',
  workspaceRoot: 'C:\\work\\engineering', origin: 'https://akb.example.com'
});
```

先登记 `public.jwk` 再验证。签名使用 V2 JWS/RS256，包含 `sub=agent:<UUID>`、服务端绑定 `runtime_agent`、真实宿主名称 `terminal_agent`、时间窗、唯一 jti、服务 Key 摘要、HTTP 方法/路径/正文摘要。AKB 校验公钥归属、有效期、防重放和权限。

简化接入的服务端 `runtime_agent` 可能是 UUID，`terminal_agent` 保留本地名称，二者并不要求相同。终端名称是持钥宿主签署的诊断信息，不是新的权限选择器；授权仍取决于登记的 UUID、公钥与策略。密钥不能隔离同账户恶意插件或任意 shell；管理 API 不应暴露为模型工具。

## 验证限制

自动化测试使用 Linux、模拟 OpenCode CLI 和本地模拟 AKB。正式 Windows NTFS ACL、实际 OpenCode 1.14.30 以及真实 AKB 接入需在目标机器执行上述验收步骤；不能以单元测试替代现场联调。
