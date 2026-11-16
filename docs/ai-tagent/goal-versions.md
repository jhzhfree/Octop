# 目标版本契约与旧页面冲突恢复

2026-11-15；分支 `feature/akb-octop-litellm-dev`；开始时本地和远端均为
`1e6f684357b02b510310299740f86daa5fcd49db`，工作区干净。

## 版本和写入前置条件

目标列表和创建响应增加 `version`。迁移 042 为现存目标回填 1；SQLite INTEGER、
PostgreSQL BIGINT 均非空、默认 1、约束大于等于 1。现有其他字段不变。

- PATCH 原目标 URL：请求体必须有 `expected_version`，值取用户当前看到的目标版本。
- DELETE 原目标 URL：必须带 `?expected_version=...`，仍只允许删除草稿。
- 缺少版本（PATCH 显式 null 也一样）返回 428 `GOAL_VERSION_REQUIRED`，不执行变更。
  不接受“以最新版本代填”的兼容回退；PATCH 布尔、字符串、非正数版本返回 422。
- 服务层强制接收版本并比较；数据库再次以版本及此前校验的字段快照作为写入条件。
  过期版本/并发变化返回 409，沿用 `DIGITAL_WORKER_GOAL_STATUS_INVALID` 错误码。
- 带更新字段的成功写入递增版本；即使将内容恢复原样，旧版本也不能重新有效。
  空 PATCH 仍须有效版本，作为既有无字段操作，不递增版本。删除没有下一版本。
- 变更、版本递增和账号审计同事务；失败回滚。审计增加 from_version/to_version，
  仍使用已认证账号，不读取客户端自报 actor。当前账号/组织写权限检查保持有效。

## 界面恢复

现有目标激活/完成按钮发送所见版本。确认框在版本改变时销毁，即使秒级更新时间没变。
操作失败后停止重试，提供“重新加载目标”；成功重读后关闭旧确认，显示当前状态/标准，
用户必须重新确认才能再次提交。若目标已终态，操作入口按原规则消失；删除的目标不再保留
为当前选择。重读失败仍保留失败状态，不自动写入；组织切换后的迟到结果被丢弃。
该策略也适用于结果未知的网络错误，避免自动重试覆盖。

当前界面原本没有目标删除按钮，本轮不新增删除入口；已有 DELETE API 同样受版本保护。
外部集成须把读到的版本传回，收到冲突后重新展示并确认，不能盲目重试。

## 兼容与部署边界

保留 URL、读取/创建行为及旧响应字段；旧客户端读取和创建可继续工作。
**旧客户端的无版本更新/删除不再成功**，必须更新客户端。这是有意的安全拒绝，无法同时
保证无条件旧写入兼容和旧页面不覆盖，不宣称完全透明兼容。

迁移是加列，但旧服务二进制不会执行新版本协议。升级应在获准维护窗口停止/排空旧写入
进程，再迁移并整体切换服务和客户端，避免旧新写入器混跑；直接回退旧二进制会失去保护。
本轮未执行部署、回退或生产迁移；这些步骤需独立授权和演练。

SQLite 测试覆盖空库创建、含目标数据的 v41 结构升级、重复启动及独立连接竞争。
PostgreSQL 提供配对迁移及 SQL 静态核对，**未完成真实 PostgreSQL 升级、并发或回滚验收**。
禁止把 SQLite 结果当作 PG 证据。低层仓储更新也递增版本；手工 SQL/旧二进制绕过协议
不在保障内，不能作为受支持的业务写入路径。

远程撤权实时清屏、真实模型/AKB/Mattermost、浏览器真实交互、制品安装与恢复仍待验证。
当前整体未达到 1.0。本轮无真实外发、生产访问、合并或部署，无新增开发依赖。

## 本阶段验证记录

Python 命令统一使用 `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1`。

- `uv run pytest tests/unit/digital_workers/test_goal_mutation.py tests/unit/digital_workers/test_readiness.py tests/unit/digital_workers/test_planning.py tests/unit/digital_workers/test_memory_repo.py tests/unit/db/test_migrate_discovery.py tests/unit/i18n tests/integration/test_scene_execution_offline.py tests/integration/test_scene_inspection_execution.py -q`：185 passed。
- `npm test -- src/pages/Organizations/GoalStatusAction.test.tsx src/pages/Organizations/planReadiness.test.tsx`：13 passed；`npm test -- src/pages/Organizations/Organizations.planning.test.tsx`：21 passed。共 34 项，不将重复执行计为新增测试。
- `make lint-all typecheck-all` 与 `npm run build`：最终均通过；mypy 658 个文件无错误。
  最后两项页面用例定向重跑 2 passed，另 19 项由名称过滤未执行（此前完整文件 21 passed），不重复计数。
  静态检查保留现有 ESLint warnings；前端测试有既有 jsdom/act 提示，构建有 chunk 提示。
- 收紧服务层必传版本时，mypy 暴露可空字段在闭包中的类型错误；修正为先读取并检查局部
  版本变量后通过。一次定向文本替换误及相邻员工更新参数，被 Ruff 立即发现并恢复。
  最终 Python 类型检查通过，相关行为由上述 HTTP 和业务回归验证。
- 既有三份完整启动场景测试的请求夹具同步版本契约，但本轮没有执行
  `test_digital_workers_api.py`、`test_enterprise_scenes_api.py`、
  `test_fresh_install_inspection_delivery.py`；不把夹具更新当作这些场景通过。
- 未运行全量 `make all`、testmon、受限遥测测试；未进行真实 PG/模型/AKB/Mattermost、
  生产或部署访问。没有读取真实凭据或修改 OS/网络/安全设置。
- Docker daemon 29.9.2 可连接，bubblewrap 0.12.0 已安装；仅查询版本。此前实际隔离
  探测受只读 uid_map 限制，本轮未重测，未宣称隔离可用。

## 2026-11-16 目标收尾复核

基线 8a3371a3。重新执行目标完成/取消、权限变化、旧页面版本冲突、并发 CAS 与
审计失败回滚测试，发现历史迁移测试还用“删 version 列，再将当前库水位改成 41”
构造旧库，被 schema 49 的降级保护正确拒绝；另有固定基线 42 的过时断言。
这不是目标操作新缺陷，不能移除保护来让测试通过。

测试现改为只应用真实 001–041 可发现迁移（包含仓库已有历史迁移辅助逻辑），在
临时库中复制合成关联数据的旧版列，检查旧目标没有 version；升级到当前版本后
数据保持且 version=1，再次迁移无变化并检查 integrity_check。基线从迁移目录读取，
SQLite/PG 文件版本相等仅是文件检查，不代表 PG 执行验证。

最终 `uv run pytest tests/unit/digital_workers/test_goal_mutation.py -q` 为 **17 passed**，
前端 `GoalStatusAction.test.tsx` 为 **8 passed**；测试文件 Ruff 与 diff 检查通过。
原失败为过时夹具，已修正。正常目标完成与异常恢复未发现需要新增功能代码的问题。
后端含真实 SQLite 跨连接竞争与 ASGI 请求，但认证依赖由测试替身提供；前端为
jsdom，没有真实浏览器多角色验收。真实模型、堡垒机、AKB、Mattermost 与部署仍待
授权测试条件，不能凭本轮测试声明企业 1.0 完整验收。
