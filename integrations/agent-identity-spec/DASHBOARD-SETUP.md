# Agent 身份接入与安全升级

## 日常接入

1. AKB 管理员创建平台连接，选择平台类型、AKB 对外根地址、可读知识库，并保存共享服务 Key。新连接使用 SERVICE_SCOPE：该连接下所有 Agent 继承平台范围。
2. Octop 创建 AKB REST/MCP 外部知识连接，填写同一个根地址和共享服务 Key。
3. 在身份抽屉选择系统 Agent，生成宿主密钥。Octop 调用 SDK bridge，bridge 复用独立本地工具 `@akb/agent-keygen`；只返回公钥、Agent UUID 和受连接作用域约束的密钥引用，私钥不进入浏览器。
4. 在 AKB 为该连接登记 Agent UUID 和 public.jwk。回到 Octop 保存并测试，身份映射与检索挂载在同一数据库事务中完成。
5. 测试检索，随后在对应系统 Agent 的对话中使用知识工具。一个连接可以绑定多名 Agent，但每名 Agent 使用独立密钥和 UUID。

也可以使用 `tools/agent-keygen` 离线生成，并向平台导入 private.pem。平台管理员掌握信任边界；不要把宿主密钥目录挂入 Agent 沙箱。所有平台均可调用同一离线 CLI 或 Node API，无需依赖 Octop UI。

## 历史连接升级：V79

V78 已将旧 V2 的 simple_mode 全部置为 TRUE，因此仅看当前值不能可靠恢复原权限。V79 不修改已执行的 V78 文件：增加独立 authorization_mode，并暂停所有既有未撤销连接，标记 REVIEW_REQUIRED。新建连接不受影响。

升级前备份数据库。升级后管理员逐个核对升级前记录，在「Agent 接入」确认授权方式与理由，再单独启用连接：

- AGENT_INTERSECTION：平台范围与 Agent 现有资源/标签授权取交集。
- SERVICE_SCOPE：所有 Agent 继承平台范围；选择它可能扩大原 Agent 权限，必须明确确认。

确认记录写入管理审计；确认本身不自动启用，不可通过普通状态接口绕过 REVIEW_REQUIRED。重复运行迁移不改变管理员确认结果。无法确认的连接保持暂停，不要猜测原授权。被 V78 删除的旧 V1 数据需从备份调查恢复，不会由 V79 凭空重建。

## 状态、轮换与身份边界

「本地已配置」不是实时远端认证结论。保存测试和管理检索记录最后验证时间与成功/失败；实际请求始终重新检查 AKB 的平台、主体、绑定及公钥状态。AKB 自检按同一启用 Agent 检查其有效公钥，不能把不同 Agent 的状态拼成成功。

解除 Octop 绑定会原子移除身份映射与挂载，保留私钥以便恢复。永久撤销公钥需在 AKB 执行。轮换时使用原 UUID 生成新密钥，登记新公钥，验证新私钥，再撤销旧公钥。共享服务 Key 轮换无需更换 Agent 密钥。

公钥证明的是登记密钥的持有者代表该 Agent 调用；不自动证明背后的自然人。员工委托身份需要另行接入可信用户认证。平台类型是展示元数据，应用主体/连接与公钥绑定才决定可信归属。

## 安装和接口

宿主需要 Node.js 22+。源码构建：

```sh
cd integrations/agent-identity-sdk
npm ci
npm run build
```

SDK TGZ 已包含运行期密钥工具。wheel/容器需安装 SDK TGZ，并设置 `OCTOP_AGENT_IDENTITY_SDK_BRIDGE` 为绝对 bridge.js 路径。根地址使用 HTTPS；显式 HTTP 开关只用于 loopback 开发。

- Octop 状态：GET /api/external-knowledge/sources/{id}/identity
- 宿主生成：POST .../identity/generate
- 绑定/解绑：POST/DELETE .../identity/agents/{nativeId}/connect
- AKB：/api/external/admin/agent-connections
- 历史授权确认：PUT /api/external/admin/agent-connections/{id}/authorization

确认请求示例：`{"authorizationMode":"AGENT_INTERSECTION","platformType":"octop","reason":"根据升级前授权记录核对"}`。

SDK、OpenCode 插件及离线工具可以分别打包，不需要启动 Octop。真实部署仍需验证对应 OS 的密钥权限、反向代理原始路径以及 REST/MCP 请求。
