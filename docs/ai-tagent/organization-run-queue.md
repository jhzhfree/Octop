# Octop 组织运行待办里程碑

## 功能

组织页提供只读运行与交付列表，组织所有者、管理员及平台管理员可选择五类：待审批、执行失败、待人工验收、需修改交付、已人工验收。每行显示运行编号、分类和服务器时区的创建时间，页面显示读取时间及下一步说明。默认每页 20 条，可刷新首页或查看下一页。点击编号可定位同次运行详情及按需读取报告、人工验收、已有发布预览；不提供批量审批或一键重试。

接口：`GET /api/organizations/{organization_id}/run-queue?category=unreviewed_success&limit=20`。后续页携带返回的 `next_before`。接口允许 1–50 条，使用数据库内部顺序游标，不使用 offset，不返回提示词、失败正文、交付证据、凭据或申请人身份。

## 状态语义

- `awaiting_approval`：运行待审批，且没有验收记录。
- `failed`：执行失败，且没有验收记录。
- `unreviewed_success`：执行成功，但没有人工验收记录。
- `changes_requested`：执行成功，但人工要求修改。
- `accepted`：执行成功，且同组织保存了人工通过记录；用于后续查看，不表示待办未完成、报告仍可访问、目标完成或消息已发送。

排队中、审批拒绝不属于此列表。已人工验收作为交付回看分类保留。异常组合不强行归类，可在组织进展汇总中查看异常计数。运行成功不等于目标完成。本接口不写数据库，不自动启动任务、重试、审批、验收或写入知识库。

## 分页与权限

按运行写入顺序倒序，游标严格向旧记录移动。新记录不会挤动后续页，刷新首页才能看新记录。多页不是固定快照，状态变化可能使运行移入或移出筛选结果。每次读取前后复查当前数据库权限；这不是事务级原子撤权保证。SQL 始终限定组织，验收关联同时限定组织。

UI 切换组织、权限或分类会清空旧结果并丢弃旧响应；重复点击不会重复提交请求。数据只通过现有 typed API 模块获取，并要求 `Cache-Control: no-store`。

## 初始版本验证与边界（历史记录，后续变更见下文）

后端针对性测试覆盖分类、跨组织隔离、权限撤销、禁用管理员、keyset 分页及插入稳定性、参数和 HTTP 契约。组件测试覆盖读取权限、分页、错误组织响应、重复点击及切换上下文的迟到响应。

本轮仅验证本地 SQLite 与组件逻辑，不宣称真实调度、外部模型、AKB 或 PostgreSQL 已联调，也不宣称浏览器端到端验收完成。全量 `make all` 受既有测试遥测外联限制，未运行，不得按定向测试通过标为全量通过。

初始版本规划的后续产品项：目标完成确认、目标选中状态同步、待办直接定位到场景运行详情（现已部分完成，见下文）。当前不为这些后续项扩大权限或启用外部能力。

本轮执行结果：`uv run --frozen pytest tests/unit/scenes/test_progress.py -q` 30 通过；`tests/unit/i18n` 80 通过；改动的 3 个源文件定向 mypy 通过；Ruff/ESLint/Prettier 检查通过。RunQueue、OrganizationProgress、DeliveryAcceptance 与 Organizations.planning 四个前端文件覆盖 42 项，初次发现重复无权限文案导致单一文本选择器失败，改用明确队列文案后该文件 12 项重跑全部通过，其余文件已通过。jsdom 仍输出现有伪元素样式支持警告，不等于浏览器验证。

## Follow-up: minimal details and explicit goal completion

Clicking a run number now opens persisted metadata through
`GET /organizations/{organization_id}/run-queue/{run_id}`. This uses the same
current manager/owner or platform-admin check as the queue, before and after
reading. SQL and response schemas exclude prompts, error text, requester/reviewer
identities, evidence and full plan snapshots. Unlike the legacy scene-run detail
route, this route does not reconcile Cron status. A queued row remains queued;
refreshing may still show lagging persisted state. It does not approve or execute
work. No detail requests occur until an explicit click; closing or switching
organization discards delayed results.

Active goal completion now shows the target and success criteria for confirmation.
Cancel leaves the goal unchanged; repeated submission is suppressed and failed
updates can be retried. The successful API result updates both the list and the
selected goal, removing active planning controls. Goal/plan reads and scene-run
polling discard old-context responses. Organization/worker scope uses a monotonic
generation, including A → B → A switching.

This is UI confirmation, not automated verification of success criteria. It does
not cancel in-flight tasks or write knowledge. Cross-client goal status races are
not solved by this change; the existing backend has no goal-status CAS contract.
No real database, browser E2E or external service integration is claimed.

## Follow-up: same-run delivery entry from the queue

The persisted detail dialog now offers three explicit actions for successful
runs when the current manager has task access (or is a platform administrator):
read the existing report/delivery evidence, open the existing independent
acceptance flow, and read an existing Mattermost publication preview. All targets
come from the selected organization and run; no pasted report/run identifier or
new cross-run attachment is introduced. The initial detail read still fetches
metadata only. Native inspection evidence is the same-run report; ordinary scene
runs may expose their existing delivery summary instead.

The existing evidence and acceptance endpoints remain authoritative. A queue
manager is not automatically an eligible reviewer: the server still rejects the
requester reviewing their own delivery and checks the current reviewer/agent
permissions. Missing or forbidden evidence is shown as unavailable. Acceptance
still requires the existing explicit decision confirmation. The queue's
publication view is read-only: create/approve/revoke controls are disabled here;
those existing workflows remain in the scene-run view. Reading never creates a
preview or sends a message, and `blocked_not_connected` still means not sent.

Organization, identity, role, permission, category and selected-run changes
unmount the old detail session. Queue refresh/pagination also closes it. Closing
an evidence view or beginning a reread clears its old text; failed rereads never
retain old report content. Delayed reads cannot repopulate a destroyed session.
Evidence requests now explicitly use `cache: no-store`. This does not add server
push revocation: a remote permission change is enforced on the next request or
when the application's current authorization context updates, not by continuous
background polling.

Validation for this increment is recorded below. It does not establish live
Mattermost, model, AKB, PostgreSQL, production deployment or browser E2E readiness.

### Validation record (2026-11-15)

- Clean checkout from `5ad98a282e62c56031006106dda03f2f0e93d56c`; no local
  changes or remote divergence were overwritten. Repository `AGENTS.md` read;
  this checkout has no `.agents/skills` directory.
- Frontend: `npm test --` with `RunQueue.delivery.test.tsx`, `RunQueue.test.tsx`,
  `RunDetails.test.tsx`, `DeliveryAcceptance.test.tsx`, and
  `MattermostPublication.test.tsx`: **54 passed**. Includes exact run targeting,
  explicit reads, no publication mutation, wrong-run rejection, denied reread,
  and organization/identity/permission/refresh cleanup with delayed responses.
  jsdom pseudo-element warnings are not browser E2E evidence.
- `UV_CACHE_DIR=/tmp/octop-uv-cache uv run pytest tests/unit/scenes
  tests/unit/digital_workers tests/unit/i18n -q`: **460 passed**.
- `uv run pytest tests/integration/test_mattermost_publication_api.py
  tests/integration/test_ai_tagent_milestone_offline.py -q` (same cache override):
  **13 passed**, two dependency deprecation warnings. These use local fixtures,
  not a real Mattermost/model/AKB deployment.
- `make all PYTEST_JOBS=2` (same cache override): Ruff check/format, full ESLint
  and Prettier passed; mypy failed in the unchanged baseline
  `src/octop/infra/db/repos/setup_lifecycle.py:25,41` (`no-any-return`). Its later
  full-suite test target was **not run**. No claim of a green full ship gate.
- Final changed frontend files passed targeted ESLint and `npx tsc -b`.
  `npm ci --cache /tmp/octop-npm-cache` and `npm run build` succeeded; generated
  dashboard assets remain ignored per repository policy. Vite emitted existing
  chunk/import warnings. `git diff --check` passed.
- Python 3.12.14, Node 24.22.1; Python dependencies installed with a writable
  temporary uv cache and `CC=gcc` (default clang was unavailable). Docker
  29.9.2 client and daemon responded to `docker version`; no application
  container or production service was started. `/usr/bin/bwrap` exists, but
  `bwrap --ro-bind / / --unshare-user --unshare-pid --proc /proc true` failed
  with `setting up uid map: Read-only file system`; agent sandbox execution
  is not established by these tests.
- Not run: real browser E2E, real PostgreSQL, Mattermost sending, model/AKB
  integration, production deployment. No real messages sent; no merges/deploys.

## Follow-up: revoke-safe report reads (2026-11-15)

The shared report reader now serves both the organization queue and the original
scene-run page. Reading is an explicit human click, not an automatic operation.
Closing, context/role changes and observed permission revocation destroy content
and pending responses. A reread clears the old report before requesting; denied
or malformed results never leave it displayed. An unavailable report remains a
separate state. No acceptance, retry of execution, publication or sending is
triggered by viewing a report.

The existing `/scene-runs/{run_id}/evidence` response shape is unchanged. Its
service resolves current database identity, disabled state, task permission and
organization/run participation before and after asynchronous transcript I/O;
a cached administrator role is not retained after demotion. It rejects a changed
run snapshot, and checks native inspection report text/hash against the persisted
same-run batch, matching the existing independent acceptance integrity boundary.
The route sets `Cache-Control: no-store` on success and handled domain errors.
The original queued-run status reconciliation behavior remains; viewing is not
a new execution authorization.

This does not provide push revocation or transaction-wide atomic authorization.
Concurrent changes after the last check are not eliminated; already delivered
content cannot be retracted from clients. Frontend cleanup depends on its current
identity/organization/permission context updating, or an explicit reread.

Validation for this follow-up:

- `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 uv run --frozen pytest
  tests/integration/test_scene_delivery_evidence.py
  tests/integration/test_scene_inspection_execution.py
  tests/unit/scenes/test_acceptance.py tests/unit/scenes/test_acceptance_api.py -q`:
  **64 passed**. Uses existing network-denying fixtures, real local SQLite and
  deterministic transcript/HTTP substitutes; no real model or inspection target.
- Vitest: `RunEvidence.test.tsx`, `RunQueue.delivery.test.tsx`,
  `Organizations.planning.test.tsx`: **36 passed**, including the original scene
  page's denied reread and task-permission cleanup. jsdom emits known unsupported
  pseudo-element warnings, not evidence of browser E2E verification.
- `npm run build` passed with existing Vite chunk/import warnings. Local ASGI
  checks verified the evidence response/no-store contract, generated OpenAPI
  description and `/api/docs` HTML response; no visual browser check was performed.
- Initial static gate stopped on a new test's Prettier formatting; formatting was
  corrected and checked again. Final changed Python files passed Ruff.
- Full pytest / `make all` / testmon were not run: prior restricted telemetry tests
  were not retried or routed around. Real PostgreSQL, model, AKB, Mattermost,
  production deployment, browser E2E and release artifacts remain unverified.
  No new dependencies installed, no credentials read, no security/network settings
  changed, and no real messages, merges or deployments performed.
- Final `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all`
  passed: full Ruff, ESLint, Prettier, mypy (657 source files) and TypeScript.
  `git diff --check` passed.


## Follow-up: accepted deliveries remain discoverable (2026-11-15)

A human acceptance previously removed a successful run from all follow-up
categories. The new `accepted` category retains a discoverable route to that
same run, its report, human review and existing Mattermost publication preview.
Users explicitly select this category and refresh it; accepting a run does not
silently change the active filter or initiate another action.

The category requires both `r.status='succeeded'` and a matching organization/run
acceptance with `decision='accepted'`. A success alone is not human acceptance;
a mismatched organization or a failed/queued run with an inconsistent acceptance
is excluded. Ordering remains descending run insertion order, **not acceptance
time**. The existing bounded keyset cursor, no-store responses, manager checks
before/after reads and frontend session cleanup are unchanged.

An accepted record is historical, not renewed authorization or proof of delivery.
The separate content/review/publication endpoints continue to enforce current
access and binding checks. The queue only reads an existing publication; preparing
or approving/revoking local previews remains in the original scene-run view.
`blocked_not_connected` still means not sent. No scheduled workflow, automatic
acceptance, goal completion, AKB write or Mattermost sending is introduced.

Validation:

- `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 uv run --frozen pytest
  tests/unit/scenes/test_progress.py tests/integration/test_scene_inspection_execution.py
  tests/unit/i18n -q`: **137 passed**. Covers accepted keyset pagination under new
  insertions, cross-organization/inconsistent-state exclusion, current-role
  revocation, HTTP response schema, and an actual local scene execution plus
  human decision moving the run from unreviewed to accepted. HTTP collection uses
  the existing MockTransport and network-denying fixture, not a live endpoint.
- Vitest `runQueue.test.ts`, `RunQueue.test.tsx`, `RunQueue.delivery.test.tsx`,
  `RunDetails.test.tsx`: **18 passed**. Includes explicit human review, selecting
  accepted deliveries, reopening the same report and existing blocked preview,
  with assertions of no publication mutation or sending. API responses are
  mocked in UI tests; this is not real browser E2E or external integration.
- No new dependency or schema migration. No full pytest/testmon or restricted
  telemetry retry. Real models, AKB, Mattermost sending, PostgreSQL, browser E2E,
  production deployment and release artifacts remain unverified.
- `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all`
  passed (mypy 657 source files, Ruff, ESLint, Prettier and TypeScript).
  `npm run build` passed with existing chunk/import warnings; jsdom warnings
  remain in component tests. `git diff --check` passed.
- Local ASGI inspection returned `/api/docs` HTML 200 and verified the accepted
  query/response enums and endpoint description in OpenAPI; no real browser
  visual check or external request was made.

### 2026-11-15：逐操作来源回看

运行详情新增按需读取的[操作来源记录](operation-trace.md)，显示服务端记录的申请/审批/
验收账号和绑定执行事件。账号身份不等于本人点击；旧记录没有 UI/API 交互来源，因此明确
标为未记录。单次 Cron 不宣称周期自动化；本地发布批准也不宣称真实发送。

## 2026-11-16：找回排队与被拒绝的申请

基线 9420b510c327980c6acf24b77a2737a8e61cca05。原待办只列待审批、失败和交付验收
分类，管理进度已有 queued/rejected 计数，但关闭审批详情后不能从该入口找回对应运行。
现在增加 queued（已排队，状态可能滞后）和 rejected（申请已拒绝）两个显式分类，
复用当前组织授权、倒序游标分页和同 run 详情。既有分类和默认选择不变，无数据库迁移。

在组织页面选择对应分类并刷新，点击 run ID 打开原详情；已排队运行可以显式刷新当前
状态。只读队列本身不触发状态同步、派发、重试或审批。queued 不证明当前正在执行；
rejected 不代表任务执行失败，也不会通过查看而重新打开申请。需要新申请仍返回目标方案。
异常地带有验收记录的排队/拒绝运行不进入该正常分类，进展汇总的不一致记录口径保持不变。

前端验证分类与返回状态/decision 一致，切换分类清除详情；新分类打开不会读报告或调用
审批。服务端验证跨组织隔离、普通成员拒绝、禁用用户/撤权、状态变化和不一致验收排除。
ASGI 使用合成 SQLite，前端使用 jsdom/API 替身；不是实际机器或浏览器点击验收。

本轮验证（2026-11-16）：

- `uv run pytest tests/unit/scenes/test_progress.py tests/unit/i18n -q`：124 passed。
- `OCTOP_TEST_DELIVERY_HTTP=1 LANGFUSE_TRACING_ENABLED=false OTEL_SDK_DISABLED=true uv run pytest tests/integration/test_fresh_install_inspection_delivery.py -q`：4 passed；包含真实回环 HTTP、合成巡检、人工验收与正常停止/重启持久化回归。采集和 harness 为替身，不证明模型或堡垒机可用。
- Vitest 的 runQueue API、RunQueue、RunQueue.delivery、RunDetails、RunDetails.approval、OrganizationProgress 六个文件：43 passed。
- `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all` 和 `NODE_ENV=production NODE_OPTIONS=--max-old-space-size=2048 npm run build` 均通过；mypy 675 个源文件。既有 ESLint/jsdom/构建体积警告仍在，无本轮失败。
- 未运行全量 `make all` 或受限遥测测试；真实浏览器、PostgreSQL、模型、AKB、堡垒机、Mattermost 双服务及部署未验证。没有新增依赖、真实发送或恢复激活。

## 2026-11-16：验收后立即更新当前交付入口

基线 51f25df7244b4d730c994e2547d9bd7bf4fac753。复现：从未验收待办打开运行，
提交“要求修改”成功后，验收面板已显示成功，但父详情仍持有 decision=null，修订
入口不显示，旧待办也仍显示未验收；必须另行手动刷新。新增组件组合测试修复前失败。

现在仅在验收响应通过现有组织/run、决定、意见及证据哈希校验，且仍属于当前页面
会话时通知父详情。详情更新决定并清空旧待办列表；要求修改时立即显示既有修订入口，
原验收成功反馈保留。验收意见/发布预览的旧读取会话销毁，重新打开时按原权限重读。
不会额外提交验收、派发执行、自动创建修订或发送消息；其他运行元数据不冒充已刷新。

关闭、身份/权限变化导致会话失效后的迟到写入响应不通知新页面；403、409、网络
不确定结果仍按现有错误路径清理内容，不假定写入已成功。服务端现有验收事务、
权限与不可覆盖规则未改，用户可显式刷新核对实际状态。

真实 API 回归在合成巡检验收后检查：同 run 详情返回实际决定，运行从未验收分类
移到 accepted/changes_requested；原要求修改→新方案→新运行→独立审批→再验收路径
继续通过。UI 使用 jsdom/API 替身；API 使用临时 SQLite，包含真实回环 HTTP，
采集和模型 harness 为替身，不是堡垒机/模型/AKB/Mattermost 真实联调。

本轮验证：

- `uv run pytest tests/integration/test_fresh_install_inspection_delivery.py tests/unit/scenes/test_progress.py -q`：48 passed（`OCTOP_TEST_DELIVERY_HTTP=1`，遥测关闭，uv 离线缓存）。
- Vitest：DeliveryAcceptance、RunQueue.delivery、RunQueue、RunDetails、RunDetails.approval、InspectionRevision 六个文件，55 passed。包括关闭/撤权后迟到决定不通知父页面，403/409/不确定失败不更新决定。
- 未运行真实浏览器多角色点击、PG、真实外部服务、生产部署或受限遥测；无全量 make all 通过宣称。

本轮 `make lint-all typecheck-all` 与生产 `npm run build` 通过（mypy 676 个源文件）；
既有 ESLint、jsdom 和构建体积/导入警告保留。`git diff --check` 通过。

## 2026-11-16：按接收员工追踪人工安排的工作

基线 35487190a142f8a528933dbb5991eb36598017db。现有组织负责人/管理者已经可以
选择另一岗位数字员工并创建目标、方案，然后通过既有场景独立审批执行。此前待办
混列全部运行，无法按接收员工查找交付；本轮补齐这一追踪断点，不另造委派/执行引擎。

操作路径：在组织中为两个岗位分别任命 Agent、建立数字员工；负责人选择接收员工，
用已有入口创建并激活目标、准备方案并申请巡检，另一管理者独立审批和验收。随后
在组织待办的“接收任务的数字员工”选择接收员工，再选择待审批、失败、待验收或
已验收分类并刷新，打开同次运行查看报告、验收或返修入口。分派是负责人明确创建
目标的人工操作，created_by 记录实际账号，不表示协调员工自动调用了另一员工。

`GET /api/organizations/{org}/run-queue` 新增可选 `worker_id`，响应回显过滤范围。
通过当前持久化的 run.plan_id → plan.goal_id → goal.worker_id 关联，限制 worker
和 run 均属该组织；不依赖客户端自报执行主体或运行文本。读取前后重新检查当前
管理权限及员工归属。其他组织或不存在的员工返回 403。保留有界倒序游标分页。

没有方案、关联被删除或不一致而无法归属的运行只在“全部”视图查看；筛选不伪造
历史归属，也不改变现有全部视图。全组织管理进度数字不受员工筛选影响，界面明确
提示。默认不筛选，既有客户端兼容；新前端要求响应范围匹配，旧后端忽略参数时
显示读取失败而非展示混合结果。切换员工清除旧详情/游标，丢弃迟到响应；员工集合（ID/组织）
或权限上下文变化重新创建待办会话。

本轮不提供源员工→目标员工的依赖图、自动委派、目标所有权转移或自动汇总验收。
真实模型/堡垒机仍缺授权测试条件，采集与 harness 替身不算外部能力验收。未新增
数据库迁移、外部执行、自动重试或消息发送。

验证：后端与语言键 **133 passed**（progress、fresh-install、i18n），包括两个岗位
的临时员工、同次运行独立审批/验收、要求修改后新运行、失败重试后的原记录、
接收员工筛选、其他员工不混入、撤权拒绝、跨组织拒绝与分页。fresh-install 含
真实回环 HTTP，身份/JWT/SQLite 为实际实现；采集与模型 harness 为替身。前端
六个文件 **47 passed**（runQueue API、RunQueue、RunQueue.delivery、RunDetails、
RunDetails.approval、OrganizationProgress），覆盖忽略过滤范围的响应拒绝、错误
后显式重试和切换员工后迟到结果丢弃；为 jsdom，不是浏览器多角色验收。

本轮 `make lint-all typecheck-all` 和生产 `npm run build` 通过（mypy 676 个源文件），
既有 ESLint/jsdom/构建警告保留，diff 检查通过。未运行完整 make all、受限遥测、
真实 PG、浏览器或外部系统验收，没有部署与真实外发。
