# 独立 Agent 密钥工具

这是一个可以单独复制、打包和发布的项目，无运行依赖，不需要 Octop、AKB、SDK 或在线服务。仅生成密钥，不转发请求、不提供网络监听。

需要 Node.js 22+。支持 Linux/macOS；Windows 使用系统 whoami/icacls 设置账户独占 ACL，权限设置失败即停止。Windows 实际 ACL 行为需在目标 Windows 主机验收。

```bash
node keygen.mjs generate --out /secure/alpha
# 或在此目录 npm pack，再安装生成的 tgz：
# npm install -g ./akb-agent-keygen-1.0.0.tgz
# agent-keygen generate --out /secure/alpha
```

PowerShell 示例：`node .\keygen.mjs generate --out "$env:USERPROFILE\agent-alpha-keys"`。
输出目录必须不存在，其父目录应可信、不可被其他账户写入，并位于 Agent 可读工作区之外。程序不覆盖任何现有文件。每个 Agent 分别生成一次。

- `identity.json`：复制其中 agentId，到 AKB 和调用平台填写同一 ID。ID 为 UUID，与 Octop 内部 Agent ID 分开。
- `public.jwk`：在 AKB「Agent 接入 → 添加 Agent」导入，并填写名称、Agent ID。
- `private.pem`：仅导入调用平台的受保护存储，不上传 AKB，不发给模型。备份也需限制访问。

Octop：新建 AKB REST/MCP 外部知识库，选择「Agent 密钥」认证，填共享服务 Key，保存后选择系统 Agent、填写 Agent ID、导入 private.pem，点击「保存并测试，自动绑定」。

轮换：使用 `--agent-id <原 UUID>`，输出到新目录，在 AKB 为原 Agent 登记新公钥，在调用平台导入新私钥并测试，成功后在 AKB 撤销旧公钥。不要改 Agent ID。服务 Key 轮换不需要重新生成 Agent 密钥。

输出 RSA-3072 / RS256、公钥 RFC 7638 指纹、PKCS#8 PEM 私钥。私钥为未加密文件，依赖账户访问权限保护；远程页面导入请使用 HTTPS。程序不访问网络，不将私钥写入 stdout/stderr。

测试：`npm test`。打包：`npm pack`。没有创建独立远端仓库或发布到 npm；当前作为独立可打包工程存放在 Octop/tools/agent-keygen。
