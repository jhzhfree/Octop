# 从 AKB / Octop 页面完成 Agent 身份接入

本功能随 `codex/opencode-workload-identity` 分支提供。更新源代码后需要重新构建并重启后端和前端部署；单独更新 Python / Java 后端不会更新已部署的静态页面。

## 先确认在哪个平台使用

| 你在哪里运行 Agent | 使用哪个组件 | 在哪里操作 |
|---|---|---|
| Octop | Octop 内置适配层 + 通用身份 SDK | Octop“外部知识库”中配置 AKB 来源的 Agent 身份 |
| OpenCode | OpenCode 身份插件 + 通用 SDK | 在 OpenCode 全局 plugin 配置中加载插件 |
| 其他 Agent 平台 | 该平台的身份适配器 + 通用 SDK | 在对应平台安装和配置适配器 |

Octop 不加载 OpenCode 插件。Octop 的普通“插件”管理页也不是此身份 SDK 的安装入口。AKB 选择平台是登记身份信任范围，不会安装相应平台的插件。

Octop 页面配置完成后还要把知识源分配给 Agent：点击来源行的“绑定”，选择 Agent 类型，填写 Octop 内部 Agent ID（身份配置抽屉内可复制），不要填写 AKB 主体 ID / 外部 UUID。正常对话执行 `search_knowledge` 检索此来源后，才会产生实际 AKB 请求和身份审计。仅保存配置或完成登记不会自动产生对话检索结果。

“配置 Agent 身份”按钮仅对 `akb-rest` / `akb-mcp` 来源的拥有者和管理员显示。看不到入口时，先检查来源类型、管理权限，以及是否已构建部署该分支的前端静态资源。

## 安装宿主组件

Octop 页面复用 `@akb/agent-identity-sdk` 0.3.2，通过受信宿主的 stdin/stdout IPC 使用同一套密钥存储、登记证明和请求签名。没有额外网络监听端口，没有浏览器私钥，也没有第二套 Python 签名实现。

宿主需要 Node.js 22+。默认 Node 文件密钥 Provider 只支持 POSIX；Windows 需要实现带 ACL 的 Provider，页面会显示组件不可用。

从源码运行：

```sh
cd integrations/agent-identity-sdk
npm ci
npm run build
```

Octop 自动寻找源码目录下的 `integrations/agent-identity-sdk/dist/bridge.js`。安装 Python wheel 的宿主应先在独立目录从本仓库打包 / 安装 SDK，然后配置服务环境：

```sh
# 在 SDK 源码目录生成 akb-agent-identity-sdk-0.3.2.tgz
npm pack
# 在宿主独立目录安装该本地包（尚未发布到 npm registry）
npm install /path/to/akb-agent-identity-sdk-0.3.2.tgz
export OCTOP_AGENT_IDENTITY_SDK_BRIDGE=/absolute/host/node_modules/@akb/agent-identity-sdk/dist/bridge.js
```

`node` 必须在 Octop 服务的 PATH 中。环境变量只接受服务管理员配置，不能由模型参数设置。私钥目录固定为 `$OCTOP_HOME/agent-identity-keys`，由 SDK 创建并验证目录 0700 / 文件 0600；宿主 HOME 和祖先目录不能被其他普通用户写入。密钥不存放在 Agent workspace 中。备份该受保护目录及 Octop 数据库；密钥丢失不会自动生成替代身份。

## 页面操作顺序

1. Octop → **外部知识库**：新建 AKB REST 或 MCP 知识源，服务地址为 AKB origin，例如 `https://akb.example.com`，不要包含 `/api/mcp` 等路径。保存后点盾牌按钮 **配置 Agent 身份**。复制安装实例 ID 和工作区 ID。
2. AKB → **接口服务 → 更多能力 → 凭证 → Agent 身份接入**（AKB ADMIN）：创建应用主体及 V2 策略。平台填写 `octop`，安装实例和工作区使用上一步数值，签发者、受众和资源地址必须与 Octop 一致。为应用授予所需知识库权限，签发有期限的只读共享服务 Key。
3. 将策略 ID、应用主体 ID、签发者、受众和共享 Key 保存到 Octop。可先保留“原有认证”，登记完毕后再切换 V2；V2 启用后未准备好的 Agent 会被拒绝，不会降级到旧认证。关联连接器时在连接器中更新共享 Key。
4. Octop 选择有权管理的 Agent，点 **生成 / 读取公钥**、**导出公钥包**。外部 UUID 由服务端生成并稳定保存；每个 Agent 具有独立密钥。
5. AKB 创建 Agent 绑定，导入公钥包，可自动创建对应 AGENT 主体。为该 Agent 配置知识库授权。AKB 的最终可读范围为应用与 Agent 授权交集。
6. AKB 公钥抽屉 **创建登记挑战**，将公开挑战 JSON 粘贴到 Octop，选择对应 kid，点 **签署登记挑战**。将返回的登记证明交回 AKB **完成登记**。挑战绑定具体策略、Agent、kid、专用用途及短期有效期，由创建挑战的 AKB 管理员完成登记。
7. Octop 点 **验证并选中**。SDK 会向 AKB 身份状态接口发送候选密钥签名，只有远端确认预期 Agent、策略和 ACTIVE kid 后才切换本地活动密钥。随后 **验证当前身份**、**测试检索**。本地“已就绪”不能代替远端验证结果。
8. 重复准备第二个 Agent；共享同一个服务 Key，公钥、外部 UUID 和 AKB Agent 主体不同。正常模型调用从 Octop 宿主运行上下文取 Agent ID，检索业务参数不能选择签名者。管理页面的测试另行检查连接和 Agent 的所有权，审计 session 标记为 `management:<userId>`。

轮换时先准备新密钥并完成挑战登记、验证并选中，再在 AKB 退役旧公钥或撤销。生成轮换候选不会切换活动密钥；AKB 撤销立即阻止继续认证。共享 Key 轮换后需要同步更新 Octop 凭证。

## 运行接口与边界

登录态且拥有知识源 / Agent（或管理员）的请求才能访问 `/api/external-knowledge/sources/{source}/identity`，其子路径包含 `agents/{agent}/prepare`、`proof`、`activate`、`probe`、`search`。所有权检查在服务端执行。响应只含配置、公钥、远端验证状态或检索结果；服务 Key 不回显，私钥始终由 SDK 保管。

通用 SDK 的桥接程序 `akb-agent-identity-bridge` 是受信宿主 IPC，不应作为模型可直接调用的工具，不应暴露 HTTP / MCP 服务。输入通过 stdin 传入，避免凭证进入命令行；错误只返回稳定代码。每次请求启动一次 Node 进程，部署规模增大时可以在相同信任边界内改为常驻受控宿主桥接，但不得允许模型选择 owner / 路径 / 密钥。

这是宿主身份闭环，不是硬件隔离或远程证明。拥有 Octop 服务账号权限、宿主管理员权限或可执行任意宿主代码的进程仍可能读取密钥。SDK 拒绝不安全权限和已丢失的密钥，但无法保护已被攻陷的宿主。安装实例 / 工作区按知识源稳定分配；同一 Agent 在不同知识源中准备的身份彼此独立。HTTP 仅允许显式启用的本机开发模式，生产使用 HTTPS，且源地址省略默认 `:443` / `:80` 端口。

AKB 提供平台无关管理页：OpenCode 和其他平台的公钥包也可以导入，但挑战签名必须回到对应平台 SDK / 插件进行；Octop 页面不会持有其他平台私钥。

## 部署前端

- AKB：`cd frontend && npm ci && npm run typecheck && npm test && npm run build`，部署新的 `dist/` 并重启对应后端。
- Octop：`make build-frontend` 后重新构建 / 安装 wheel 或容器并重启服务；`dashboard/` 是源代码，`src/octop/dashboard/` 是构建产物。

入口：AKB“接口服务 → 更多能力 → 凭证 → Agent 身份接入”；Octop“外部知识库 → AKB 知识源右侧盾牌按钮”。

## 新手接入向导（推荐）

无需手工抄写应用 ID、策略 ID、安装实例或工作区：

1. Octop 知识源的“Agent 身份接入”中点击 **下载 AKB 接入信息**。OpenCode / 其他使用 SDK 的平台可运行 `akb-agent-identity setup export /absolute/path/identity.json > akb-setup.json`（SDK 0.3.2+）。此命令只导出 V2 配置中的公开平台参数，不要求已绑定 Agent，也不会输出服务 Key、私钥或文件路径。配置文件本身仍需满足宿主文件保护要求。
2. AKB 点击 **连接新平台**，选择平台并导入接入信息包。向导会校验地址格式及平台标识，提示缺失字段的来源；为连接填写一个易懂的名字即可新建应用，也可显式选择已有应用。
3. 核对摘要并创建连接。按页面四步指引选择应用可读知识库、添加 Agent 并登记公钥。未授权时页面会明确提示尚不能读取知识。
4. AKB 点击 **下载平台连接配置**，在 Octop 导入，应用 / 策略 ID、签发者和受众自动填入高级参数。不同平台、安装实例、工作区或 AKB 地址的文件会被拒绝；导入不会自动保存、自动更换认证模式或授予权限。另行填写共享服务 Key 后保存。
5. 每个 Agent 完成登记并在 Octop 验证选中密钥后，再启用 V2 并测试检索。文件交接不能代替公钥持有证明或权限校验。

文件格式为 `akb-platform-setup/v1`（平台 → AKB）和 `akb-platform-connection/v1`（AKB → 平台），两者均为公开、限定字段的元数据；不得上传完整平台配置、服务凭证或私钥。
