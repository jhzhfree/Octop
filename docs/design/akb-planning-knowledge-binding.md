# 数字分身规划的 AKB 知识绑定安全设计

版本：重建稿 V0.1，2026-11-14。

**重建说明**：原云工作区丢失后，根据此前代码调查、设计记录和审查结论重新编写。本文不是原文件找回，也不保证与原文件逐字一致。所有新增契约、映射和验收要求均为设计；此前规划功能的实现、测试结果不能据本文认定已在恢复工作区恢复。本文不证明真实 AKB 或模型联调通过。

## 1. 目标与现状边界

目标是在数字分身规划中安全引用岗位获准的 AKB 资料，只返回可编辑草案，不自动执行、保存计划、写记忆或发布知识。

此前调查的代码依据（实施时须在当前基线重新核验）：

- `infra/digital_workers/service.py::set_knowledge_sources` 保存岗位知识授权，包括 `provider / mcp_server / knowledge_base_ref / read_tools`；MCP 来源必须属于同组织、同岗位、同 Agent 的启用场景。
- `infra/knowledge/sources.py::canonical_knowledge_sources` 兼容旧 `akb_knowledge_sources`，规范化时会丢弃未知字段。不能简单添加 `external_source_id` 并假定它进入执行快照。
- `infra/scenes/service.py` 在申请中快照知识能力，审批时通过规范化来源与当前能力比较检查授权变化。直接改变规范化格式可能破坏旧待审批运行。
- `infra/knowledge/external/service.py` 另有 `source_id / connection_id / ref` 模型；`_search_spec` 校验来源、连接和用户来源使用权限，再按认证模式调用。
- `infra/knowledge/external/agent_identity.py` 以宿主 Agent 身份走签名调用链路。
- 此前开发的 `digital_workers/planning.py` 曾实现组织管理者、员工/目标归属、Agent 任命、模型配置及返回前上下文检查。恢复工作区是否已重建该模块，应另行核验。

岗位能力与外部来源是两套绑定，不能凭同名、URL 或相同 KB 编号自动认定等价。现有 `search_knowledge` 会合并 Agent 挂载来源，不能直接用于岗位限定规划，否则可能超出岗位授权。

## 2. 身份语义与既有决策

### 2.1 Agent 签名不等于当前用户授权

必须区分：Agent 身份证明、Octop 当前用户对来源的本地使用权、AKB 当前用户资源 ACL。三者不能相互替代。

- `akb_identity.py::issue_assertion` 可签发包含用户 `sub`、Agent ID、短有效期和 `jti` 的 `X-Octop-Identity`。
- 内置 `akb_mcp.py`、`akb_rest.py` 适配器在获得非 `workload_only` 的 `OctopIdentity` 时才添加用户断言。
- 此前检查的 `execute_as_agent` SDK 调用包含宿主 Agent、连接、会话和 trace，但该调用处未见当前用户 assertion 传递。因此不能认定该路径已实现 AKB 逐用户 ACL。
- 此前检查的 `ExternalKnowledgeService` service 分支清除 `octop_identity` 后调用适配器。底层适配器有签名代码，不代表实际服务路径必然传播用户身份。
- 通用 MCP 静态 header 是连接配置，不是逐请求用户身份注入；不能宣传为与内置 delegated-user assertion 等价。

**启用门槛**：必须实现并证实“当前用户权限与 Agent/岗位权限的交集”。缺少可信用户映射、逐请求传播或 AKB 判权证据时，该知识辅助入口保持不可用，不能退化为仅 Agent ACL、共享服务 Key 或管理员权限。

### 2.2 保持已确定的权限策略

1. Octop 与 AKB 不要求同一个 IdP。身份通过受信的显式绑定接口映射，不通过同名、同邮箱或模型提供的用户 ID 推断。
2. 普通个人 PAT 强制到期，默认只开放 retrievals / SEARCH / QUERY / LIST / READ_OBJECT；服务 Key 单独策略。本入口不扩大 scope。
3. Memory 管理和其他写权限单独申请；沿用既有合并的 Memory 管理权限策略。本设计不创建或继承记忆写权限。
4. 服务身份由 AKB 管理员创建并绑定，默认禁止网页登录，仅供 API/MCP。Octop 组织管理者不等于 AKB 管理员，不能代其创建服务主体或授予 AKB 权限。

## 3. 最小实现范围

首期仅接受显式关联的 AKB Agent 身份来源，并要求第 2 节用户权限交集门槛通过。既有服务身份来源继续其原用途，但本入口不接受，不在 Agent 认证失败时降级。

用户主动选择知识辅助和具体来源。界面披露查询及返回片段将发送到数字员工配置的模型，并说明留存与费用。不得开启自由检索工具，不读取全部知识，不自动访问材料中出现的链接。

## 4. 显式映射契约（拟新增）

新增独立 `worker_knowledge_source_mappings` 表，不改旧能力 JSON 的形状：

| 字段 | 含义 |
|---|---|
| mapping_id | 稳定主键 |
| organization_id、worker_id | 组织、员工归属，由服务端验证 |
| capability_key | 规范化岗位来源的版本化稳定摘要 |
| capability_json | 规范化来源快照，用于展示及完整比较 |
| external_source_id | 显式选定的外部来源 ID |
| expected_connection_id | 批准的连接，禁止静默更换 |
| approved_connection_revision | 批准的连接 revision |
| approved_resource_revision | 批准的来源 resource_revision |
| revision、enabled | 映射乐观锁版本、开关 |
| created_by、updated_by、created_at、updated_at | 审计属性 |

首期唯一约束 `(worker_id, capability_key)`，一个岗位来源映射一个外部来源。摘要格式固定版本，纳入 provider、MCP server、KB ref、selector、read tools、forbidden arguments、access；列表先规范化排序。摘要由服务端计算，不接受客户端或模型的授权摘要。

管理 API 只收岗位来源选择、外部来源 ID 和预期版本，不收 endpoint、凭据、签名身份或任意 ref。服务端验证：

1. 组织管理权限、员工归属和 Agent 任命仍有效。
2. 岗位当前确有此能力，场景授权仍成立。
3. 来源为 `akb-mcp` 或 `akb-rest`，Agent 认证有效，KB ref 精确一致。
4. 当前用户有来源和连接使用权，来源、连接均启用。
5. 外部来源显式挂载于该 Agent；共享来源不自动成为岗位授权。
6. 已有 AKB 用户映射和实际逐用户 ACL 路径满足第 2 节门槛。
7. 检索操作属于岗位批准的 read tools。REST/MCP 操作等价关系由固定服务端适配契约定义，不按工具名称猜只读性。

任何条件不能证明即拒绝。来源管理权限不等于读取全部 AKB 内容的权限。

## 5. 每次规划的鉴权交集与撤权

有效来源为：

`用户选择 ∩ 岗位当前能力 ∩ 有效映射 ∩ Agent 当前挂载 ∩ Octop 用户/连接使用权 ∩ AKB 当前用户 ACL ∩ AKB Agent/应用授权`

AKB 权限不能由 Octop 的 `is_admin`、共享标志或签名成功推断。

规划请求可增加 `knowledge_mapping_ids`、`expected_capability_version` 及映射 revision。ID 只是选择器，不是授权凭证。首期建议最多 3 个来源，并限制查询长度、片段数与总字符预算。

执行步骤：

1. 校验员工、目标、主体；建立能力、映射、来源、连接版本快照。
2. 从当前数据库重新构造 spec，注入受信用户、Agent 和 trace；不接受客户端身份或虚构运行线程。
3. 复用经验证的 Agent 签名及逐用户授权链路进行固定只读检索，不授权模型选择工具或来源。
4. 检索后、送入模型前重查绑定、版本和权限。变化则丢弃片段。
5. 将片段作为不可信材料送入无工具规划模型。
6. 返回前再检查可检测的撤权及上下文变化；变化则不交付旧结果。

发送供应商后的撤权无法撤回已发数据。不能声称上述检查消除了所有并发撤权窗口，必须在发送前满足披露和留存策略要求。

## 6. 结果、证据和提示词注入防护

不要把 `retrieve_context` 的文本/空结果当作唯一状态合约。规划入口需要类型化 `ok / empty / failed`；首期任一选定来源失败则整体失败，不悄悄忽略或改来源。

服务端证据结构：`evidence_id、mapping_id、source_id、knowledge_base_ref、chunk_id、title、retrieved_at、source_version、snippet`。版本仅在上游实际提供时填写，否则 null；不虚构来源。链接仅返回真实、安全协议的证据地址，不主动访问。

模型只能引用本次 evidence_id；逐一校验输出引用，未知 ID 拒绝。模型不能改写服务端证据。片段中的指令、身份声明、工具调用和密钥要求都只是数据，不能修改系统指令、权限、范围或执行策略。

默认不记录原始对话、证据内容、完整提示词、凭据和签名。审计记录主体、来源 ID、版本、状态、时延、数量等必要元数据。生成引用不意味着业务验收或正式知识发布。

## 7. 向后兼容迁移和审批快照

### A. 增量建表

使用 `infra/db/migrations` 的项目机制，分别验证 SQLite/PostgreSQL。默认空映射，不重写 worker 能力、场景快照和历史证据。旧规划不选知识时保持原行为。

迁移助手仅列出候选供有权人员确认；相同 KB、多连接、缺失来源都不能自动选第一个。已有 Agent 来源不能自动授权给岗位。

### B. 先只接规划

旧场景申请、审批和执行不依赖新表，继续原快照与授权检查。新增映射不能给旧运行增加资源。撤销映射阻止后续规划读取，但不能撤回已经生成或发送的数据。

### C. 未来执行端升级（不在首期范围）

若执行器采用新映射，新增明确 `knowledge_binding_schema_version=2` 和独立映射快照，含来源、连接、映射批准版本；不通过改变旧 canonical 来源格式隐式迁移。

- V1 待审批运行继续旧路径，不能自动进入新执行器。切换需重新申请和批准。
- V2 在审批及执行前检查版本和权限，任何变化失败关闭。
- 未知 schema 拒绝，不按最新格式猜测。
- 不重写已完成运行的证据；回滚停用新入口，保留表和审计。
- 取消规划请求不保证供应商不计费，也不能据此宣称总预算受控。

## 8. 失败关闭

| 情况 | 行为 |
|---|---|
| 无映射、歧义、连接/ref/版本变化 | 配置冲突，要求重新确认，不猜测 |
| 跨组织、撤权、禁用、无使用权限 | 拒绝且不返回片段，不泄露敏感存在性 |
| 用户映射/断言/Agent 身份未就绪 | 功能不可用，不降级为服务或管理员身份 |
| 上游失败或超时 | 明确失败，不用旧片段代替 |
| 无结果 | empty；可由用户改问法或主动改为无知识规划 |
| 引用伪造、输出越界 | 拒绝草案，不保存、不执行 |

## 9. 分阶段验收

1. **映射契约**：双数据库迁移、唯一约束、乐观锁；旧请求不变；候选不自动保存；组织管理者不能越权建立映射。
2. **假检索闭环**：Agent 来源多于岗位时只取交集；禁用、换连接、换 ref、撤权均拒绝；失败/无结果区分；无共享凭据回退。
3. **证据/模型**：长度与预算限制、未知引用拒绝、恶意片段不改变权限和系统规则；全部用假模型/假适配器，无真实外发。
4. **真实身份联调**：授权测试环境验证分离 IdP 映射、签名、重放、用户 ACL、Agent ACL、撤权、费用与留存。真实证据齐全后才能启用。
5. **未来执行迁移**：V1/V2 并存、旧运行不增权、版本改变需重新审批、重启和重试不重复副作用。

### 必须通过的双用户与撤权门槛

- A/B 使用同一共享 Agent、同一岗位来源与连接，但在 AKB 仅分别可访问集合 A/B。结果必须各自符合权限，不得取 Agent 或服务 Key 权限并集。
- 撤销 A 的 AKB 权限后，新请求拒绝或排除被撤资源；B 的合法请求不受影响。旧缓存、旧草案和 Agent 较广权限不能恢复 A 的访问。
- 检索后、发送模型前检测到撤权应停止发送；发送后、交付前检测到变化应拒绝交付，同时承认已发数据无法撤回。
- 缺失用户绑定、伪造用户 ID、失效/重放 assertion、Agent 签名有效但用户无权，均失败关闭。
- 通用 MCP 静态身份若不能通过这些测试，入口必须不可用；先补可信逐请求传播，不把验收降为仅 Agent ACL。

上述为待实施和待验收要求；当前没有真实双用户、撤权或分离 IdP 端到端通过的结论。建议先完成显式映射及假检索合约，再实施实际知识增强。

## 10. 当前基线认证边界复核（2026-11-14）

核对 AKB `0fbf45ad762a8182d6217bd024ced55085e36180` 和 Octop
`ca3fa57fd78d174eeec088a100c056b5a4b6ea69` 后，须补充以下实现事实：

1. Octop `external/agent_identity.py::execute_as_agent` 使用 Agent SDK 的 V2
   工作负载签名，不传递当前用户断言。不能仅添加 `X-Octop-Identity` 就宣称完成委托。
2. AKB `ExternalAccessService.authenticate` 的 workload 分支提前返回，但其调用的
   `WorkloadIdentityService.authenticateStored` **已拒绝**同时携带
   `X-Octop-Identity` 或 `Authorization` 的请求；discovery 也拒绝用户断言。
   这不是已证实的“用户断言被静默忽略”漏洞，不应重复修补或者移除此拒绝。
3. 非 workload 的 Agent/API Key + Octop assertion 是另一条已有用户委托路径。
   不能为复用该路径，未经批准就将规划来源切换成服务身份。不同 IdP 仍需可信、显式、
   可撤销的主体绑定；相同邮箱、用户名、Agent ID 或本地管理员标志不是绑定证明。
4. AKB `RetrievalAuthorizationService.effectiveTags` 对非 workload 凭据的
   `CLIENT` 模式返回客户端标签；`SHADOW` 在请求超出授权时只记录并返回请求标签。
   所以知识库 grant 交集、有效签名和存在 delegated principal **都不足以证明内容 ACL**。
   未来启用门槛还须验证服务端 `ENFORCE`、文档/片段过滤、缺少标签授权默认拒绝，
   并覆盖同一 KB 内 A/B 可见内容不同、撤权及缓存隔离。不能将工作负载路径的强制标签
   交集误当作其已具备当前用户身份。

本轮交付是可执行的关闭边界回归，不新增授权开关、检索入口或假身份绑定。
`tests/unit/digital_workers/test_planning_knowledge_boundary.py` 通过实际 ASGI 路由验证：
知识 mapping、身份、凭据、工具和启用字段注入均在模型/SDK/来源调用前被拒绝；
管理员不豁免请求 schema，普通成员仍被组织权限拒绝；对话文本提出读取知识的要求也
不会触发检索。模型为本地假对象，当前用户依赖为测试注入，不代表 JWT、真实 AKB、
跨 IdP 或双用户 ACL 联调通过。未来实现知识规划时须将本回归更新为专用入口的强制
用户授权契约，而不是增加默认 False、可被调用方遗漏的安全标志。
