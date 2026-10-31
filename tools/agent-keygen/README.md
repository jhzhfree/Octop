# 独立 Agent 密钥工具

这是一个可以单独复制、打包和发布的项目，无运行依赖，不需要 Octop、AKB、SDK 或在线服务。仅生成密钥，不转发请求、不提供网络监听。

需要 Node.js 22+（打包成 exe 后不再需要 Node）。支持 Linux/macOS；Windows 使用系统 whoami/icacls 设置账户独占 ACL。程序在写入后会**回读校验**保护是否真的生效，校验失败即删除整个输出目录；权限设置失败即停止。Windows 实际 ACL 行为需在目标 Windows 主机验收。

## 用法

```bash
node keygen.cjs generate --out /secure/alpha
# 或在此目录 npm pack，再安装生成的 tgz：
# npm install -g ./akb-agent-keygen-1.2.0.tgz
# agent-keygen generate --out /secure/alpha
```

PowerShell 示例：`node .\keygen.cjs generate --out "$env:USERPROFILE\agent-alpha-keys"`。

参数：

| 参数                    | 说明                                                   |
| ----------------------- | ------------------------------------------------------ |
| `--out <new-directory>` | 必填。必须是**不存在的**目录，父目录需只有当前账户可写 |
| `--agent-id <UUID>`     | 可选。省略则新生成；轮换时传原 UUID，不要改动          |
| `--help` / `--version`  | 打印用法 / 版本                                        |

成功时 stdout 只输出一行 JSON：`{"agentId":"...","kid":"...","directory":"..."}`；失败时 stderr 输出原因，退出码为 `1`。私钥内容永不打印。

## 打包成 exe（Windows）

```powershell
npm install          # 仅构建期依赖 postject，运行期仍无依赖
npm run build:exe    # 产出 dist\agent-keygen.exe
.\dist\agent-keygen.exe generate --out "$env:USERPROFILE\agent-alpha-keys"
```

产物是把 Node 运行时与本工具封装在一起的单文件可执行程序，用法与 `node keygen.cjs` 完全一致。注意：

- exe 仍然调用系统的 `icacls.exe` / `whoami.exe`，这两者是 Windows 自带组件
- 未签名时 Windows SmartScreen 可能在首次运行时告警；对外分发请用 `signtool` 签名
- 在 Linux/macOS 上执行同一脚本产出 `dist/agent-keygen`（无扩展名）

## 安全前提

- 输出目录必须不存在，程序**不覆盖任何现有文件**；父目录应可信、不可被其他账户写入，并位于 Agent 可读工作区之外
- POSIX 使用 0700 目录 / 0600 文件；Windows 移除继承并只授予当前账户 SID，SYSTEM 与 Administrators 因提权能力视同 POSIX 的 root
- 每个 Agent 分别生成一次。私钥为未加密文件，依赖账户访问权限保护；远程页面导入请使用 HTTPS，备份也需限制访问

## 产出与去向

- `identity.json`：复制其中 agentId，到 AKB 和调用平台填写同一 ID。ID 为 UUID，与 Octop 内部 Agent ID 分开
- `public.jwk`：在 AKB「Agent 接入 → 添加 Agent」导入，并填写名称、Agent ID
- `private.pem`：仅导入调用平台的受保护存储，不上传 AKB，不发给模型

Octop：新建 AKB REST/MCP 外部知识库，选择「Agent 密钥」认证，填共享服务 Key，保存后选择系统 Agent、填写 Agent ID、导入 `private.pem`，点击「保存并测试，自动绑定」。

轮换：使用 `--agent-id <原 UUID>`，输出到新目录，在 AKB 为原 Agent 登记新公钥，在调用平台导入新私钥并测试，成功后在 AKB 撤销旧公钥。不要改 Agent ID。服务 Key 轮换不需要重新生成 Agent 密钥。

输出 RSA-3072 / RS256、公钥 RFC 7638 指纹、PKCS#8 PEM 私钥。

测试：`npm test`。打包：`npm pack`。没有创建独立远端仓库或发布到 npm；当前作为独立可打包工程存放在 Octop/tools/agent-keygen。

## 平台插件的 Node API

工具提供稳定的包入口和 TypeScript 类型；导入模块不会执行 CLI，不打印日志、不联网。

```js
import { generateToDirectory } from '@akb/agent-keygen';
const generated = await generateToDirectory({
  directory: '/secure/platform/alpha', // 必须不存在，放在 Agent 工作区之外
  // agentId: '原 UUID',              // 轮换时保留
});
// generated: agentId、kid、directory、publicJwk；不包含私钥内容。
// 平台在受信管理流程中导入该目录的 private.pem，并向 AKB 登记 public.jwk。
```

SDK 内部也可调用 `generateKeyMaterial(agentId?)`，获得宿主内存中的私钥 KeyObject 与 publicJwk，再交给自身的受保护存储。这是宿主权限接口，不能直接注册成提供给模型的工具。

OpenCode 插件开发者可以在本机初始化/管理命令调用 CLI，或直接导入以上 API；不要在每次聊天、模型工具调用或插件自动加载时生成新身份。CLI 成功 stdout 仍只有 agentId、kid、directory，不含私钥。生成完成后通过 SDK 导入或初始化注册流程使用密钥，不能靠生成密钥自动取得 AKB 权限。

Octop 的宿主生成流程使用同一个包，浏览器只拿到公钥与作用域引用。浏览器不再自行生成并下载含私钥的 zip。密钥引用不允许跨知识连接或跨 Agent 使用。
