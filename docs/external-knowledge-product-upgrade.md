# 外部知识库：连接、资源、身份与升级验收

## 产品边界

外部知识库由四个对象组成：服务连接、外部知识库资源、使用范围（挂载）、Agent 身份绑定。服务连接保存地址、产品/协议、认证类型和服务凭证；资源保存外部库编码及检索配置；挂载决定谁可以使用；身份绑定决定以哪个已登记的 Agent 身份调用。

支持矩阵：AKB MCP/REST 支持服务凭证和 Agent 认证；RAGFlow、WeKnora、标准检索 HTTP 使用服务凭证。AKB MCP 知识源只允许 `knowledge_search`、`knowledge_query`，任意工具调用使用通用连接器。历史自定义工具配置会被标记为待处理，不会自动改成另一个工具执行。

认证只改变授权材料。两种 AKB 认证共用协议适配器、请求参数和结果解析。Agent 模式由 Octop 内置 Python 签名，不再要求安装 Node 或 SDK bridge；终端/OpenCode SDK 不变。每次 HTTP 请求和重试使用新的 nonce，签名覆盖实际发送的方法、路径、正文及服务凭证摘要。身份来自服务端运行上下文，模型参数不能选择签名人。

## 管理流程

1. 在“服务连接”新建连接，选择产品、地址、认证类型和服务 Key；连接可以先保存，再添加知识库。
2. 添加外部知识库时选择已有连接、填写库编码和检索设置。同一连接下多个库复用服务 Key 和 Agent 身份。
3. Agent 模式在身份抽屉选择本地 Agent，填写已在 AKB 登记的 UUID，生成或导入私钥并校验。首次绑定必须提供私钥或本人生成的引用，不能按 UUID 借用宿主机已有私钥。
4. 获取 Agent 模式的知识库目录前显式选择 Agent；不同 Agent 的可读范围可能不同，界面不再默认取第一个身份。
5. 通过检索验证后配置使用范围。服务凭证无需 Agent 身份，但来源使用权与挂载目标管理权仍然必须满足。

共享连接使用者可以绑定自己的 Agent，不能修改连接服务 Key 或管理他人的 Agent。引用私有连接器、设置挂载和实际执行均有权限检查。撤销共享、停用连接、撤销挂载后，新调用不会因旧挂载继续获得权限。

检查结果按连接版本、资源和 Agent 分开记录。轮换凭证后旧检查结果不再表示当前版本已通过；连接发现成功也不代表当前 Agent 可以读取当前库。多来源返回 `ok`、`empty`、`partial`、`failed`；全部失败不能当作无命中。部分失败保留成功片段，并告知覆盖不完整。

## 数据与 API

- `external_knowledge_connections`：共享连接及配置版本；服务 Key 继续使用既有凭证加密机制。
- `external_knowledge_sources`：兼容原来的来源 ID，作为资源表，关联 `connection_id`，有独立 `resource_revision`。
- `external_knowledge_bindings`：资源到本地 Agent/知识库的使用范围。
- `external_agent_bindings`：连接级身份，独立密钥引用和验证状态。
- `external_connection_checks`：按配置版本记录检查阶段、状态、错误码；运行状态不再重写整块加密凭证。
- 新密钥加密保存在既有 secrets 存储中，绑定通过外键持有引用。解绑或删除连接只回收没有其他绑定引用的密钥。

新增接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET / POST | `/api/external-knowledge/connections` | 列出、新建连接；响应不包含 Key 或私钥 |
| PATCH | `/api/external-knowledge/connections/{connection_id}` | 带 revision 更新、轮换 Key、启停或调整共享 |
| DELETE | `/api/external-knowledge/connections/{connection_id}` | 删除已无资源的连接 |
| GET / POST | `/api/external-knowledge/resources` | 列出资源，或给已有连接添加资源 |

原 `/sources`、`/sources/{id}`、绑定和身份接口继续兼容。资源修改、删除仍可使用原接口。资源及连接编辑携带对应 revision，过期版本返回冲突。删除资源不会顺带删除仍可复用的连接；删除连接前先移除资源。

## 网关部署

Octop 的连接地址允许部署前缀，例如 `https://kb.example/gateway`；也接受包含已知 API 路径的地址，并规范化一次。认证与服务模式使用同一条路径。

若网关把 `/gateway/api/mcp` 重写为后端的 `/api/mcp`，在 AKB 配置：

```yaml
knowledge:
  external:
    public-path-prefix: /gateway
```

这是管理员配置的外部签名路径前缀。AKB 不信任调用者传来的 `X-Forwarded-Prefix` 来决定验签目标。没有路径重写时保持空值；该配置实例的其他签名客户端也必须使用相同的公开路径。普通转发头不能绕过路径、正文或凭证绑定。

AKB REST 的 `/kb/retrievals` 在 Agent 模式要求签名正文包含 `knowledgeBaseId`；请求头与正文同时存在时必须指向同一库。Octop 两种认证都发送该正文标识，防止仅修改未签名选库头而改变目标。

## 升级与回滚

1. 升级前停止写入并备份完整数据库和旧 `agent-identity-keys` 目录。备份必须包括 secrets 表中的凭证加密材料。
2. 启动新版本执行 SQLite/PostgreSQL 配套迁移 030。原来源 ID 不变；既有来源各自映射到连接，不自动合并不同连接或身份。
3. 旧加密配置在首次读取时迁移：明确的 Agent 简化配置保留 Agent 模式，普通配置保留服务模式。身份映射移到独立表，Key 保持加密；重复读取不会反复迁移。
4. 无法识别的旧身份或损坏凭证显示待处理，不会静默降级为服务模式。管理员在连接页显式选择认证并更换不可读凭证；Agent 模式随后重新绑定验证。
5. 旧 Node 密钥库的活动密钥按需导入加密存储，并固定到身份引用。POSIX 检查文件所有者及私有权限；Windows 旧文件迁移用系统 PowerShell 检查 ACL。新密钥生成和业务调用不依赖 PowerShell 或 Node。未完成的临时生成流程可以重新生成。
6. 迁移成功后先验证，再处理旧密钥目录。程序不会自动销毁它，便于核查和回滚；它仍是敏感备份，应按原有权限保护。
7. 回滚应停止服务、恢复升级前的完整数据库及旧密钥目录，再启动对应旧程序。不要只回退程序而继续使用已迁移的数据库；保留升级后的快照，以便重新升级和核对期间新增数据。

新部署的 Python wheel 包含原生身份代码与迁移文件，不需要另装 integrations 目录。Windows 实机应在正式发布前运行对应验收；Linux 测试和 Windows 兼容代码检查不能代替实机验证。

## 运行预算和来源追踪

一次聚合最多 16 个来源、同时最多 4 个检索；排队、重试与多次协议请求共享来源的总截止时间。聚合复用 HTTP 连接池。只读请求仅对限流和上游服务错误作一次受预算限制的重试，且每次重新签名。响应流在超过 2MB 时停止读取。

片段按资源命名空间去重，不同知识库相同的 chunk ID 不会合并。结果携带 source_id、库编码和结构化引用；外部文档 URL 只接受无用户凭证的 HTTP/HTTPS 链接，没有外部 URL 时回到知识库管理页，不伪造本地文档链接。

## 验收入口

```bash
uv run pytest tests/unit/knowledge tests/unit/db/test_repo_external_knowledge.py tests/unit/db/test_db_pool.py tests/unit/i18n -q
make all
cd dashboard
npm test -- --run src/pages/ExternalKnowledge src/utils/parseKnowledgeCitations.test.ts src/utils/knowledgeCitationDisplay.test.ts
npm run build
```

AKB 运行 `backend` 的 Maven 测试。新增测试覆盖 Python 签名样本的 Java 验签、管理员网关前缀、正文选库约束及篡改拒绝。

重点人工验收：两种认证的参数一致；错误/撤销密钥不能调用；共享使用者只能绑定自己的 Agent；撤销共享立即拒绝新调用；同一连接多库复用与轮换一致；多来源部分失败可见；未知旧配置保持待处理；安装包内无 Node 时仍可进行原生身份操作。
