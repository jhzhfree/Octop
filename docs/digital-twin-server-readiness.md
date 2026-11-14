# 服务端计划准备度观察（第 3 轮领域能力）

## 状态与边界

`WorkerPlanReadinessService.inspect` 是只读的服务端观察，不是运行授权、审批、预算保证或知识授权。当前阶段新增独立领域模块与测试，尚未接 HTTP 或前端；原前端已加载数据提示仍独立存在。

调用方必须提供组织、员工、目标、计划 ID，以及期望的员工版本和计划版本。服务检查组织 owner/manager（系统管理员沿用既有权限语义），依次验证资源归属及 Agent 访问权。普通成员、组织外用户、跨组织员工、错误目标或计划不能获得报告；版本不匹配返回既有冲突错误。

## 报告契约

返回 `organization_id / worker_id / goal_id / plan_id`、员工版本、能力版本、计划版本、目标更新时间、服务端观察时间和 checks。checks 仅使用稳定机器码和 `satisfied / unmet / unknown` 三态，未来 HTTP/UI 适配应为这些机器码提供中英文显示文案，不应将机器码直接当用户提示。

本地检查：

- `worker_active`：员工激活。
- `goal_active`：目标激活。
- `plan_ready`：计划处于已有 ready 状态，仅表示计划状态。
- `plan_content_complete`：标题、方法、目标成功标准、步骤及预期产出非空。
- `agent_enabled`：Agent 启用。
- `agent_appointed`：Agent 仍任命于对应岗位。
- `matching_scene_enabled`：同组织、岗位和 Agent 存在启用场景，仅存在性检查，不检查运行配置完整性。

始终为 unknown：`runtime_approval`、`knowledge_user_acl`、`model_budget`。报告始终返回 `execution_authorized=false`、`scope=local_observation_not_atomic`。即使本地检查全部 satisfied，也不能展示成“可执行”或“已获权限”。

## 并发和查询约束

服务返回前再次读取并比较快照，观察到员工、目标、计划、任命、Agent 状态、匹配场景存在性变化时拒绝返回旧报告；若组织/Agent 访问权被撤销则拒绝。两次读取不是跨资源事务快照，不保证捕获 ABA 变化，也不是权限租约。报告之后仍可立即过期，真正执行必须沿用现有场景审批与执行前重新鉴权。

场景使用限定组织、岗位、Agent 的 `SELECT 1 ... LIMIT 1`，不拉取全部场景、不读取提示词、不逐场景 N+1。查询次数与场景数量无关，但数据库实际扫描成本仍取决于索引与数据规模，不作常量耗时承诺。报告不泄露场景提示词、模型密钥、知识片段或私有 Agent 配置。

## 副作用与验证

无模型调用、知识检索、计划保存、审批写入、调度创建或审计写入。真实 SQLite 测试断言数据库 total_changes 不变。

定向验证：19 项 SQLite 测试覆盖权限、跨组织、版本冲突、本地失效项、观察期间变更、场景范围及只读属性；Ruff、mypy 通过。未运行受阻全量测试、真实 PostgreSQL、外部模型或 AKB，也未完成 HTTP/浏览器验收。

## HTTP 与界面接入（独立文件已实现，需集成）

新路由 `worker_plan_readiness.router` 提供 GET
`/organizations/{organization_id}/digital-workers/{worker_id}/goals/{goal_id}/plans/{plan_id}/readiness`。
必须传 `expected_worker_version` 和 `expected_plan_version`（正整数）。返回 403 权限不足、404 资源链不匹配、409 版本/观察期间变化、422 参数缺失或无效、503 服务不可用。成功与领域错误均 `Cache-Control: no-store`。错误只返回稳定 `detail.code`，避免泄漏内部内容，由消费端本地化。

在 `app.py` 的 routers 导入列表添加 `worker_plan_readiness`，然后紧邻现有 `worker_knowledge_mappings` 挂载添加：

```python
_RouterMount(worker_plan_readiness.router, "/api", ["virtual-workers"]),
```

如果现有父 router 已包含 `/api`，应注册到该 router，不能重复前缀。不替换认证、setup 或组织权限中间件。

在计划卡片（已有 worker / goal / plan 的渲染作用域）添加：

```tsx
import ServerPlanReadiness from "./ServerPlanReadiness";

<ServerPlanReadiness target={{
  organizationId: worker.organization_id,
  workerId: worker.worker_id,
  goalId: goal.goal_id,
  planId: plan.plan_id,
  workerVersion: worker.version,
  planVersion: plan.version,
}} />
```

此面板独立于现有本地 PlanReadiness，保留原历史记录展示。不自动轮询，点击时读取；重新请求即清除旧报告，失败不回退旧成功结果；切换目标/版本忽略旧异步响应。组件自带中英默认文案，可由 `serverPlanReadiness.*` 翻译覆盖。未知项始终明确显示未验证，不展示执行按钮。即便观察通过也不能替代运行端的权限、审批、预算和 ACL 重验。

新增定向验证：`tests/unit/digital_workers/test_readiness_api.py`（真实 SQLite + 最小 FastAPI 挂载）覆盖权限、跨组织、版本冲突、参数、未知/无授权及服务不可用；`ServerPlanReadiness.test.tsx` 覆盖未知、权限/冲突清旧、版本响应不符和切换期间旧请求。

### 审查补强

非管理员先检查组织成员管理角色，再读取组织，非成员/普通成员对已有和不存在组织均返回相同 403 code，不泄露组织存在性。任职检查使用带组织、岗位、agent 约束的 `SELECT 1 … LIMIT 1`，不再继承全组织任职列表读取；场景和任职都只返回单条存在性结果，双读仍不构成原子授权。SQL 实际扫描成本由数据库索引/执行计划决定，不宣称固定时间。
