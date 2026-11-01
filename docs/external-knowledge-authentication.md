# 外部知识库认证类型

外部知识库的通信协议与认证类型分别配置。创建、修改来源时使用 `auth_type` 字段：

| 协议 `kind` | `service`（服务凭证） | `agent`（Agent 认证） |
| --- | --- | --- |
| `akb-mcp` | 使用服务凭证连接并调用工具，无需绑定 Agent 身份 | 服务 Key 可连接、获取工具列表；执行工具必须绑定 Agent 并签名 |
| `akb-rest` | 使用服务凭证直接调用 REST | 绑定 Agent 后逐次签名 REST 请求 |
| `ragflow`、`weknora`、`standard-retrieval` | 使用对应产品的服务凭证 | 当前不支持，API 拒绝该组合 |

默认使用 `service`。AKB 中必须签发匹配的凭证：**服务凭证**可直接调用；**Agent 凭证**中的服务 Key 只能发现 MCP 工具，不能通过把 Octop 的认证类型改成 `service` 绕过服务端验签。

Agent 登记仍使用 AKB 原有的 UUID、公钥登记流程。Octop 选择 `agent` 后，在“绑定 Agent / 认证”中选择本地 Agent、填写登记 UUID、导入对应私钥。业务调用使用 Octop 内置 Python 实现的现有 V2 签名认证，服务 Key、请求方法、路径、正文、时效和防重放参数均参与验证。工具参数中的 Agent 名称、UUID 不能替代签名身份。

`akb-mcp` 的连接测试只执行 `initialize` 和 `tools/list`（Agent 模式不自动调用 `list_knowledge_bases`）。连接成功不代表业务调用已授权。列出知识库、检索和其他工具调用都必须使用已绑定 Agent 的身份。未绑定时会报身份未就绪，不会回退为无签名请求。

服务凭证来源可使用普通的来源绑定来配置哪些 Agent 或本地知识库使用它；这种使用范围绑定不要求导入 Agent 私钥。服务凭证调用不附加 Agent 身份断言。旧的已保存来源中，包含 `agent_identity.simple` 的配置继续按 `agent` 读取，明确没有身份的普通配置按 `service` 读取；无法识别的非空身份配置保留为待处理，不会静默降级。认证类型保存在独立连接配置中，身份映射通过迁移 030 移入独立表；关联连接器的来源仍使用连接器服务 Key，并保留来源自己的认证类型。

已有 Agent 身份绑定时禁止修改认证类型。先解绑全部 Agent，再修改。修改服务 Key 会保留现有 Agent 登记关系；并发修改使用凭证版本比较，避免恢复已删除的绑定。

## 配置与分阶段排查

页面分为“服务连接”和“知识库资源”。先创建连接并填写服务 Key（AKB 协议必填），再选择认证类型。连接级 Agent 身份不自动挂载知识库。

- `POST /api/external-knowledge/connections/{id}/probe`：只验证地址、协议与服务凭证，不执行工具检索、不假设 Agent 身份。MCP 使用 initialize/tools/list；REST 使用 AKB `GET /api/v1/external/connection`。需要同步部署支持该端点的 AKB。
- 连接详情中的 Agent 认证：登记 UUID、公钥，导入或生成对应私钥后验证并保存身份。只有此阶段成功才代表 Agent 身份有效。
- 在“知识库资源”选择已有连接，通过“拉取列表”加载 `KB_...` ID；首次资源保存前即可加载目录。Agent 模式需选择已验证身份。资源检索测试必须填写非空问题；检索通过后配置 Agent 使用范围。

连接失败按凭证、地址、权限、协议、限流或超时展示下一步操作；成功状态不会显示错误通用提示。连接检查按配置版本保存，修改连接后原结果失效。

出现 AKB“缺少 API Key”时，先检查连接是否已保存服务 Key。服务凭证可选择 `X-API-Key` 或 `Authorization: Bearer`；Agent 请求必须转发 `X-API-Key` 和 `X-AKB-Workload-Assertion`。网关移除这些头时，AKB 仍会拒绝请求。控制台登录 Cookie 不等于服务凭证。空问题也应在检索前补全，不能用连接或身份检查替代实际资源检索。

## 本地验证

1. 在 AKB 的 **Agent 凭证 → 服务 Key** 中选择多个可查询知识库，保存并重新打开确认；登记 Agent 的步骤保持不变。
2. 在 Octop 创建 `akb-mcp` / `agent` 来源，填入该服务 Key。保存后测试连接，应能看到工具列表。
3. 未绑定 Agent 时执行检索，应失败；绑定正确 UUID 与私钥后检索应成功，并受 AKB 选择的知识库范围约束。
4. 改用另一私钥或撤销 Agent，再检索应失败；停用 AKB 凭证后，连接测试也应失败。
5. 新建 `akb-mcp` / `service` 来源，填写 AKB **服务凭证**中的 Key，无需导入私钥即可列出知识库和检索；知识库授权与工具 scope 仍由 AKB 校验。

完整的连接复用、产品界面、升级回滚与验收规则见 [产品升级说明](external-knowledge-product-upgrade.md)。

连接身份与资源挂载的独立管理、密钥恢复/轮换、移除身份的影响范围，以及迁移 031 的检查版本规则，见 [产品升级说明](external-knowledge-product-upgrade.md#连接身份与资源检查改进)。
