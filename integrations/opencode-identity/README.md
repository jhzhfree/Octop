# OpenCode Agent 身份插件 0.3.0

适用于 **OpenCode 1.14.30、Node.js 22+**，配套通用 SDK **0.4.0** 和支持 AKB V76 的服务端。其他 OpenCode 版本尚未核验。文件密钥 Provider 在 POSIX 使用权限位、在 Windows 使用 ACL 收紧为仅当前账户，因此宿主需使用 NTFS 等支持 ACL 的卷。

两个 Agent 共用一个 AKB Service Key，分别使用自己的私钥和稳定 UUID。模型只填写检索问题等业务参数，插件从 OpenCode 的真实工具执行上下文获取运行 Agent，并通过通用 SDK 调用 AKB 的 MCP / REST。

插件注册 `akb_bases`、`akb_search`、`akb_query`。它不会透明修改所有原生 MCP / shell / curl 请求；需要身份闭环的知识调用应使用这些工具。权限审批通过 OpenCode 的真实 Effect 执行，拒绝或取消不发送请求，失败不自动降级。

## 新接入：简化第一版（推荐）

不再交换平台配置、填写策略 ID 或手工签署登记挑战。已有 V1/V2 配置继续使用，下面旧流程仅用于高级兼容接入。

1. 使用独立 [agent-keygen 工具](../../tools/agent-keygen/README.md)，为每个 Agent 生成一份密钥对。该工具无 SDK / 平台依赖。
2. AKB「Agent 接入」新建服务接入，填写调用方访问的 AKB 根地址，选择可读知识库，保存一次性共享服务 Key。同一接入下 Agent 继承相同可读范围，默认仅未设置权限标签的内容。
3. AKB「添加 Agent」填写名称、identity.json 中的 agentId，导入 public.jwk。私钥不能上传 AKB。
4. 按下文构建并安装 SDK 0.4.0 / 插件 0.3.0，复制随包的 `config.simple.example.json` 为受保护配置文件。只需修改真实路径、AKB 地址、运行 Agent 名称、UUID 和私钥文件路径。所有文件放在工作区外，并收紧权限。
5. 在 OpenCode 全局插件配置中一次加载本插件，`configFile` 指向上述配置。插件在启动时自动发现公开认证配置、验证公钥登记、导入并选中私钥，无需额外 CLI 激活。
6. 分别让两个 OpenCode Agent 使用 `akb_search` / `akb_query`。真实身份来自 OpenCode 工具上下文，业务参数不能选择 Agent 身份。

简化配置示例（完整双 Agent 版本见 `config.simple.example.json`）：

```json
{
  "configVersion": 3,
  "proofProfile": "AGENT_KEY_RS256_V2",
  "workspaceRoot": "/srv/engineering",
  "origin": "https://akb.example.com",
  "transport": "mcp",
  "serviceKeyFile": "/secure/akb/service.key",
  "keyStore": {"provider": "node-file", "rootDirectory": "/secure/akb/keys"},
  "agents": [{"runtimeAgentKey": "builder", "agentId": "e21cce9c-1b16-4ab8-a76a-bb761df919b8", "privateKeyFile": "/secure/alpha/private.pem"}]
}
```

全局 OpenCode 配置沿用下文 `plugin` 的本地安装入口与 `configFile` 配置方式。`runtimeAgentKey` 必须等于 OpenCode 中实际 Agent 名称，不能填写展示别名。私钥源文件与配置只允许运行账户读取（POSIX 0600，父目录可信；Windows 使用账户独占 ACL）。更换私钥后重启 OpenCode，验证成功后再在 AKB 撤销旧公钥。不要给同一个平台的两个 Agent 配置同一 AKB Agent ID。

未登记、公钥不匹配、服务停用、Agent 停用或请求被拒绝均不会回退到原有认证。启动时会发出公开配置发现和签名身份测试；实际知识查询仍须通过 OpenCode 的权限审批。每次请求继续签名 method/target/body、服务 Key 摘要和短期防重放标识。

## 项目是否独立

本目录已有独立 `package.json`、TypeScript 构建、测试和 TGZ 打包入口，运行时不依赖 Octop 的 Python 后端或前端。**当前源码开发仍依赖相邻 SDK 目录**（`file:../agent-identity-sdk`），所以不能只复制本目录就声称是完整独立源码工程。

简化第一版只将密钥生成工具作为独立可打包工程交付（`tools/agent-keygen`）。SDK 和平台适配器继续在现有工程中维护，不要求另建插件项目或部署常驻服务。后续是否迁移源码仓库可按发布需要决定，历史选项见 [项目交付决策](../agent-identity-spec/PROJECT-DELIVERY.md)。尚未创建新远端仓库，也未发布公共 npm。

## 1. 从源码构建交付包

在 Octop 仓库根目录执行，顺序不能颠倒：

```sh
cd integrations/agent-identity-sdk
npm ci
npm test
npm pack
cd ../opencode-identity
npm ci
npm test
npm pack
```

得到两个文件：

- `integrations/agent-identity-sdk/akb-agent-identity-sdk-0.4.0.tgz`：通用 SDK 和管理 CLI。
- `integrations/opencode-identity/akb-opencode-identity-0.3.0.tgz`：OpenCode 适配器，已捆绑相同 SDK 的运行代码。

CI 会构建、测试并上传这两个包。它们尚未发布到 npm registry；不要直接用未经发布的包名在线安装。V1 老部署的初始化步骤独立存放于 [LEGACY-V1.md](./LEGACY-V1.md)，新接入不要生成 V1 安装私钥或配置 JWKS 服务。

## 2. 在 OpenCode 宿主安装

管理员先准备由受信运行账户管理的安装目录，例如 `/opt/akb-opencode`，然后以该账户安装两个本地包：

```sh
cd /opt/akb-opencode
npm install /path/to/akb-agent-identity-sdk-0.4.0.tgz /path/to/akb-opencode-identity-0.3.0.tgz
```

部署机器只需要这两个 TGZ，不需要克隆 Octop、启动 Octop 服务或保留相邻源码目录。安装结果必须保留完整 `node_modules`，不要只复制 `dist/index.js`。

下文的 `akb-agent-identity` 是 SDK 的管理命令。未加入 PATH 时，使用 `/opt/akb-opencode/node_modules/.bin/akb-agent-identity` 代替。

## 3. 准备配置并连接 AKB

管理员为 OpenCode 运行账户准备工作区之外的受保护目录。示例：

| 项目 | 示例路径 | 要求 |
|---|---|---|
| 实际工作区 | `/srv/engineering` | 必须匹配 OpenCode 执行的 `directory` |
| 配置、owner、binding、挑战文件 | `/etc/akb-agent-identity/` | 当前运行账户所有，目录 0700、文件 0600 |
| 共享服务 Key | `/etc/akb-agent-identity/service.key` | 0600，不写入源码、命令参数或模型工作区 |
| Agent 私钥存储 | `/var/lib/akb-agent-identity/keys` | SDK 创建 / 检查目录 0700、文件 0600 |

父目录不能被其他普通用户写入；同 UID 的任意 shell 仍可能读取私钥，见末尾信任边界。不要在 Agent 工作区内保存这些文件。

首次接入可复制随包提供的 `config.bootstrap.example.json` 为受保护的 `identity.json`。填写实际平台安装实例、工作区、AKB HTTPS 根地址、issuer 和 audience。这份引导配置的 `bindings` 为空，只用于导出接入信息，**尚不能启动业务调用**。

```sh
akb-agent-identity setup export /etc/akb-agent-identity/identity.json > akb-setup.json
```

导出只包含公开平台参数，不包含私钥、服务 Key 或路径。已有完整 V2 配置也可导出；SDK 会核对所有绑定中的 issuer / audience / 平台范围，发现不一致会拒绝。

在 AKB 打开 **接口服务 → 更多能力 → 凭证 → Agent 身份接入 → 连接新平台**，导入 `akb-setup.json`，命名应用，选择应用可读知识库。下载 AKB 返回的公开连接配置，将策略 ID、应用主体 ID、issuer、audience、resource 合入本地配置中的对应绑定。该下载文件不是完整 OpenCode 运行配置，不能直接覆盖 `identity.json`。

在 AKB 为这个连接签发一个只读、带期限的严格共享 Key，安全写入 `serviceKeyFile` 指定的文件。不要用原有 LEGACY Key 代替。

## 4. 为每个 Agent 生成并登记独立密钥

以 `builder` 为例，安装实例和工作区值必须与上一步一致：

```sh
akb-agent-identity identity create /etc/akb-agent-identity/builder-owner.json opencode opencode-office-01 engineering-01
akb-agent-identity agent init /var/lib/akb-agent-identity/keys /etc/akb-agent-identity/builder-owner.json
akb-agent-identity key export-public /var/lib/akb-agent-identity/keys /etc/akb-agent-identity/builder-owner.json '<builder-kid>' > builder-public.json
```

`identity create` 生成稳定 UUID，已存在时不会覆盖；`agent init` 返回公钥 `kid`，将其填入第三条命令。私钥不会输出。`builder-public.json` 导入 AKB 的 Agent 绑定窗口后，**运行 Agent 键填写 OpenCode 的真实配置名 `builder`**，再为该 Agent 选择知识库授权。

AKB 生成登记挑战后下载 `challenge.json`。根据随包 `config.v2.example.json`，将该 Agent 的完整绑定条目保存为受保护的 `builder-binding.json`，至少包含：策略 ID、绑定 ID、应用主体 ID、真实运行 Agent 键、owner 文件中的 UUID、平台 / 安装 / 工作区、issuer、audience、resource。所有占位值必须替换成 AKB 返回或本地生成的真实值。

```sh
akb-agent-identity key prove-registration /var/lib/akb-agent-identity/keys /etc/akb-agent-identity/builder-binding.json '<builder-kid>' /etc/akb-agent-identity/challenge.json
```

把输出的专用登记证明交回创建挑战的 AKB 管理员完成登记。然后把该绑定条目加入 `identity.json.bindings`，执行：

```sh
akb-agent-identity key select /etc/akb-agent-identity/identity.json builder '<builder-kid>'
```

`key select` 会实际调用 AKB，确认预期 Agent、策略、ACTIVE 公钥后才选中密钥。对 `reviewer` 重复上述步骤，生成另一个 owner UUID 和独立私钥；它们共用同一个 `serviceKeyFile`。最终配置形状见 `config.v2.example.json`。

```sh
akb-agent-identity doctor /etc/akb-agent-identity/identity.json
```

`doctor` 只检查本地配置和活动密钥；本地 ready 不代表 AKB 联通成功，应以 `key select` 的远端验证和实际检索为准。

## 5. 在 OpenCode 全局配置加载一次

配置必须由管理员保护，安装及配置路径换成实际值：

```json
{
  "plugin": [[
    "file:///opt/akb-opencode/node_modules/@akb/opencode-identity/dist/index.js",
    {"configFile": "/etc/akb-agent-identity/identity.json"}
  ]],
  "permission": {
    "akb_search": "ask",
    "akb_query": "ask",
    "akb_bases": "ask"
  }
}
```

两个 Agent 共用这一份插件配置，不分别绑定 MCP。工作区使用真实 `directory`，不要用非 Git 目录可能返回 `/` 的 `worktree`。更改 Agent 映射、工作区或 AKB 地址后重启 OpenCode；后续调用会重新读取当前活动密钥和服务 Key。

模型工具只传业务参数，例如：

```text
akb_bases({})
akb_search({query: "报销规则", knowledgeBaseCode: "HR-POLICY", topK: 3})
akb_query({query: "报销截止时间？", knowledgeBaseId: 12})
```

不要增加 `agentId`、`kid` 或认证 Header 参数。只读模式不开放写入、Memory 或用户委托。

## 6. 验收与排错

需要真实 AKB、两个不同私有库授权的 Agent、已发布并索引的匹配资料。在源码插件目录可执行：

```sh
AKB_PLUGIN_CONFIG=/etc/akb-agent-identity/identity.json npm run test:live
```

这项脚本直接调用插件适配器，测试 MCP / REST 的发现、检索、问答及交叉访问拒绝。它不能代替真实 OpenCode 宿主验收。宿主已配置可用模型供应商后，分别运行：

```sh
opencode run --agent builder '使用 akb_search 检索我的知识库中的报销规则'
opencode run --agent reviewer '使用 akb_search 检索我的知识库中的发布审批规则'
```

按 OpenCode 正常权限流程批准调用，在 AKB 审计核对：相同 `keyId`、不同 `agentPrincipalId` 和 `signingKid`；各自可读、交叉不可读；拒绝审批时没有请求发出。纯单元测试或模型回答“成功”不能代替这些证据。

| 问题 | 检查顺序 |
|---|---|
| 导出接入信息失败 | 文件 / 父目录权限；是否 V2；issuer / audience 是否齐全且与所有绑定一致 |
| 未知 Agent | 真实 OpenCode Agent 配置名是否与 `runtimeAgentKey` 一致；配置修改后是否重启 |
| 身份未就绪 / 401 | 严格共享 Key 是否有效；候选公钥是否完成登记；是否成功 `key select` |
| 身份通过但无知识 | 应用与 Agent 是否都授权目标库；资料标签、发布状态、索引及检索问题 |
| 升级后插件加载失败 | 完整安装目录是否保留；OpenCode 是否仍为已核验 1.14.30；Node 是否 22+ |

轮换：SDK `key rotate` 仅生成候选，完成登记和 `key select` 后再在 AKB 退役 / 撤销旧钥。共享 Key 轮换后同步更新凭证文件。升级前备份受保护的配置和密钥；回滚插件版本时保留身份和密钥，不自动回退 V1 / LEGACY。丢失私钥不能通过改 Agent ID 或自动新建文件恢复，应由管理员重新登记新钥。

## 信任边界

插件证明受信 OpenCode 宿主的执行身份。受保护目录不能防御同 UID 任意 shell、恶意插件或已被攻陷的宿主。需要强隔离时，隔离模型工具执行账户 / 容器，保护宿主和签名器。不得将任意 curl 的参数或用户自己传入的 Agent 名作为可靠身份。

完整跨平台协议和开发规范可在[项目文档](https://github.com/jhzhfree/Octop/tree/codex/opencode-workload-identity/integrations/agent-identity-spec)阅读。当前公共 npm 未发布、独立远端仓库未创建；这两项需按项目交付决策另行落实。

## 使用独立本地密钥工具

平台初始化工具可单独安装 `@akb/agent-keygen` TGZ（源码在 Octop/tools/agent-keygen），无需启动 Octop。

```sh
agent-keygen generate --out /secure/opencode/alpha
# 轮换：agent-keygen generate --out /secure/opencode/alpha-next --agent-id <原UUID>
```

也可在受信管理命令中 `import { generateToDirectory } from '@akb/agent-keygen'`，传入不存在的目录，取得 Agent ID 与公钥。私钥保存在 0600/账户独占 ACL 文件；不得发送到模型工具结果。登记 public.jwk 后，按 SDK 的导入/选择流程或简化配置使用私钥。SDK 自身生成密钥也复用该工具；插件无需复制 RSA 或 RFC7638 实现。新身份生成不应放在普通聊天工具或插件自动启动回调里。

AKB V79 会暂停历史连接以确认 SERVICE_SCOPE 或 AGENT_INTERSECTION；请管理员确认并启用后再调用。不同授权模式不会通过平台类型或 UI 显示自动切换。
