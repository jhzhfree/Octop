# 人工交付验收 API 契约

独立路由已实现，使用现有 current_user/get_server 依赖。挂载到主应用后方可通过 `/api` 使用；本轮不改 app.py/UI。

## 主应用整合片段

在 `src/octop/api/app.py` 的 router 导入列表加入 `scene_acceptance`，并在现有企业场景挂载附近加入：

```python
_RouterMount(scene_acceptance.router, "/api", ["scene-acceptance"]),
```

先应用 034 迁移。仓储直接使用 `server.services.db`，无需 RepoBundle 新字段。

## 阅读摘要

`GET /api/organizations/{organization_id}/scene-runs/{run_id}/acceptance/review`

200 JSON：

```json
{
  "run_id": "...",
  "answer": "服务端从隔离运行会话提取的最后可见答案",
  "evidence_sha256": "64位小写十六进制",
  "evidence_length": 123,
  "snapshot_sha256": "64位小写十六进制",
  "acceptance": null
}
```

仅授权的独立审核者可读，服务端异步读取前后复核当前身份。`acceptance=null` 表示尚未人工验收，不能把 succeeded 显示成 accepted。已审核时 acceptance 返回下述决定记录。200 设置 `Cache-Control: no-store`。

## 提交人工决定

`POST /api/organizations/{organization_id}/scene-runs/{run_id}/acceptance`

```json
{
  "decision": "accepted",
  "expected_evidence_sha256": "GET返回的哈希",
  "expected_snapshot_sha256": "GET返回的哈希"
}
```

decision 仅允许 accepted / changes_requested。禁止额外字段；不接收答案正文、reviewer_id、权限声明、自由备注、完成度。reviewer 来自当前认证用户。哈希为精确 64 位小写十六进制。服务端重新提取证据，禁止客户端正文作为证据源。

200 返回 run_id、organization_id、decision、reviewer_id、reviewed_at（Unix 秒）、evidence_sha256、evidence_length、snapshot_sha256、scene_version。无答案正文。相同决定/审核人/证据重试幂等；已保存决定不可覆盖。

## 错误与 UI 行为

- 403：当前角色/证据权限不足、自审、用户禁用或撤权。
- 404：run 不存在或不属于该组织。
- 409：未执行成功、没有证据、证据/快照已改变、已提交不同决定。应刷新 review，不能自动更换哈希重试用户决定。
- 422：字段缺失、非法决定/哈希、任何多余字段。
- 503：数据库尚未就绪。

领域错误形如 `{"detail":{"code":"ACCEPTANCE_STALE","message":"..."}}`；422 使用框架标准校验错误。

UI 展示“交付摘要人工验收”，提示摘要可能被截断，不代表全部产物验证。由用户阅读后明确点击“验收通过”或“要求修改”；不要由模型/后台调度器自动提交。确认时绑定用户阅读的两个哈希。通过后显示审核人和时间，不把运行执行状态、业务目标完成度一起改成完成。changes_requested 不自动重跑。

## 验证边界

定向 API 测试使用最小 FastAPI + HTTPX ASGI、真实 SQLite 验收事务、替换为离线证据源；覆盖服务端取证、幂等、冲突、禁止额外字段/伪造权限、哈希过期、自审、无证据权限管理者、跨组织拒绝。当前认证依赖在测试中覆盖为固定身份，完整登录/主应用挂载须在整合后另测。不涉及外部服务、模型或遥测。
