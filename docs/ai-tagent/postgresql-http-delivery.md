# PostgreSQL 回环 HTTP 企业巡检验收（2026-11-16）

本轮从 `da97359c7bcd62301ab14e7e95f77818bd7752f0` 核对分支，
将既有新安装交付测试用于真实 PostgreSQL 17.12，复用完整应用服务器与 HTTP 路由。
它不是仓储单测，也不是生产或真实外部系统验收。

## 环境、身份及权限

使用 [PostgreSQL 交接验证](goal-handoffs-postgresql.md) 中相同固定 digest 官方镜像。
rootless Docker，非 root postgres 用户、network none、只读根文件系统、cap-drop ALL、
no-new-privileges，PGDATA 为 tmpfs；仅通过 `/tmp/octop-pg-delivery/socket` 的 Unix socket 访问。
没有开放 PG TCP 端口、挂载业务库、使用真实凭据或修改主机安全/网络设置。

测试明确要求 `OCTOP_TEST_PG_DELIVERY_ADMIN` 和 `OCTOP_TEST_DATABASE_DISPOSABLE=1`。
夹具拒绝 TCP/远程连接，仅允许本地 Unix socket；管理员只创建随机独立空白库和登录角色。
应用使用该库 owner，实查 `rolsuper/rolcreatedb/rolcreaterole` 全为 false。
这是数据库 owner 的 DDL/DML 权限验证，不是自定义最小表权限、只读数据库或迁移/运行双账号验证。
结束只移除本测试随机数据库与角色，不重置既有 public schema 或业务库。

Octop API 服务由 Uvicorn 在随机回环端口运行，客户端为真实 HTTP。
应用侧测试网络拦截只放行此端口，属于 Python 测试约束，不是 OS 网络沙箱。
libpq 使用上面的本地 socket，不能把 Python socket 拦截说成能约束原生 libpq 网络。

## 逐步验收路径

| 阶段 | 实际验证 | 边界 |
| --- | --- | --- |
| 初次启动 | 临时 HOME、未绑定数据库、health 区分 db=false | 干净安装，不是已有生产库升级 |
| 初始化 | 向导密码校验、POST setup/database、应用自行迁移绑定 PG、health db=true | 使用合法的 PostgreSQL Unix-socket URI |
| 管理员与登录 | initial-admin、finish、真实登录 JWT | 合成管理员密码，无真实账号 |
| 独立角色 | API 创建 reviewer 普通用户，加入组织 manager，单独登录 | 人类应用角色与 DB owner 是两个层次 |
| 员工/岗位 | 两个 Agent、负责人/运维岗位、任命、员工激活 | Agent harness 是测试替身 |
| 交接 | 源目标激活、交接理由、接收草稿、同键重放、来源查询 | 没有自动执行或外发 |
| 任务计划 | 接收目标激活、计划编辑 ready、场景/巡检任务配置 | 提供假模型配置，模型调用明确禁止 |
| 审批前状态 | 请求运行仅待审批；无巡检采集 | 没有伪造成功 run |
| 权限负例 | 未登录 401、自批 403、撤权/普通成员拒绝、跨组织查询拒绝 | 两个真实 JWT 会话 |
| 并发审批 | 同时两次审批只一次成功，另一次 409 | 同进程 HTTP 并发，非多进程应用部署 |
| 运行报告 | 调度运行、巡检结果、报告落库、证据摘要与原结果关联 | 只读 HTTP 上游为固定合成响应；未连堡垒机 |
| 独立验收 | reviewer 查看证据并验收，负责人读取反馈与管理进度 | 不是自主多智能体验收 |
| 交接结果查找 | 按接收 goal_id 查运行；源目标/其他员工不混入 | 使用当前数据库关联 |
| 返修重提 | changes_requested 后新计划、新审批、新报告、新验收；旧反馈留存 | 重复采集仍使用同一合成上游 |
| 失败恢复 | 配置版本漂移导致失败且不采集；显式新计划、新审批后交付 | 不是断电/网络分区/主从切换测试 |
| 重启 | 完整停止应用，再打开同一临时安装，重新登录、读取验收/证据/进度 | 同一 Python 测试进程内服务生命周期重启；PG 容器未重启 |

前端 HTML 与构建资源实际 HTTP 访问通过；没有实际浏览器点击验收。
原主动关怀调度在夹具中停用；不能将本路径说成验证了所有后台任务。
AKB、真实模型、堡垒机、Mattermost 发送不在本轮范围。

## 复现入口

为本轮独立容器准备完成后，用本地合成管理员连接启动：

```sh
OCTOP_TEST_PG_DELIVERY_ADMIN='host=/tmp/octop-pg-delivery/socket dbname=postgres user=octop_test' \
OCTOP_TEST_DATABASE_DISPOSABLE=1 UV_OFFLINE=1 uv run pytest \
  tests/integration/test_fresh_install_inspection_delivery.py -k postgresql -q
```

三个 PG 用例是正常验收、返修重提、运行失败后恢复，均使用真实回环 HTTP。
未设置独立 opt-in 时跳过；不自动发现数据库、不使用生产连接。
初次尝试 400 是夹具误把 keyword conninfo 当成 setup API 要求的 URI；
改用 `postgresql://role@%2F.../database` 的合法 Unix-socket URI 后通过，没有为此修改产品解析规则。

## 同轮优先修复的真实并发缺陷

独立审查提示后，在真实 PG 用屏障复现：worker 预锁查询时目标不存在，另一连接随后创建它；
后续目标查询可见新行，但该行未被预锁。另一连接再将员工暂停到版本 2，原交接仍按版本 1 成功落库。
这是 READ COMMITTED 不同语句快照之间的窗口，不是模拟结果。

现在必须核验预锁返回的 ID 集合覆盖源和接收员工，否则在读取后续状态前返回 404；
不能使用稍后才出现、未持锁的员工。SQLite 行为不变，不新增迁移。
新增真实 PG 回归覆盖该插入窗口、相反方向的同时交接、普通写入者持目标锁后再改员工导致的反向锁序。
最后一种仍可能发生死锁/锁超时，测试要求一方整体提交、另一方回滚，不宣称全系统无死锁。
没有放宽同键指纹、源版本、撤权、独立验收或恢复限制。

## 本轮结果与剩余门槛

联合执行以下四组：`test_fresh_install_inspection_delivery.py`、
`test_postgresql_goal_handoffs.py`、`test_postgresql_governance_migrations.py`、
`tests/unit/digital_workers/test_goal_handoffs.py`，**56 项全部通过**：
7 条完整路径（3 PG 回环 HTTP + 4 SQLite），25 项 PG 交接、5 项 PG 治理、19 项 SQLite 交接。
`make lint-all typecheck-all` 通过（mypy 678 源文件），未运行受限全量遥测测试。
本轮没有前端改动，使用已构建资源完成 HTTP 资源读取和静态类型检查；未进行浏览器点击。

结束核对测试数据库、测试角色、随机 schema 剩余均为 0，随后移除独立 PG 容器，
没有留下常驻数据库服务。Docker 缓存镜像保留；bubblewrap/浏览器沙箱设置未改动。

部署时仍需更新所有承载交接的应用进程；此修复不能约束仍运行旧代码的实例。
尚未验证真实模型、AKB、堡垒机、Mattermost、TLS/密码认证、自定义最小 DB 权限、
其他 PG 版本、多进程部署、故障切换、断电或安全恢复。在线/原地恢复及恢复激活继续关闭。
当前证据缩小了 PG 企业场景交付差距，不构成 1.0 发布批准。
