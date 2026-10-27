# 外部知识库对接模块设计方案

> 目标：在 Octop 知识库模块中引入**外部知识库源（External Knowledge Source）**管理能力，可连接第三方知识库（首个目标：AKB）并获取检索结果，与本地知识库协同参与 Agent 检索。
>
> 依据：`AGENTS.md` 模块边界、`docs/development-standards.md`、AKB `feature/octop-mcp-integration`（`docs/api/OCTOP-INTEGRATION.md`、`docs/api/external_kb_search_protocol.md`、`backend/.../McpServerService.java`）。
>
> 状态：**已实施（决策点按推荐值落地）**。
>
> 落地选择：A1 复用连接器凭据加密（源表自带 `credential_blob`，可关联 `instance_id`）/ B 新建迁移 028 / C RRF 融合 / D 提供「允许内网 HTTP」开关且默认关闭 / E AKB 优先走 MCP（REST 作为备选）/ F 一期不含 SSE 与 READ_OBJECT / G 一期支持绑定 Agent 与本地知识库。
>
> 实现位置：后端 `src/octop/infra/knowledge/external/`（types / http / normalize / fuse / registry / service / adapters）与 `src/octop/api/routers/external_knowledge.py`；仓储 `src/octop/infra/db/repos/external_knowledge.py`；迁移 `028_external_knowledge.sql`（含 `.pg.sql`）；前端 `dashboard/src/pages/ExternalKnowledge/`。

## 1. 现状调研结论

### 1.1 Octop 本地知识库（已存在）

| 层 | 位置 | 说明 |
|---|---|---|
| 表 | `knowledge_bases` / `knowledge_base_members` / `knowledge_documents`（迁移 005） | 公共字符串 id + 整数 PK |
| 领域 | `infra/knowledge/service.py`（`KnowledgeService`）、`index.py`（`KnowledgeIndex` 本地向量索引）、`embed.py`、`parse.py`、`chunk.py`、`ocr.py` | 本地 RAG 全链路 |
| 检索 | `infra/knowledge/retrieve.py::retrieve_context()` | 每轮对话把命中片段拼成带引用的上下文（默认 k=8、字符预算 6000），失败静默降级为空 |
| 工具 | `infra/knowledge/tools.py::build_knowledge_tools()` | 内置 LangChain 工具 `search_knowledge`（k≤20），描述里列出本轮挂载的知识库 |
| API | `api/routers/knowledge_bases.py` | `/capability`、`/embedding-options`、`/feature`、KB CRUD、文档上传/预览/重索引等 |
| 前端 | `dashboard/src/pages/KnowledgeBases/`、`dashboard/src/api/modules/knowledgeBases.ts` | 列表、文档管理、文件夹、文本编辑 |

### 1.2 已存在的"外部知识库"能力（重要：不要重复造轮子）

1. **连接器网关适配器体系**：`infra/connectors/gateway/registry.py` 定义 `GatewayAdapter` 协议（`list_tools` / `call_tool` / `probe_credentials`），已注册 16 个适配器，其中 `weknora`、`ragflow` 的 catalog `category = "knowledge"`。
   - `adapters/ragflow.py`：只读检索，`POST {base}/retrieval`（`Authorization: Bearer`），工具 `retrieve`，探测走 `GET /datasets`，输出 `{content, dataset_id, document_id, document_name, similarity, truncated}`。
2. **AKB REST 客户端**：`infra/knowledge/akb_client.py`（`AkbClient.search/query` → `/api/v1/external/search|query`；`AkbMemoryClient.capture`）。**当前只被 `infra/digital_workers/memory.py` 使用**（记忆捕获），**未接入知识库检索链路**。强制 HTTPS origin、禁止重定向、响应体限长、错误信息脱敏。
3. **数字员工知识源绑定**：`infra/knowledge/sources.py`（`canonical_knowledge_sources`：provider / mcp_server / knowledge_base_ref / read_tools / forbidden_arguments / selector_argument）+ `infra/agents/middleware/knowledge_source_scope.py`（MCP 工具调用前强制范围校验，拒绝越权 selector）。
4. **凭据加密**：`infra/connectors/crypto.py`（Fernet + `secrets` 表），连接器实例 CRUD 与 `/connector-instances/{id}/test` 探测已具备。
5. **AKB MCP preset**：提交 `ba82e567 feat(connectors): add AKB MCP preset`（在 custom MCP 体系中）。

### 1.3 缺口（本方案要解决的）

- 没有统一的**外部知识库管理面**：列表 / 新建 / 探测 / 检索测试 / 启用停用 / 绑定。
- 外部检索**未进入本地检索链路**：`retrieve_context()` 只查本地 `KnowledgeIndex`，`search_knowledge` 工具也只覆盖本地库。
- 缺少**跨源结果归一化**（不同源字段各异：RAGFlow `similarity`、AKB `score`/`citations`、WeKnora 自有结构）与**融合排序**契约。
- 缺少**失败降级契约**（单源失败不影响整体，全失败才报错）。

## 2. AKB 侧接口（feature/octop-mcp-integration）

### 2.1 MCP（首选接入方式）

| 项 | 值 |
|---|---|
| 端点 | `POST https://<host>/api/mcp` |
| 传输 | MCP JSON-RPC over **stateless** Streamable HTTP：POST JSON；**无 session、无 SSE 流**；`GET` 返回 405；`notifications/*`（无 id）返回 202 + 空体 |
| 认证 | `X-API-Key: <key>` |
| 协议版本 | 文档声明协商 `2025-06-18`；代码常量 `PROTOCOL_VERSION = "2024-11-05"`（**存在出入，客户端应按服务端 initialize 返回值协商，不要硬编码**） |
| 工具（按 scope 过滤） | `knowledge_query`（QUERY）、`knowledge_search`（SEARCH）、`list_knowledge_bases`（LIST）、`list_knowledge_cards` / `get_knowledge_card` / `list_claims` / `get_claim` / `get_evidence` / `graph_neighbors`（READ_OBJECT）、`memory_recall` / `memory_capture` / `memory_forget`（MEMORY_*）、`ingest_text`（INGEST） |
| 关键参数 | `knowledgeBaseCode`（稳定编码，推荐）/ `knowledgeBaseId`（旧兼容，绑定 Key 可省略）、`query` 或 `question`、`topK`（默认 10）、`permissionTags`、`minScore`、`answerFormat`（default/markdown/text/html） |
| 返回 | `knowledge_query` 给答案 + `queryId` + `citations`；`knowledge_search` 给候选/重排/引用与 `returned` 条数 |

> `tools/list` 按授权过滤，服务端每次调用复核 scope；`permissionTags` 只是过滤条件，**不能代替 AKB 侧 ACL**。

### 2.2 REST v1（版本化外部 API）

- `POST /api/v1/external/search`、`POST /api/v1/external/query`：请求 `{knowledgeBaseId, question, topK, permissionTags}`，信封 `{success, data, traceId}`，`X-API-Key` 头。
- `POST /api/v1/external/kb/retrievals`：**standard 检索协议**实现，`Authorization: Bearer <key>` + `X-Knowledge-Base-Id` 头，body `{question, topK}`，响应 `{chunks:[{id, content, score, source, metadata}]}`；`score ∈ [0,1]` 且越大越相关、按降序；`id` 跨请求稳定（AKB 用 `EVD_…` 证据码）；空结果 200 + `{"chunks":[]}`；错误码 `INVALID_ARGUMENT/UNAUTHORIZED/FORBIDDEN/KB_NOT_FOUND/RESOURCE_EXHAUSTED/INTERNAL`。
- `POST /api/v1/external/memories/capture`：写入记忆（Octop 已在用）。

### 2.3 SSE（本期不接入，列为二期）

- `GET /api/v1/external/stream/query`：流式问答（stage / delta / citation / done）。
- `GET /api/v1/external/stream/events`：事件订阅（types 过滤、`Last-Event-ID` 续传）。

AKB MCP 是 **stateless POST**，不存在服务端推送通道；SSE 属于另一套外部 API 能力。一期保持"请求-响应"检索，不引入长连接。

### 2.4 已知风险（来自 AKB 自查报告）

- `McpController` 对业务失败仍记 HTTP 200 审计成功 → Octop 侧**不能只看 HTTP 状态码**，必须解析 JSON-RPC `error` 并在本地区分「传输成功 / RPC 失败 / 业务拒绝」。
- 传输自称 Streamable HTTP 但仅实现单 POST → 客户端不要依赖会话、GET 或 SSE。

## 3. 总体设计

### 3.1 分层与落点（遵守 AGENTS.md 边界）

```
dashboard/ ──HTTP──► api/routers/external_knowledge.py ──► infra/knowledge/external/*
                                                            ├─ registry.py    源注册与解析（kind → adapter）
                                                            ├─ adapters/       akb_mcp.py / akb_rest.py / ragflow.py / weknora.py / standard_retrieval.py
                                                            ├─ normalize.py   跨源 chunk 归一化 + 去重 + 预算裁剪
                                                            ├─ fuse.py        本地 vs 外部 融合排序（RRF）
                                                            └─ service.py     管理门面（CRUD / probe / search / retrieve）
infra/knowledge/retrieve.py  ← 扩展：本地 + 外部融合入口（仍只被 api/、gateway/、tools 调用）
```

- 新增目录 `src/octop/infra/knowledge/external/`（领域层，可被 `api/`、`infra/agents/` 调用；不得反向依赖 `api/`、`cli/`）。
- 新增路由文件 `src/octop/api/routers/external_knowledge.py`（薄：校验 + 调 `infra/` + 错误映射）。
- `infra/knowledge/akb_client.py` 保留并复用（它是领域内的 HTTP 适配器），新的 AKB MCP 适配逻辑放 `infra/knowledge/external/adapters/akb_mcp.py`。

### 3.2 统一检索契约（核心）

所有外部源在适配层统一为**标准 chunk**（对齐 AKB standard 协议，RAGFlow/WeKnora 做字段映射）：

```python
@dataclass(frozen=True)
class ExternalChunk:
    id: str              # 跨请求稳定；缺失时用内容摘要兜底
    content: str         # 非空正文
    score: float         # [0,1]，越大越相关（距离型必须转换）
    source: str          # 文档名/路径/链接，用于展示与溯源
    metadata: dict       # 原样透传（docName/page/evidenceCode/sourceUri/…）
    origin: str          # "akb" | "ragflow" | "weknora" | "standard"
    ref: str             # 命中的知识库 ref（code 或 id）
```

检索结果统一为：

```python
{ "chunks": [...], "citations": [...], "warnings": [{"origin": "akb", "code": "TIMEOUT", "message": "..."}] }
```

**降级契约**（沿用 AKB 协议 §4）：单源失败（超时 / 4xx / 5xx / 协议错误 / 未配置）→ 该源记入 `warnings` 并返回空结果，**不中断整体检索**；全部源失败 → 整体返回错误提示。

### 3.3 适配器与协议矩阵

| kind | 传输 | 端点 | 认证 | 探测方式 |
|---|---|---|---|---|
| `akb-mcp`（新增，首选） | MCP JSON-RPC / stateless POST | `/api/mcp` | `X-API-Key` | `initialize` + `tools/list`（校验 `knowledge_search`/`knowledge_query` 可见） |
| `akb-rest`（新增） | REST | `/api/v1/external/search` `/query` | `X-API-Key` | 受控 `search` 调用（最小 topK）或 `/kb/retrievals` |
| `standard-retrieval`（新增，通用第三方） | REST（standard 协议） | 用户自填完整 endpoint | `Authorization: Bearer` + `X-Knowledge-Base-Id` | 发一次 `question` 探活，校验 `chunks` 结构 |
| `ragflow` / `weknora`（已有，纳入统一注册） | REST | 现有 | Bearer / 现有 | 现有 `probe_credentials` |

> 通用第三方只要实现 standard 协议（`POST {endpoint}` + Bearer + `X-Knowledge-Base-Id` → `chunks`）即可接入，无需为每种系统写适配器。

### 3.4 数据模型

`[决策点 A]` 存储复用：
- **方案 A1（推荐）**：复用 `connector_instances`（凭据已加密、已有 CRUD/test/共享语义），新增 `infra/knowledge/external/` 只存**绑定与策略**表。优点：不重复实现凭据管理、与 WeKnora/RAGFlow 现状一致；缺点：管理入口分散在「连接器」。
- **方案 A2**：独立两张表 `external_knowledge_sources`（含凭据密文）+ `knowledge_source_bindings`。优点：独立管理面、模型自洽；缺点：与连接器体系并行，凭据管理需重造。

推荐 **A1 + 一张绑定表**：

```sql
CREATE TABLE IF NOT EXISTS external_knowledge_bindings (
  id TEXT PRIMARY KEY,                    -- 公共字符串 id（ULID）
  owner_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  instance_id TEXT NOT NULL,              -- 关联 connector_instances（公共 id）
  kind TEXT NOT NULL,                     -- akb-mcp / akb-rest / ragflow / weknora / standard-retrieval
  ref TEXT NOT NULL DEFAULT '',           -- 默认知识库 ref（AKB baseCode / RAGFlow dataset_id / 外部 kb id）
  selector_argument TEXT NOT NULL DEFAULT '',   -- 强制注入的参数名（AKB: knowledgeBaseCode）
  forbidden_arguments TEXT NOT NULL DEFAULT '',  -- 禁止调用方传入的参数（逗号分隔，AKB: knowledgeBaseId）
  read_tools TEXT NOT NULL DEFAULT '',    -- 允许的工具白名单（逗号分隔）
  top_k INTEGER NOT NULL DEFAULT 8,
  min_score REAL NOT NULL DEFAULT 0.0,
  timeout_ms INTEGER NOT NULL DEFAULT 10000,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ekb_owner ON external_knowledge_bindings(owner_user_id);

CREATE TABLE IF NOT EXISTS external_knowledge_targets (
  binding_id TEXT NOT NULL REFERENCES external_knowledge_bindings(id) ON DELETE CASCADE,
  target_type TEXT NOT NULL,              -- agent | knowledge_base | scene
  target_id TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (binding_id, target_type, target_id)
);
```

迁移编号：`[决策点 B]` —— 现有断言 `v == 27`，027 由 `3d018583`（2026-10-19）引入且尚未随发布版本落地。按 `AGENTS.md`「未发布改动并入当前未发布 00N」应**并入 027**；但 027 已在 develop 上多日、他人本地库可能已到 27，改动会被跳过。**建议新建 `028_external_knowledge.sql` + `.pg.sql` 并同步版本断言到 28**，理由在 PR 中说明。

### 3.5 融合策略

`[决策点 C]`
- **C1（推荐）RRF 融合**：本地 `KnowledgeIndex` 得分与外部 `score` 量纲不可比，用 `1/(60+rank)` 倒数排名融合，再按预算裁剪。本地/外部各取 topK，融合后取前 N。
- **C2 分段拼接**：先本地后外部（或反之），各自按预算分配。实现简单、可解释，但排序质量略差。

默认 C1，管理员可在绑定上配置 `min_score` 与外部源的 `top_k`。

### 3.6 安全与合规

- URL 校验复用 `infra/utils/ssrf_guard.py` + `custom_mcp.validate_mcp_http_url`。`[决策点 D]`：**AKB 常部署在内网 HTTP**，`akb_client.py` 当前强制 HTTPS。建议新增显式开关（默认仅 HTTPS，管理员可为本源勾选「允许内网 HTTP」并在 UI 提示风险），而不是放开全局。
- 凭据一律经 `connectors/crypto.py` 加密入 `secrets`，**不进日志、不进 prompt、不返回给前端**（列表接口只回脱敏指纹）。
- 不跟随重定向（避免凭据转发）、超时 ≤ 60s、响应体限长（沿用 `akb_client` 的 2MB 思路，按源可配）。
- 错误脱敏：对外只给 `origin + code + 简短消息`，上游响应体不落日志。
- 权限：源 owner / 管理员可管；绑定目标（agent/KB/场景）必须校验归属；Agent 调用时复用 `knowledge_source_scope` 强制 selector 与 forbidden 参数。
- 解析 JSON-RPC 结果时区分 `transport_ok / rpc_ok / business_ok`，审计与 UI 状态分开（对应 AKB-04）。

### 3.7 API 设计（新增，前缀 `/api/external-knowledge`）

| Method | Path | 作用 |
|---|---|---|
| GET | `/sources` | 列出可见外部知识库源（含探测状态、脱敏凭据指纹） |
| POST | `/sources` | 新建（kind、instance_id 或内联凭据、ref、top_k、min_score、timeout、策略） |
| PATCH | `/sources/{id}` | 修改配置与启用状态 |
| DELETE | `/sources/{id}` | 删除（级联解绑） |
| POST | `/sources/{id}/probe` | 连通性 + 能力探测（MCP：initialize + tools/list；REST：探活并返回可用知识库列表） |
| POST | `/sources/{id}/search` | 检索测试（返回归一化 chunks / citations / warnings） |
| POST | `/retrieve` | 运行时多源检索（供 Agent / 网关），返回融合上下文 + citations + warnings |
| GET/POST/PATCH/DELETE | `/bindings` / `/bindings/{id}` | 绑定到 agent / knowledge_base / scene |

所有端点：`summary` + `response_model` + 归属校验 + `OctopError` 错误映射（对齐 `docs/development-standards.md` §5）。

### 3.8 前端

`dashboard/src/pages/KnowledgeBases/` 新增「外部知识库」Tab（或 `pages/Settings/ExternalKnowledge/`）：
- 列表（名称 / 类型 / 端点 / 状态灯 / 上次探测时间 / 绑定数）
- 新建/编辑抽屉：类型选择 → 端点与凭据 → 默认知识库 → 检索参数（topK / minScore / 超时）→ 高级（允许内网 HTTP、selector 与 forbidden 参数）
- 「探测」「检索测试」按钮（展示归一化 chunks 与 citations 预览）
- 绑定选择：Agent / 本地知识库 / 场景
- API 封装放 `dashboard/src/api/modules/externalKnowledge.ts`，类型放 `api/types/`；文案维护 `locales/{zh,en}.json`

### 3.9 Agent 侧接入

- 扩展 `infra/knowledge/tools.py`：`search_knowledge` 工具在描述中标注已挂载的**外部源**，检索时同时调用 `external/service.retrieve()`；外部片段在引用中标注来源系统（如 `[AKB · HR-POLICY]`）。
- 复用 `middleware/knowledge_source_scope.py`：外部 MCP 源在工具调用前强制 `knowledgeBaseCode`、拒绝 `knowledgeBaseId`（与 AKB 文档一致）。
- 数字员工现有 `knowledge_sources` 绑定保持不变（兼容）；新模块提供统一入口，后续可选迁移。

## 4. 实施计划

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1 适配层 | `infra/knowledge/external/{registry,normalize,service}.py` + `adapters/{akb_mcp,akb_rest,standard_retrieval}.py`，接入已有 `ragflow`/`weknora` | 单测：归一化、score 转换、去重、超时/错误降级 |
| P2 存储与 API | 迁移 028 + repo + `/api/external-knowledge/*`（CRUD / probe / search / retrieve / bindings） | 集成测试：CRUD 权限、探测失败态、检索测试返回结构 |
| P3 融合检索 | 扩展 `retrieve.py`（RRF 融合 + 引用统一 + warnings）与 `tools.py` | 单测：混合排序、单源失败不中断、预算裁剪 |
| P4 前端 | 外部知识库 Tab + 绑定 + 探测/测试交互、`tsc -b`、Prettier | `make lint-frontend`、`npx tsc -b` |
| P5 收尾 | i18n zh/en、CHANGELOG、`make all` 全绿、live 测试（真实 AKB，缺凭据自动跳过） | `make all` + 文档 |

## 5. 待确认决策点

1. **[A] 存储**：复用 `connector_instances`（推荐）还是独立 `external_knowledge_sources` 表？
2. **[B] 迁移**：新建 028（推荐）还是并入 027？
3. **[C] 融合**：RRF（推荐）还是分段拼接？
4. **[D] 内网 HTTP**：是否为本源提供「允许内网 HTTP」开关（默认关闭）？
5. **[E] 传输优先级**：AKB 走 MCP（推荐，与 AKB 官方指南一致）还是 REST `/api/v1/external/*`？
6. **[F] 一期范围**：是否包含 SSE 流式问答（建议二期）、是否包含 `READ_OBJECT`（知识卡片/证据详情）工具暴露给 Agent？
7. **[G] 绑定范围**：一期是否只做 Agent 绑定，还是同时支持本地知识库 / 企业场景绑定？

## 6. 参考

- AKB：`docs/api/OCTOP-INTEGRATION.md`、`docs/api/external_kb_search_protocol.md`、`docs/release/AKB_feature_octop_mcp_integration_代码审查与改进实施方案.md`（AKB-03/04 为本方案直接依据）
- Octop：`AGENTS.md`（§5 模块边界、§7 数据库/迁移规则）、`docs/development-standards.md`（§3/§4/§5/§6）、`infra/knowledge/akb_client.py`、`infra/connectors/gateway/registry.py`
