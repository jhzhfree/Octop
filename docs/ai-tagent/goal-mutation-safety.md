# 目标终态与草稿并发写入保护

2026-11-15，开发分支 `feature/akb-octop-litellm-dev`，开始时本地与远端均为
`c89ff7c1e458fc52f5b1371bbcb85388f7be48f8`，工作区干净。本阶段未新增依赖或迁移。

## 确认的问题和修复

原服务在读取目标后检查状态，但 UPDATE/DELETE 仅按 goal_id 执行。并发请求可在检查后
分别完成/取消同一目标，或在另一请求激活目标后仍编辑/删除草稿；审计写入也在独立事务。

现在服务把已验证的服务端目标快照交给数据库条件写入。条件比较目标/员工 ID、状态、
标题、说明、成功标准和可空周期字段，不依赖秒级 updated_at 判断是否发生变化。
写入语句同时检查持久化账号未禁用、当前组织管理资格或当前管理员身份。
目标变更和账号审计在同一个事务；审计失败则一起回滚。只有获胜写入记录成功审计，
冲突不会覆盖已完成的结果或再写一条成功日志。审计主体仍由认证账号提供，不接受请求体
自报 actor，不推断本人点击或自动触发模式。

已有目标 PATCH/DELETE URL、请求与响应保持兼容。服务校验之后目标改变则返回 409；
写入前权限撤销返回 403。用户应重新加载目标、核对当前状态和标准后再决定，不能自动
重试覆盖。正常草稿编辑、激活、完成、取消、草稿删除仍遵守原状态机；完成目标仍不代表
自动验收交付、停止在途运行或发送 Mattermost 消息。

## 精确边界（本阶段历史记录）

后续[目标版本契约](goal-versions.md)已补上客户端版本、删除前置条件和界面冲突恢复。
下列无版本限制描述仅适用于提交 `1e6f6843`，不是最新接口约定。

这是**服务端读取到写入之间**的竞态保护，没有增加客户端版本/ETag 协议。
旧页面在请求到达前已经过期，服务端可能读取到更新后的草稿；该请求仍没有表达用户
所见的旧版本，因此不宣称解决了所有“旧页面覆盖”情形。相同数据被改动后又恢复的 ABA
情形也不具备版本历史检测能力。后续需要客户端版本与目标审批快照契约。

低层无条件仓储方法仍用于既有初始化/测试夹具；当前业务服务的目标更新/删除已统一走
条件写入。没有改动工作计划、员工档案删除或其并发规则。PostgreSQL 使用兼容的条件
SQL，但本轮未连接真实 PostgreSQL，不能把 SQLite 的多连接测试当作其并发验收。

远程撤权后已打开页面仍需会话通知/主动重新验证机制：前端当前仅在感知权限变化、
重读或关闭时清理，不会自动实时清屏。此缺口已加入发布就绪度，未扩大本轮实现范围。

## 验证方式

- 使用迁移后的临时 SQLite 和两个独立数据库连接，屏障同步竞争完成/取消及重复完成。
- 服务级注入竞争激活/终态，检查失效草稿写入/删除返回冲突且不破坏获胜状态。
- 同秒修改成功标准后激活旧快照必须冲突；覆盖可空周期与正常状态生命周期。
- 审计 INSERT 故障注入，确认更新和删除回滚；禁用、成员撤销/降级、旧管理员声明及
  跨组织写入被拒绝；实际管理员无成员关系仍可按原规则操作。
- 本地 ASGI 验证 PATCH/DELETE 结果、409、服务端 actor 与 OpenAPI 描述；未启动真实
  Octop 服务、读取真实凭据、访问生产或外发消息。

最终命令结果见下方记录。受限遥测测试、全量 `make all`/testmon 不运行，也不以替代方式
重试。真实模型/AKB/Mattermost、目标 PostgreSQL、部署隔离、制品恢复、浏览器验收仍未完成；
整体仍不满足 1.0 发布条件。

### 本阶段结果

以下 Python 命令均设置 `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1`，使用现有依赖。

- `uv run pytest tests/unit/digital_workers/test_goal_mutation.py tests/unit/digital_workers/test_readiness.py tests/unit/digital_workers/test_planning.py tests/unit/digital_workers/test_memory_repo.py -q`：67 passed。
- `uv run pytest tests/integration/test_scene_execution_offline.py tests/integration/test_scene_inspection_execution.py -q`：34 passed；模型/HTTP 来源仍是离线替身，不是真实联调。
- `npm test -- src/pages/Organizations/GoalStatusAction.test.tsx`：6 passed；存在既有 jsdom 提示。
- `make lint-all typecheck-all`：通过，Ruff/Prettier/ESLint/mypy/TypeScript 全部完成；
  ESLint 69 warnings、0 errors，mypy 658 个源文件无错误。
- 最终无失败或跳过的上述定向测试。前端源码未变，本轮不重跑前端生产构建；上阶段构建
  结果不作为本阶段新增证据。全量门禁及受限遥测测试未运行。
- Docker daemon 29.9.2 可连接；bubblewrap 0.12.0 已安装。本轮仅查询版本；此前实际
  bubblewrap 隔离受只读 uid_map 限制，未改变安全/网络设置，也未宣称应用隔离通过。
