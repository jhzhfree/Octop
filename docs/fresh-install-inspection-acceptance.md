# 空库到只读巡检交付：基础版本 HTTP 验收

## 范围与证据边界

自动化入口：`tests/integration/test_fresh_install_inspection_delivery.py`。

这是进程内真实 FastAPI ASGI + 全新 SQLite 的集成验收，不是浏览器验收、
真实模型联调或真实企业资产验收。测试不直接 seed 用户、组织、员工、目标、
计划、运行成功状态、报告或人工验收记录，所有业务写入都走产品 HTTP API。
它只通过 repository **读取**运行后的持久报告，并与 HTTP 暴露的证据核对。

- `octop_client` 替代 LLM harness；额外禁止 model stream，确保确定性巡检不调用模型。
- HTTP 巡检目标为 `https://fixture.invalid/status`，仅由显式 `httpx.MockTransport`
  返回 `status=healthy, disk_percent=45`。这不是任何真实资产的健康结论。
- Socket connect/connect_ex/sendto 与 DNS 查询禁止；遥测通过
  `LANGFUSE_TRACING_ENABLED=false`、`OTEL_SDK_DISABLED=true` 禁用。
- 关闭无关 proactive 调度；场景审批触发的 Cron、Gateway、证据保存与读取仍为真实实现。
- Provider 配置使用测试占位 key，通过管理员 API 创建，仅用于智能体配置验证，不调用外部服务。
- 不运行系统安装、不绑定 TCP 服务、不向企业微信/邮件等外部渠道发送内容。

## 可重复执行

在已安装项目依赖的 Octop 工作区运行：

```sh
LANGFUSE_TRACING_ENABLED=false OTEL_SDK_DISABLED=true \
  uv run --offline --no-sync pytest -q tests/integration/test_fresh_install_inspection_delivery.py
```

需要现成的 uv 环境。该命令不安装依赖；缓存目录如不可写，可设置 `UV_CACHE_DIR`。
临时 HOME 与数据库由 pytest 隔离，不使用已有运行环境数据。

## 实际 HTTP 流程

下列路径均带 `/api` 前缀；`{org}`、`{worker}` 等由创建响应返回。

1. `GET /setup/status` 确认未绑定数据库；读取该临时 HOME 的向导密码文件，
   `POST /setup/verify-password`，`POST /setup/database` 绑定新的 SQLite。
2. `POST /setup/initial-admin`，`POST /setup/finish`，`POST /auth/login`。
3. 管理员 `POST /users` 创建普通审批用户；审批用户独立登录。
4. `POST /organizations`，增加 manager 成员，创建岗位，管理员 API 配置测试 provider、
   创建智能体，再用岗位 `appointments` 绑定智能体。
5. `POST /organizations/{org}/digital-workers`，PATCH active；创建目标并 PATCH active；
   创建含步骤与预期输出的计划，使用版本号 PATCH ready。
6. 创建场景；管理员启用 enterprise-assistant inspection/reports，并只允许 fixture origin；
   创建当前用户拥有的 inspection connection 和 Agent-bound job。
7. 场景 `/runs` 传入 ready plan、inspection job、显式共享结果，关闭 memory 使用/捕获。
8. 请求人自批必须返回 403；组织内独立 manager 审批，真实 Cron 执行完成为 succeeded。
9. `/inspection-result` 检查来源 run id 与计数；验收 review 获取同一持久报告及两个哈希，
   独立审批用户提交 accepted，再读取验收记录确认保存。
10. `/organizations/{org}/progress?start=<epoch秒>&end=<epoch秒>` 获取有界窗口的进度。
    start/end 为必需参数，end 为不含端点。

## 验收断言

- Cron `last_status=ok`，场景 run `succeeded`。
- HTTP 只发生一次 MockTransport 请求，目标严格匹配 fixture URL。
- 巡检 sources=1、attempted=1、succeeded=1、failed=0、unknown=0、health_unknown=0；
  reports_persisted=1、scene_bound=1、not_started=0。
- HTTP review 文本等于 repository 读取的持久报告；报告包含 45 指标，内容哈希匹配。
- 初次 review 无验收记录；accepted 后 reviewer 是独立审批用户，证据哈希不变。
- 进度 denominator=1，execution.succeeded=1，其余执行状态为0；
  delivery.accepted=1，unreviewed_success=0，changes_requested=0。
- 进度明确 `external_systems_verified=false`、`execution_authorized=false`，
  不把历史数据库计数作为外部资产核验或下一次运行授权。

## 尚不能据此宣布的能力

此项只证明新安装后的只读 HTTP 巡检闭环在受控替身下可用。
真实模型网关、AKB 知识检索、外部连接器、生产身份源、定时跨重启运行、
真实浏览器交互及企业生产环境稳定性仍须各自联调与验收。
