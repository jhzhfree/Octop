# 身份 SDK 与外部插件的交付边界

OpenCode 的 AKB MCP 身份插件制作、打包、安装和宿主适配代码已迁出 Octop，由[opencode-identity 外部工程](https://github.com/jhzhfree/opencode-identity)维护。Octop 不再保留或构建该插件，不再交付插件 TGZ。

Octop 内保留：

- `packages/akb-identity-sdk`：Python 身份认证 SDK，Octop 原生调用链路使用，支持独立 wheel/sdist 交付。
- `integrations/agent-identity-sdk`：通用 Node SDK、终端密钥接口与可选的受信宿主 bridge，保留独立测试和 TGZ 交付。
- `tools/agent-keygen`：Node SDK 依赖的通用离线密钥工具，不是插件制作器。
- `integrations/agent-identity-spec`：协议、公共向量、平台无关接入契约及 SDK 安装冒烟测试。

Octop 的外部知识库连接、Agent UUID/密钥配置、加密存储、逐请求签名、AKB REST/MCP 调用及管理界面继续使用。删除外部插件源码不会重新生成身份，也不改变已有绑定、密钥或知识库授权。

Node SDK 包可通过 `node test-packages.mjs <SDK绝对路径.tgz>` 在无相邻源码的临时宿主中验收；Python SDK 的构建、部署依赖和独立示例见 [SDK 文档](../../packages/akb-identity-sdk/README.md)。公共协议向量仍通过 `node verify-design.mjs` 验证。
