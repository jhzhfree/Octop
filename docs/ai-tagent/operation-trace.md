# 组织运行的操作来源记录

2026-11-15；开发分支 `feature/akb-octop-litellm-dev`，开始核验的远端基线
`fb87ea8015678ce10704cee265da07b6a7e7b78e`。这是本地可验证的增量，尚非 1.0 发布验收。

## 用户入口与事实边界

组织待办 → 同一个运行详情 → “查看操作来源记录”。申请、批准/拒绝运行、交付验收决定、
发布预览创建/本地批准/撤销、绑定执行成功/失败，均来自已有服务端 audit_log。
按插入顺序倒序展示最多 100 个候选记录，不拼造缺失事件，不返回原始 payload、错误、
报告内容、提示词或账号名称。无需迁移；旧审计缺失时明确显示不可据此判断未发生操作。

- 数字 actor 是服务端写入的账号 ID；它不证明本人点击，UI/API 交互方式显示“未记录”。
- 只有绑定 Cron 的 `_system` 执行审计标为“系统服务执行”。运行批准者不是执行主体。
- Agent、代表账号、执行器、单次任务是当前绑定的配置上下文，不是新增历史审计事实。
  绑定须匹配 Agent、申请账号和 Cron 线程；失配时不展示 Cron 事件或推测执行器。
- `date:` 调度仅标为单次任务；其他调度来源标为未知，不推断周期自动化或自动触发来源。
- 本地发布预览批准不代表 Mattermost 发送成功。该页面无执行、审批或外发操作。
- 审计有保留期，部分旧操作与审计不是同一事务；`history_complete=false` 永远明确。
  当前版本不是完整的不可篡改审计系统，也未补录调用渠道、每次执行触发者或自动策略身份。

## 权限和兼容性

GET `/api/organizations/{organization_id}/scene-runs/{run_id}/operation-trace`：
复查当前账号禁用状态、tasks 权限、组织资格和运行参与者/管理员权限；读取前后均检查，
丢弃期间变化的数据。响应及权限错误 `Cache-Control: no-store`。读取不修正运行状态。
报告读取复用同一当前身份检查，保留其既有状态协调语义。

前端只有点击后读取；重读先清空旧数据。关闭、组织/运行/身份/权限改变后丢弃延迟响应。
响应须通过组织/run、事件类型、主体类型及只读声明校验。网络错误不显示原始异常。
权限变化的清理只在前端收到 props 更新、重读或关闭时发生。当前 AuthGuard 不会向已
打开页面实时推送远程撤权；服务端重验不能收回已显示数据，实时清屏仍是 1.0 验收缺口。
记录页和既有报告、验收、发布预览并存，待审批/失败运行也可由获准参与者读取记录。

## 验证与限制

本轮使用现有已安装依赖，无新增下载/安装来源。后端 SQLite、Cron、审计和鉴权实际运行；
模型为固定输出替身，HTTP 巡检为 MockTransport；socket 外联被定向测试夹具禁止。
这不是模型、AKB 或 Mattermost 真实联调。

门禁命令与最终结果记录在本次提交及交付说明中。`make all` 包含全量 `pytest -m "not live"`，
该选择器并不保证无外联；遵照限制没有运行全量、testmon 或曾被拒绝的遥测相关测试。
仅运行明确列出的离线定向测试及静态检查、前端构建。

当前环境 Docker daemon 可连接（29.9.2），bubblewrap 二进制为 0.12.0；此前实际隔离探测
受只读 uid_map 限制。本轮仅检查版本，不修改 OS/网络/安全配置，不宣称应用隔离通过。
尚需受信任入口的逐次调用渠道/触发身份记录、真实集成、PostgreSQL 并发迁移、浏览器验收、
制品安装与恢复等证据，详见[发布就绪度](../release-readiness.md)。

### 本阶段实测结果

- `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 uv run pytest tests/integration/test_scene_operation_trace.py tests/integration/test_scene_delivery_evidence.py tests/integration/test_scene_execution_offline.py tests/integration/test_scene_inspection_execution.py tests/unit/scenes -q`：176 passed。
  覆盖真实离线申请/批准/执行/验收审计、当前授权与撤权、组织隔离、绑定失配、限量脱敏、
  旧历史缺失、只读行为、HTTP/OpenAPI 与原报告/巡检/发布预览逻辑。
- `npm test -- src/pages/Organizations/RunOperationTrace.test.tsx src/pages/Organizations/RunDetails.test.tsx src/pages/Organizations/RunQueue.delivery.test.tsx src/pages/Organizations/RunEvidence.test.tsx`：28 passed；
  追加待办入口断言后该文件重跑 8 passed；文案迁入语言包后上述四文件重跑仍为 28 passed（不重复计数）。
- `uv run pytest tests/unit/i18n -q`（同样离线 uv 环境）：80 passed。
- `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all`：通过；
  ESLint 0 errors / 69 warnings；Ruff、Prettier、mypy（658 文件）、TypeScript 通过。
  后续 Python 小改再次 Ruff/格式/mypy 验证。
- `npm run build`：通过；存在构建 chunk 大小提示。测试有既有 jsdom 伪元素和 React act 提示。
- 首轮新增测试有两处测试数据构造错误（外键引用不存在的 Agent、误用 Cron 存储列名），
  已修正，最终上述后端回归无失败。无未解决的本阶段测试失败。
- 未运行：`make all`、全量后端/testmon/受限遥测测试、真实浏览器交互、真实模型/AKB/
  Mattermost、目标 PostgreSQL/生产/部署/合并。OpenAPI/文档路由用本地 ASGI 验证，未打开远端文档 UI。
