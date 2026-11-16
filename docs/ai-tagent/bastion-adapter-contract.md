# 堡垒机适配连接点与离线回执契约

2026-11-16，基线 `c9fd4021c47a8a06733a6787434fc7c60cbce91e`。
本阶段没有联网适配器、堡垒机配置入口或执行命令的新增能力。

## 现有能力审查

| 连接点 | 当前实现 | 不可据此推断的能力 |
| --- | --- | --- |
| 数字员工 | `infra/digital_workers/service.py` 校验岗位任命并关联 Agent 与 owner_user_id | 不存在已验证的员工专用堡垒机主体/账号映射 |
| MCP | `infra/connectors/custom_mcp.py` 和 `builder.py` 已支持用户可见连接器、远程 MCP 与 OAuth 配置 | 用户持有或共享的凭据不等于员工独立身份；不能直接把任意 MCP 工具输出接入验收 |
| 企业巡检 | `enterprise_assistant/adapters.py` 已有 HTTP 采集及带固定主机密钥、显式私钥、固定四条命令的直接 SSH 采集 | 直接 SSH 不是堡垒机代理；没有厂商无关机器授权或堡垒机审计关联 |
| 运行保护 | `AssistantService.live_snapshot` 重验任务/连接版本、允许来源、权限；`scenes/inspection.py` 绑定同 run 报告 | 当前同 run 场景只接受 HTTP 巡检，未开放 SSH/MCP 场景执行 |
| 证据与报告 | 子运行持久化、内容/快照哈希、场景报告与独立验收 | 内容哈希不是外部系统签名，不证明命令实际执行或身份真实性 |

因此不新增另一套 MCP 客户端或直接 SSH 执行器。未来协议适配应复用现有连接器的
凭据/传输能力，在受控领域服务中解析授权与身份，调用有明确范围的操作，再将
校验后的证据接入现有运行事务；不得把通用 Agent 任意工具调用等同于批准的巡检。

## 本阶段可复用契约

`src/octop/infra/enterprise_assistant/inspection_receipts.py` 提供三个不可变、严格字段模型：

- `InspectionBinding`：组织、员工、Agent、run、逐次 request、连接器及版本、策略版本、
  外部主体、目标、已注册操作 ID。字段是有界引用，不能放 URL、凭据或 shell 命令。
- `ReceiptExpectation`：调用方预期绑定、有效窗口（最多 300 秒）、来源标识。
  来源仅允许 `simulation` 或 `unverified_external`，不提供“已验证”选项。
- `InspectionReceipt`：协议版本 1、绑定摘要、实际主体/目标/操作声明、审计事件 ID、
  起止时间、collected/denied/failed 结果及有界产物摘要/大小元数据。

`validate_receipt(raw, expected, now=...)` 在 64 KiB 上限内解析 JSON，拒绝重复键、
额外字段、类型转换、错配、过期和不完整产物声明。每个绑定维度都进入确定性摘要；
即使摘要一致，主体/目标/操作声明也必须匹配。错误代码不包含原始回执内容。
失败/拒绝回执不得同时声称取得产物，采集成功必须包含产物摘要和大小。

`receipt_preview` 复用 `format_report` 生成明确标注的离线核验预览，显示上下文与
审计引用。成功采集仍为 health unknown；失败/拒绝为 unavailable。只检查结构和
关联，不采信上游自报健康，不把原始工具文本或任意执行输出直接写入报告。
这是开发者接口，无生产路由、持久化或前端接线，不能作为场景验收的可信证据源。

最小合成调用例可直接复用 `test_inspection_receipts.py` 中 `expected` 和 `payload`。
字段 JSON Schema 可由各模型的 `model_json_schema()` 获取；不要求 MCP 或特定厂商。

## 信任边界与未来接线要求

调用方提供的 expected 必须来自可信服务端的当前授权事实，而不是请求体/模型/回执。
当前函数不读取数据库、不授予权限、不核验签名、不证明文件存在或与摘要一致。
回执完全匹配也可能是伪造，预览明确显示 `NOT VERIFIED`；不能升级为真实执行成功。

request_id 提供关联字段，但纯校验可重复调用，**没有防重放或恰好一次执行保证**。
未来执行入口必须持久化单次 claim，核验已批准快照及当前身份/ACL/连接版本，处理
调用超时后的未知结果，禁止自动重试命令；需通过堡垒机审计查询对账。当前没有跨
进程抢占、撤权与外部调用原子性、外部产物获取/签名验证或报告事务接线。

真实联调之前需明确：

1. 经授权的测试堡垒机/接口与测试目标机器，厂商不限，可用 MCP 或其他接口。
2. 数字员工独立主体的注册/映射/撤销方式、最小权限目标和操作清单、审批人及凭据
   托管方式；不得默认复用用户个人或管理员凭据，也不得把 Agent ID 当外部身份凭证。
3. 可信接口契约、TLS/主机验证、端点允许名单、只读操作语义和有界输出；MCP 工具
   名称“只读”本身不是权限保证，需要对端实际授权及可审计执行约束。
4. 可认证的回执及审计事件查询、时间语义、产物获取与脱敏、失败/超时/重复请求行为。
5. 将真实身份、目标、操作、逐次运行、产物和审批关联至报告的持久化设计及独立验收。

## 验证记录

本轮在合成数据下验证全部绑定维度错配、回执字段越权、时间窗口、大小/类型限制、
重复键、拒绝/失败结果、并发纯解析及报告预览。新测试禁止 DNS/连接和子进程调用。
既有巡检回归使用 HTTP transport/采集替身；不连接真实服务器、不执行真实命令。

复现：

```bash
UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 uv run pytest \
  tests/unit/enterprise_assistant/test_inspection_receipts.py \
  tests/unit/enterprise_assistant/test_workflows.py \
  tests/integration/test_scene_inspection_execution.py -q -rs
UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all
```

最终 98 项通过、1 项跳过（缺少可选 Paramiko，既有 SSH 单测未运行），无失败。
静态门禁通过：mypy 666 文件、前端 TypeScript，ESLint 保留 69 条既有 warning。
首次 Ruff 检查发现测试中 dict 构造风格问题，修正后检查通过。没有前端代码修改，
本轮不重复构建。Docker client/server 29.9.2 可读，bubblewrap 0.12.0 已安装；未运行
容器或重试既有受阻的 sandbox 探测，这些版本不构成部署隔离验收。未运行全量
make all、受限遥测、真实 MCP、
堡垒机、PG 或生产部署。恢复激活继续关闭，Mattermost 真实发送仍未接通。
