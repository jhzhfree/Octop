# 简化 Agent 接入第一版

## 日常操作

1. 独立工具 `tools/agent-keygen` 为每个 Agent 生成密钥对。
2. AKB：接口服务 → 凭证 → Agent 身份接入 → 新建服务接入。填名称、AKB 对外根地址、可读知识库，保存共享服务 Key。
3. AKB：添加 Agent，填名称、工具生成的 UUID，导入 public.jwk。同一个服务接入可以登记多名 Agent。
4. Octop：外部知识库 → 新建 AKB REST/MCP 连接 → 认证方式选 Agent 密钥，填 AKB 地址和共享服务 Key，保存。
5. Octop 自动打开绑定抽屉：选择 Octop 系统 Agent，填同一个 AKB UUID，导入 private.pem，点「保存并测试，自动绑定」。不再单独点知识库绑定。
6. 选中已绑定 Agent，输入问题点「测试检索」；之后用该系统 Agent 聊天触发知识库检索。只保存配置或登记公钥不会自动执行搜索。

私钥导入本平台宿主受保护存储，不发送 AKB、不进入模型参数。远程管理请使用 HTTPS。一个 Agent 的 AKB UUID 与 Octop 内部 Agent ID 是两个标识，页面通过选择器自动关联。不同 Octop Agent 不允许绑定同一个 AKB UUID。

认证方式切回「原有认证」不会把严格共享 Key 变成普通 Key：还需在连接中替换成原有认证对应的普通凭证。密钥认证失败不自动降级。

## 权限与状态

同一服务接入下所有 Agent 继承共享可读范围，第一版不再单独配置每个 Agent 的知识库策略。默认只读所选库的无权限标签内容；带标签数据仍按服务侧授权约束，不会自动开放。

“已验证并选中”表示本地密钥上次完成验证，不代表 AKB 之后一直未停用。点击保存并测试或实际调用重新检查服务 / Agent / 公钥有效状态。

- 解除 Octop 绑定：停止向该 Agent 提供知识源，受保护文件保留以便恢复。永久撤销需在 AKB 撤销公钥。
- 更换 Agent 密钥：独立工具保留原 UUID 生成新密钥，AKB 为原 Agent 登记新公钥，Octop 导入新私钥测试，最后撤销旧公钥。
- 轮换共享 Key：AKB 轮换后立即替换 Octop 连接的服务 Key；无需更换 Agent 私钥。
- 已有高级接入保持原有应用与 Agent 权限交集，不自动迁移或扩大权限；AKB 页面底部有高级兼容入口。

## 宿主部署

新功能需要 AKB 后端数据库迁移 V76、AKB 前端、Octop 后端与前端同时更新。仅拉代码不会更新已部署页面。

Octop 宿主安装 Node.js 22+，构建 SDK 0.4.0：

```sh
cd integrations/agent-identity-sdk
npm ci
npm run build
```

源码运行时自动查找 `dist/bridge.js`。wheel/容器部署须安装 SDK TGZ，并将 `OCTOP_AGENT_IDENTITY_SDK_BRIDGE` 设置为宿主可读的绝对 `dist/bridge.js` 路径，重启 Octop。私钥位于 `$OCTOP_HOME/agent-identity-keys`，POSIX 0700/0600，Windows 使用账户独占 ACL，所有祖先目录都须可信。该目录不能暴露给 Agent 工作区或模型工具。

AKB 根地址须使用 HTTPS；当前 SDK 的 HTTP 开关仅用于显式允许的本机 loopback 测试。普通认证支持的内网明文 HTTP 不代表 Agent 签名模式也支持。生产跨机部署使用 HTTPS。

前端构建：AKB `cd frontend && npm ci && npm run build`；Octop `make build-frontend`，按各自部署方式发布产物并重启后端。勿直接编辑 Octop 的 `src/octop/dashboard` 构建目录。

## 不同平台

Octop 使用内置适配，不加载 OpenCode 插件。OpenCode 使用独立适配器，见 `integrations/opencode-identity/README.md` 的 configVersion 3。其他平台只需实现：可信运行上下文 → 本地 Agent/UUID 绑定 → 私钥签名 → MCP/REST。密钥生成工具与平台无关。

接口：Octop 管理 `/api/external-knowledge/sources/{id}/identity/simple`、`/agents/{nativeId}/connect`；AKB 管理 `/api/external/admin/agent-connections`。AKB 的公开配置发现不认证 Agent，只返回签名坐标；知识请求仍必须通过严格签名、短期有效期、防重放和服务可读范围检查。

## 本地验收记录

本版已通过 AKB 223 项 Java 测试、Octop `make all`（4764 通过 / 16 跳过）、SDK 与 OpenCode 插件测试和独立 TGZ 安装验证。浏览器完成服务接入创建、双 Agent 公钥登记、私钥导入与自动绑定。真实本地 AKB / Octop 联调中，REST 和 MCP 分别以两名 Agent 检索到非空内容；越权、重放、跨 Agent 冒充、停用和撤销按预期拒绝。

上述结果来自隔离的本地测试环境，并不表示用户线上部署已更新；Windows 账户 ACL 仍需在目标主机验收。
