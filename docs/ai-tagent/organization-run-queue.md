# Octop 组织运行待办里程碑

## 功能

组织页增加只读运行待办，组织所有者、管理员及平台管理员可选择四类：待审批、执行失败、待人工验收、需修改交付。每行显示运行编号、分类和服务器时区的创建时间，页面显示读取时间及下一步说明。默认每页 20 条，可刷新首页或查看下一页。此版本提供定位编号与操作指导，不提供直接跳转、批量审批或一键重试。

接口：`GET /api/organizations/{organization_id}/run-queue?category=unreviewed_success&limit=20`。后续页携带返回的 `next_before`。接口允许 1–50 条，使用数据库内部顺序游标，不使用 offset，不返回提示词、失败正文、交付证据、凭据或申请人身份。

## 状态语义

- `awaiting_approval`：运行待审批，且没有验收记录。
- `failed`：执行失败，且没有验收记录。
- `unreviewed_success`：执行成功，但没有人工验收记录。
- `changes_requested`：执行成功，但人工要求修改。

验收通过、排队中、审批拒绝不属于此待办。异常组合不强行归类，可在组织进展汇总中查看异常计数。运行成功不等于目标完成。本接口不写数据库，不自动启动任务、重试、审批、验收或写入知识库。

## 分页与权限

按运行写入顺序倒序，游标严格向旧记录移动。新记录不会挤动后续页，刷新首页才能看新记录。多页不是固定快照，状态变化可能使运行移入或移出筛选结果。每次读取前后复查当前数据库权限；这不是事务级原子撤权保证。SQL 始终限定组织，验收关联同时限定组织。

UI 切换组织、权限或分类会清空旧结果并丢弃旧响应；重复点击不会重复提交请求。数据只通过现有 typed API 模块获取，并要求 `Cache-Control: no-store`。

## 验证与边界

后端针对性测试覆盖分类、跨组织隔离、权限撤销、禁用管理员、keyset 分页及插入稳定性、参数和 HTTP 契约。组件测试覆盖读取权限、分页、错误组织响应、重复点击及切换上下文的迟到响应。

本轮仅验证本地 SQLite 与组件逻辑，不宣称真实调度、外部模型、AKB 或 PostgreSQL 已联调，也不宣称浏览器端到端验收完成。全量 `make all` 受既有测试遥测外联限制，未运行，不得按定向测试通过标为全量通过。

后续产品项：目标完成确认、目标选中状态同步、待办直接定位到场景运行详情。当前不为这些后续项扩大权限或启用外部能力。

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
