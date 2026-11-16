# PostgreSQL 岗位交接验证（2026-11-16）

岗位交接创建现在支持 SQLite 和 PostgreSQL。未增加迁移，沿用 schema 050。
本次证明范围是交接的领域服务、真实数据库事务与迁移；不是整个产品 PG 交付或生产高可用认证。

## 实际运行环境与来源

- 原环境没有 postgres/initdb/psql 二进制和镜像；官方 Docker Hub `library/postgres:17` 正常下载。
- 固定镜像 digest：`sha256:0d938e975177bef9f1f61f3f9dbcf2e0aa0a139568f66d3415ed8892eb97f6ac`。
- 实际 `SELECT version()`：PostgreSQL 17.12，Debian 17.12-1.pgdg13+1，x86_64。
- Docker 29.9.2 rootless，容器非 root `postgres` 用户，`--network none`，没有发布 TCP 端口；
  `listen_addresses=''`，只使用独立目录中的 Unix socket。
- 容器只读根文件系统、cap-drop ALL、no-new-privileges、512 MiB 内存、1 CPU、128 PID 上限；
  PGDATA 在 tmpfs，数据全为合成。没有挂载业务目录或真实凭据，没有更改主机安全/网络配置。
- 测试 socket 父目录 0700；临时库本地 trust 仅用于此隔离合成库，不是生产部署认证建议。

## 事务设计

现有 `PostgresPool` 复用 psycopg 连接池，`transaction()` 成功提交、异常回滚。
SQLite 路径继续使用 BEGIN IMMEDIATE；PG 不假设它具有同样的全库写锁。

1. 检查当前事务为 READ COMMITTED。其他隔离级别返回 `GOAL_HANDOFF_ISOLATION_REQUIRED` / 503，
   不静默将管理员更强隔离配置降级。
2. 事务局部锁等待上限 2 秒、单语句上限 5 秒；管理员设得更短时保留更短值。
   不修改连接/数据库的持久配置。这里不是整个 HTTP 请求的总时限承诺。
3. 同一组织、认证账号与请求键映射到事务级 advisory lock。它只串行同键交接，
   哈希碰撞仅带来额外等待；唯一约束与请求内容指纹仍是最终数据约束。
4. 对认证用户及需要的组织成员行加 SHARE 锁；按员工 ID 顺序锁住两端员工行。
   后续修复要求预锁实际返回两端 ID，否则拒绝，避免 READ COMMITTED 下使用晚插入的未锁员工。
   源目标 UPDATE 行锁保护版本比较，接收岗位/Agent 与任命行 SHARE 锁保护授权和状态读取。
   普通 SQL 撤权/暂停写入也受这些行锁约束，不依赖其他代码遵守 advisory 协议。
5. 同事务写入源版本、接收草稿、来源记录和审计。相同键/内容重试返回既有记录并查询当前接收状态；
   不同键争抢同一源版本仅一个成功。不同键加最新版本仍允许再次交接。
6. 锁等待、死锁、语句取消和序列化错误映射为 409 `GOAL_HANDOFF_BUSY`，事务回滚后可重试原键。
   并非所有数据库故障都映射为此码；其他错误仍失败，不宣称所有故障可自动恢复。

撤权与交接按数据库锁顺序先后生效；已持有授权行锁的交接可能先提交，撤权随后提交。
不承诺在交接提交之前发起的撤权一定抢先，不保证实时撤权清屏。
应用数据库角色需具有原数据读写权限以及 PostgreSQL 行锁所需权限；本轮未验收自定义最小授权角色。

## 可复现测试与结果

`tests/integration/test_postgresql_goal_handoffs.py` 必须显式配置
`OCTOP_TEST_DATABASE_URL`、`OCTOP_TEST_DATABASE_DISPOSABLE=1`，数据库名须以 `octop_test` 开头。
每项创建随机独立 schema，search_path 不回退 public；结束只删除自身 schema。
不设置这两个开关时跳过，不自动探测业务数据库。测试使用完整实际迁移链并重复启动，达到 schema 050。

本次连接是本机测试 socket：

```sh
OCTOP_TEST_DATABASE_URL='host=/tmp/octop-pg-handoff/socket dbname=octop_test_handoffs user=octop_test' \
OCTOP_TEST_DATABASE_DISPOSABLE=1 UV_OFFLINE=1 uv run pytest \
  tests/integration/test_postgresql_goal_handoffs.py \
  tests/integration/test_postgresql_governance_migrations.py \
  tests/unit/digital_workers/test_goal_handoffs.py -q
```

46 项通过：22 项真实 PG 交接、5 项既有 PG 治理、19 项 SQLite 交接。
覆盖正常/同键、不同键 CAS、真实独立连接池并发、跨组织、用户禁用、Agent 权限/任命撤销、
普通撤权写入被事务行锁阻挡、审计触发器异常回滚、锁超时后重试、删除后来源留存、
更强隔离级别拒绝、较短管理员超时保留与连接复用。

首次运行曾失败：新夹具同一用户两个 Agent 重名违反唯一约束，已改为不同名称；
旧 PG 治理夹具仅应用 033/034，缺少当前代码使用的 044 review_note 字段，导致 4 项失败。
已补上真实 044 迁移及审计 note 哈希断言，保持产品验收逻辑不变，重跑 46 项全部通过。
旧治理夹具仍是特定迁移测试，不将其说成全迁移链验证；完整迁移链由本次新交接夹具覆盖。

## 尚未验证

- 全套 PG HTTP 安装/登录/执行/验收、跨进程应用重启、TLS/密码、最小数据库授权角色、其他 PG 版本。
- 生产负载、连接池耗尽、网络分区、断电、主从切换与数据库恢复；本次审计异常和锁超时不等于这些故障。
- 真模型、AKB、堡垒机、Mattermost 与浏览器点击。SQLite 实际回环 HTTP 继续使用模型/巡检替身。
- 全量 make all 中此前受限遥测测试，没有重试或绕过。在线/原地恢复与恢复激活仍关闭。

本轮另外通过 SQLite 新安装到交接/审批/报告/验收的 4 条场景（含真实回环 HTTP）、
8 项前端交接测试、`make lint-all typecheck-all`（mypy 678 源文件）与生产前端构建。
测试结束核验随机 schema 剩余 0、public 表 0；临时容器已停止并移除，tmpfs 数据不保留，
固定 digest 镜像保留为开发缓存。上面的测试 URL 不是常驻服务地址，重新运行需重新启动隔离临时库。

启动同等临时环境的步骤是：创建仅当前用户可访问的 socket 父目录，将子目录单独绑定到容器；
使用上述固定 digest、非 root postgres 用户、network none 和只读根文件系统，
为 `/var/lib/postgresql/data` 提供 uid/gid 999、0700 的 tmpfs，为 `/tmp` 提供 tmpfs；
运行 `initdb --no-locale --encoding=UTF8 --auth-local=trust --auth-host=reject -U octop_test` 后，
以 `listen_addresses=''` 和该绑定 socket 目录启动 postgres；通过该 socket 创建
`octop_test_handoffs` 合成库，再运行上述测试命令。禁止把测试 URL 换成已有业务库。

本轮未修改 bubblewrap 或浏览器沙箱；原有沙箱限制仍不属于本次解除范围。

后续完整 HTTP 路径及晚插入员工锁窗口的实际复现、修复和验证，见
[PostgreSQL 回环 HTTP 企业巡检验收](postgresql-http-delivery.md)。本文件上面的 22 项是前一阶段结果，
不能将其当作已覆盖后来发现的插入窗口；后续阶段补充了对应回归。
