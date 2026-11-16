# 企业巡检整体交付核验 — 2026-11-16

基线：feature/akb-octop-litellm-dev，521dd2bf7690a9851df5b3f7640f4e6c00a327f1。
本轮不新增业务功能；补齐可重复的真实回环 HTTP / 安装包验收，并说明产品发布差距。
项目包版本 1.0.2b7 是原 Octop 版本，不代表 AI-TAgent 企业场景达到 1.0。

## 核验环境和方法

从干净临时目录创建独立 Python 3.12.14 虚拟环境，通过 uv.lock、离线缓存执行
uv sync --frozen --no-editable，成功安装 218 个包。Octop 和仓库内 AKB SDK 均构建为
非 editable 安装，已核对 import 来自新环境 site-packages；其他依赖取自既有锁定缓存，
没有联网下载、接受新增条款或使用真实凭据。不是从公网从零拉取依赖的安装验收。

每个用例创建独立空 HOME 和 SQLite。主要闭环没有直接插入用户、组织、运行、报告或
验收状态，均调用公开 API。一次走真实 Uvicorn TCP listener（127.0.0.1 随机端口），
实际服务构建后的 SPA HTML 和其入口脚本/样式；并非 ASGITransport 冒充网络请求。
另三次仍用 ASGITransport，覆盖接受、要求修改返修、失败后新申请。

Uvicorn 和客户端在同一个测试 Python 进程内，OctopServer 生命周期真实启停。
Agent manager/harness、供应商配置对象使用已有测试替身；禁止模型调用；巡检 HTTP
上游使用 MockTransport 合成健康与磁盘数据。proactive care 被暂停，遥测关闭。
测试级 socket/DNS 检查只放行该 listener 的数值回环地址及端口，禁止其他连接/UDP；
这不是 OS 网络沙箱，不允许用此夹具执行不可信代码或装载真实秘密。

## 一条完整操作路径及证据

| 步骤 | 管理者/操作员路径 | 本轮实际验证 | 状态与边界 |
| --- | --- | --- | --- |
| 安装 | 安装 Octop，包含仪表盘与依赖 | 独立非 editable 环境，218 包，导入路径核对 | 通过；离线缓存安装，不是生产镜像/系统服务 |
| 启动和前端交付 | 打开站点 | 真实 Uvicorn 返回 SPA HTML 与入口 JS/CSS，资源非 HTML 错误页 | 通过资源交付；未执行浏览器 JS |
| 初始化 | 设置 SQLite，建立初始管理员，完成向导 | 真正一次性向导令牌、数据库创建与 API | 通过；全部合成账号 |
| 登录和角色 | 管理员登录，创建审阅者并登录 | 实际密码认证/JWT，未覆盖 current_user 依赖 | 通过；非真实人员/SSO |
| 配置员工 | 建组织、岗位、任命 Agent，启用数字员工 | 公开 API 保存组织/岗位/任命/员工 | 通过本地配置；Agent 运行时为替身 |
| 创建任务 | 建目标与标准，建方案并标记就绪，选择场景与巡检来源 | 真实版本校验、方案/来源快照及待审批记录 | 通过；源地址 fixture.invalid，仅替身响应 |
| 独立审批 | 另一名组织管理者审阅并批准 | 未登录、自批、撤权、普通成员、错误组织拒绝；两个并发批准只成功一个 | 通过实际 API 和 SQLite；未做浏览器多会话 |
| 运行 | 批准后排队执行巡检 | 真实 Cron 调度、执行器、报告持久化；只采集一次 | 通过本地执行路径；不代表真实堡垒机或模型 |
| 验收 | 读取同 run 报告，独立接受或要求修改 | 真实内容/快照哈希绑定及验收持久化 | 通过；证据内容为合成数据 |
| 返修/失败 | 新草稿、重新选来源与共享、重新独立审批 | ASGI 分支验证要求修改后新运行、来源版本变化失败后新申请；旧记录保留 | 通过；真实 TCP 分支本轮只验证接受主路径 |
| 管理进度 | 读取组织进展 | 分开统计执行和验收；跨组织拒绝，accepted 不自动完成目标 | 通过 API；前端另有 jsdom 测试 |
| 正常重启 | 停止服务后重新启动、重新登录 | 同一临时安装 OctopServer stop/start；报告哈希/验收/进度一致，无重复采集 | 通过同进程服务重启；非进程崩溃、断电或恢复演练 |

## 可复现的检查

先在 dashboard 执行 npm ci（若依赖尚未安装），再执行 npm run build。本轮使用现有
锁定 node_modules，未重复 npm ci。构建产物为 src/octop/dashboard，不手工编辑。

在仓库根目录执行（只用于隔离测试环境）：

```sh
DELIVERY_ENV=$(mktemp -d /tmp/octop-delivery-venv.XXXXXX)
UV_PROJECT_ENVIRONMENT="$DELIVERY_ENV" UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 uv sync --frozen --no-editable
OCTOP_TEST_DELIVERY_HTTP=1 UV_PROJECT_ENVIRONMENT="$DELIVERY_ENV" UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 uv run --no-sync pytest tests/integration/test_fresh_install_inspection_delivery.py -q
```

实际环境路径为 /tmp/octop-delivery-venv.5IBtwd；它是本轮临时安装，不用于部署。
测试成功或失败均关闭 HTTP listener 与 OctopServer。普通 CI 默认跳过依赖前端产物的
TCP 参数；交付核验必须显式设置 OCTOP_TEST_DELIVERY_HTTP=1，缺少前端产物时失败，
不能把该项 skip 记为通过。

## 仍阻断完整交付的条件

- 浏览器：Chromium 已安装，但此前沙箱启动失败，bubblewrap UID 映射受限；本轮没有绕过或重试失败设置。需要提供可安全启动浏览器沙箱的隔离运行器，再跑两个用户的实际 UI 点击、离线、撤权、导航和刷新。jsdom 与 HTML 下载不能替代它。
- 生产形态：Docker client/server 29.9.2 可用，但没有启动产品部署容器；仍需授权的无外网/无真实秘密部署环境验证入口命令、TLS、进程重启、持久卷、升级和负载。本轮没有改 OS 安全/网络设置。
- 堡垒机：需要授权测试实例、数字员工独立身份、最小只读授权、目标机器和回执契约。厂商未限定，可用 MCP 或其他接口；本轮没有访问机器。
- AKB/模型：需要授权测试端点、合成文档、可信用户/Agent 绑定、强制内容 ACL 与双用户撤权证据，以及允许的模型调用/费用范围。知识辅助规划仍不可激活。
- Mattermost：入站服务身份及源状态契约仍未完成真实双服务验收，真实发送保持关闭。
- 安全恢复：隔离镜像不等于服务恢复；原地/在线恢复和镜像激活仍关闭，数据库/工作区一致性和可证明停写仍是阻断。
- 数据库/完整门禁：未运行真实 PostgreSQL、Windows、完整 make all 或受限遥测测试；不能宣称跨平台或全量发布门禁通过。

本轮未发现此合成主路径中的新产品缺陷，因此不以增加占位功能替代以上解除条件。

## 本轮执行结果

- 独立安装环境：fresh-install 4 项全部通过（3 条 ASGI 分支、1 条真实 TCP 主路径）；最终格式修正后，TCP 与重启用例再次单独通过。
- 同一安装环境：巡检执行、验收领域/API、组织进度 4 文件共 106 项通过；与上述合计 110 个不同后端用例。
- 前端 jsdom：OrganizationProgress、AccessSession.sensitive、RunApproval、RunDetails.approval、RunDetails、RunQueue.delivery、RunQueue、InspectionRevision、DeliveryAcceptance 共 85 项通过。
- make lint-all typecheck-all 通过（mypy 674 源文件），最终修改测试文件 Ruff 检查/格式检查通过。中途一处嵌套 async-with 风格检查已修正。
- NODE_ENV=production NODE_OPTIONS=--max-old-space-size=2048 npm run build 通过；保留既有 ESLint、构建体积、Browserslist 和库弃用提示。
- 本轮测试没有未修复失败。未运行项不能计为通过，详见上表和阻断条件；没有真实模型、AKB、堡垒机、Mattermost 联调结果。

## 安装与健康检查复核（基线 99ca4679，2026-11-16）

本轮确认并修复服务管理探针的误报：此前任何 HTTP 200 且可解析 JSON 的响应
（包括 `{"ok": false}`、空对象、数组）都返回健康。现在要求对象中的 `ok` 严格为
布尔 `true`；错误状态码、非 JSON、连接失败和否定响应返回失败，保留现有有界重试。
成功详情明确仅是 liveness，未检查模型和对接 readiness。`/api/health` 的响应结构不变，
OpenAPI 描述明确不测试依赖；无数据库的安装向导仍可存活，不用模型缺失阻断配置入口。

配置提示审查：

- ModelStep 与 `wizard.model.intro` 已提示模型用于助手回复，可跳过并稍后配置。
  本轮实际 HTTP/ASGI 用 `provider_draft: null` 完成安装；健康成功并不证明模型可调用。
- KnowledgeMappings 的 `knowledgeMappings.boundary` 已明确检索未启用、映射/快照不等于
  AKB 授权、逐用户 ACL 未验证。知识库不是基础安装必须项，知识辅助场景仍需单独验收。
- 企业方案 readiness 仅是本地只读观察，`execution_authorized=false`；Mattermost 本地
  预览与未挂载的入站接收不等于真实对接。这些边界没有因健康探针成功而开放。
- 未新增一个声称统一证明外部依赖可用的仪表板，未尝试远端连通性或读取真实凭据。

验证：新增探针测试在修复前 6 failed / 4 passed，复现误报及缺少范围提示；修复后
`uv run pytest tests/unit/infra/setup/test_health_probe.py tests/unit/infra/setup/test_service.py
tests/unit/cli/test_service_cmd.py -q` 为 66 passed。带 `OCTOP_TEST_DELIVERY_HTTP=1` 的
既有 fresh-install 测试为 4 passed，新增断言确认安装前 db=false、无模型完成安装后
db=true 均仅表示 liveness；包含真实回环 HTTP，harness 与采集仍为替身。

安装失败定向回归覆盖 provider 保存故障、初始管理员插入回滚及默认 Agent 引导失败后
重启保持关闭；失败后的管理员恢复操作仍需要现有管理路径，不宣称自动完成安装。
本轮没有重新构建安装包、浏览器点击或更改服务管理器；未验证真实模型/AKB/堡垒机/
Mattermost、PG、生产部署及受限遥测。

上述三个安装失败定向测试均通过（3 passed）；本轮总计 73 passed。
`UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all` 通过
（mypy 675 个源文件，既有 ESLint 警告保留）。无前端源码变动，未重复生产构建；
未运行全量 make all 或受限遥测测试。
