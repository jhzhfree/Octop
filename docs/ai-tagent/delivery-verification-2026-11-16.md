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

## 版本化历史下拒绝切库的配置一致性（基线 f765e119）

复现：干净 SQLite 安装启用 `history_v2_enabled=true`，尚未创建管理员时，向
`POST /api/setup/database` 提交另一个空库路径。原接口返回 400，但已创建/迁移目标库
并改写 config.json；当前进程仍使用原库，下次启动却选择新库，可能触发历史库身份
不匹配。修复前集成测试在“配置应保持字节不变”断言失败。

现在复用原有切库资格检查，在探测、迁移目标库和持久化配置之前拒绝；底层 rebind
入口仍重新检查。移除错误信息中“重启即可切库”的误导。正常未启用版本化历史的空库
切换保留；不支持历史迁移，也不尝试修改已错配的安装。

新增测试使用实际 HistoryArchive、临时 SQLite 和 ASGI/真实回环 HTTP 两种入口，
断言拒绝后配置字节、当前池不变且未创建目标库，然后创建管理员、停止并重新启动，
验证管理员仍存在。harness 为替身，网络只允许测试回环 listener；正常重启在同一
测试进程中，不能声称已验证 OS 服务重启或断电。

范围限制：本修复提前阻止已知不兼容运行时的确定性失败，不提供 config.json 与
数据库切换的跨进程事务；并发管理员/其他配置写入、切换中的后续 I/O 故障等仍不应
依赖此预检查作为全局互斥保证。已有 SQLite 迁移锁只协调合作升级进程，不是全局停写。

本轮回归命令（`LANGFUSE_TRACING_ENABLED=false OTEL_SDK_DISABLED=true`，uv 离线缓存）：

```sh
uv run pytest tests/integration/test_setup_database.py \
  tests/unit/db/test_migration_process_lock.py \
  tests/unit/db/test_migration_atomicity.py \
  tests/unit/db/test_runtime_replace_services.py \
  tests/integration/test_setup_wizard.py::test_concurrent_initial_admin_claim_creates_only_one \
  tests/integration/test_setup_wizard.py::test_initial_admin_insert_failure_rolls_back_setup_claim \
  tests/integration/test_setup_wizard.py::test_bootstrap_failure_does_not_report_completed_and_restart_stays_closed -q
```

结果 **46 passed**（既有依赖弃用警告保留）：数据库选择 6、独立 spawn 进程迁移锁 6、
迁移原子性 29、runtime retarget 2、管理员并发/插入失败/引导失败 3。管理员 HTTP
竞争是同进程异步请求；跨进程验证针对迁移，不扩称多进程向导或配置写入已安全。
未运行完整 make all、受限遥测、真实 PG、浏览器或外部服务；没有部署和真实外发。

本轮 `make lint-all typecheck-all` 通过（mypy 675 个源文件）；变更文件 Ruff 和
`git diff --check` 通过。没有前端源码变更，未重复构建。

## 并发向导提交与过期工作进程（基线 c0db77d5）

本轮复现一个实际竞争：第一个 `/setup/database` 已持久化 first.db、等待运行时启动时，
第二个请求可以改为 second.db 并返回 200；第一个请求恢复后也返回成功，但其选择已
被覆盖。不是通过线程调度猜测：测试暂停首次 bind 后提交第二次请求，修复前断言失败。

现在 `/setup/database`、`/setup/initial-admin`、`/setup/finish` 共用安装目录中稳定的
`.setup-mutation-lock` SQLite 伴随文件，持有非阻塞排他事务跨越 handler 的 await。
竞争请求立即返回既有 SETUP_REQUIRED/410（操作进行中），不排队堵塞事件循环。
进入后比较工作进程加载的数据库配置与当前配置；不同则返回 409 并要求重启该工作
进程，避免仅有锁却向过期仓储创建管理员或覆盖新配置。原用户创建事务/CAS 和向导
生命周期检查仍保留，锁不替代认证与授权。新进程重载正确配置后可继续安装。

验证范围：

- 真实回环 HTTP 暂停第一次 bind、并发提交第二个数据库选择：竞争者被拒绝，未创建
  第二库；随后创建管理员，正常停止/启动后仍读取 first.db 和唯一管理员。
- 两个运行时加载同一初始配置，只有先选择数据库者可继续；另一个的数据库、管理员、
  finish 请求均拒绝，配置不被覆盖，重启后可读取已创建管理员。
- 独立 spawn 进程验证排他、正常退出释放、terminate 后释放、过期配置拒绝。这里是
  真实进程/文件锁测试，不是完整双 HTTP 服务部署。
- 配置写入故障注入验证原配置不变、guard 释放、重试成功和重启可读；现有用户插入
  失败事务回滚及 finish 副作用故障保持关闭的回归继续执行。

部署与兼容边界：仅适用于采用本协议、共享稳定本地安装目录的进程。不要删除/替换
伴随文件，不与旧二进制混跑；不覆盖同 UID 文件替换、直接编辑配置、其他管理 API/CLI
写配置、普通业务写入或网络文件系统。单机 SQLite 已测试，PG 与多主机未验。
这不是整个配置文件和数据库/运行时启动的原子事务；配置提交后的引导故障、系统断电
及历史遗留错配不自动回滚，不应把本修复用于在线恢复或证明全局停写。

本轮最终验证（关闭遥测，uv 离线缓存）：

```sh
uv run pytest tests/unit/infra/setup/test_mutation_guard.py \
  tests/integration/test_setup_database.py tests/integration/test_setup_wizard.py \
  tests/integration/test_setup_bootstrap.py -q
```

**61 passed**，包含最终真实 HTTP 并发测试，既有依赖弃用警告保留。新增测试初次运行
发现测试快照在默认配置首次落盘前捕获、ASGI 直接传播注入异常两项夹具问题，已按真实
启动后快照及异常传播语义修正；最终无失败。`make lint-all typecheck-all` 通过
（mypy 676 个源文件），Ruff 和 diff 检查通过。没有前端源码变更，未重复构建；未跑
全量 make all、受限遥测、PG、真实模型/AKB/堡垒机/Mattermost 或生产部署。

## 首次配置发布竞争与配置别名（基线 8452272b）

独立审查发现启动本身也会写配置：进程 B 在“配置不存在”后暂停，A 完成默认配置
加载并持有 setup guard 写 chosen.db，B 恢复 `write_text` 会覆盖为默认 octop.db。
本轮用真实 spawn 子进程执行 load_config、在默认发布前暂停复现，修复前测试失败。
这是同版本正常启动路径缺口，不能归因于手工写文件或旧版本。

load_config 现在通过同目录独占临时文件写入完整 JSON，flush/fsync 后使用不覆盖
目标的硬链接原子发布；若 EEXIST 则重读竞争赢家。不会使用陈旧 exists 结果覆盖文件。
仅自己的未被修改默认值保留首次延迟绑定语义；竞争者写入的数据库配置原样解析。
普通异常清理临时文件；发布前进程终止可能留下未引用的临时文件，但不暴露半截
config.json，后续启动仍能创建配置。不提供断电持久性或目录 fsync 的验证承诺。
文件系统必须支持同目录硬链接；不支持时失败关闭，不回退覆盖写入。新默认配置
沿用临时文件的私有权限（POSIX 0600），既有配置权限不重写。

配置别名策略：config.json 文件符号链接不再支持，load_config 拒绝并且不改目标；
向导 guard 也拒绝非普通文件、符号链接和多硬链接配置，防止不同 home 的两把锁
被误当作对共享配置互斥。整个 home 的目录符号链接仍归一到同一锁。已有文件别名
部署必须由管理员在停服下准备各自私有配置，不能把普通复制当作数据库迁移或安全
恢复方案；本轮不自动转换、不改用户数据。不能抵御同 UID 主动路径替换。

测试覆盖：持 setup guard 的 A 与延迟首次创建 B、两个同时首次创建者、发布前
terminate、fsync/link 故障清理与重试、配置文件符号链接拒绝、跨 home 共享文件
别名拒绝及 home 目录别名互斥。仅合成临时文件，没有读取真实配置凭据。

本轮关闭遥测、使用 uv 离线缓存执行：

```sh
uv run pytest tests/unit/test_config_creation.py tests/unit/test_config.py \
  tests/unit/infra/setup/test_mutation_guard.py tests/integration/test_setup_database.py \
  tests/integration/test_setup_wizard.py tests/integration/test_setup_bootstrap.py -q
```

**147 passed**，最终无失败，既有依赖弃用警告保留。未运行完整 make all、受限遥测、
真实 PostgreSQL、Windows、网络文件系统、断电或生产部署。

本轮 `make lint-all typecheck-all` 通过（mypy 676 个源文件），Ruff 和 diff 检查通过。
无前端变更，未重复构建。

## 发布后中断恢复（基线 aa64a7cb）

独立复核新增的实际故障点：link 已发布完整 config.json、unlink 尚未执行时杀死
发布进程，会留下两个硬链接；51f25df 的 guard 正确拒绝共享配置，却也使本次合法
发布残留卡住向导。之前的 kill 测试仅覆盖 link 前，不能作为此窗口的恢复证据。

现在 load_config 在解析完整配置后收尾已发布的临时链接：只考虑同目录的发布器
保留名称 `.config.json.<8位小写字母/数字/下划线>.tmp`，必须是普通文件、与当前
配置 dev/inode 相同；POSIX 还要求当前 UID 所有且模式 0600。检查期间发现配置
inode 改变则停止清理。不改配置内容、不删除其他 inode、目录或符号链接。这个
内部临时名称空间保留给发布器，不能放用户文件；不是对同 UID 主动伪造路径的保护。

其他位置/名称的共享硬链接保留，setup guard 仍要求 nlink=1；因此跨 home 共享
配置不会因本次恢复被放行。清理权限或 I/O 持续失败时仍报错，待管理员修复文件
系统条件后重新启动，不自动放宽权限。没有清除其他未发布孤立临时文件的扫盘操作。

新测试在真实子进程 os.link 成功后暂停并 terminate，检查配置字节不变、残留链接
收尾、guard 可进入；真实回环 HTTP 验证随后选择数据库、创建管理员并正常重启。
另注入 unlink 失败后重试、附加跨 home 等价共享链接仍拒绝、无关临时文件/目录
不删除、非私有模式的别名不作为发布残留清理。修复前 post-link 和 unlink 两项
测试均失败。本轮仍未验证断电、Windows、网络文件系统或生产启动。

最终代码定向门禁：`test_config_creation.py`、`test_config.py`、
`test_mutation_guard.py`、`test_setup_database.py`、`test_setup_wizard.py`、
`test_setup_bootstrap.py` 共 **152 passed**；采用 uv 离线缓存并关闭遥测。
`make lint-all typecheck-all` 通过（mypy 676 个源文件），diff 检查通过。没有前端
源码变化，未重复构建；未运行全量 make all、受限遥测、外部联调或生产操作。
