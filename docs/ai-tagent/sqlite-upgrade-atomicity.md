# SQLite 企业场景升级失败的事务边界

2026-11-16；基线 b37981a0f598382e757292bc2ec24fab77463e01。

## 已复现的问题

原 SQLite 032–045 脚本通过 executescript 在自动提交连接上逐语句执行。
以 035 为例，新增 report_attachment_sha256 列及附件表后，如果版本更新失败，
结构已提交而版本仍为 034。下一次启动重放 ALTER TABLE，会遇到重复列，阻断启动。

临时数据库使用实际 001–034 迁移构造；在版本更新前注入 RAISE(ABORT)，原代码
确实残留新增列，回归测试首次失败。未对真实用户数据库执行升级或恢复。

## 修复与兼容范围

核查 032–049 SQLite 脚本均为可事务执行的 DDL/DML 后，扩展既有 046–049 的
BEGIN IMMEDIATE / COMMIT / 异常 ROLLBACK 边界到 032–049。每个脚本的结构、数据
及 schema watermark 一起提交；失败后不保留该脚本的部分结构。没有修改迁移文件、
schema 编号、用户数据口径或 PostgreSQL 路径。早期兼容迁移助手保持原行为。

这是单个脚本的原子性，之前成功的脚本不会因后续失败而回滚。后续版本脚本必须另行
确认是否能纳入事务。既存半升级库不会自动补列、删表或强改版本；应保留原库和错误记录，
在授权维护环境根据完整结构/数据分析恢复，不能直接重置版本号制造升级成功。

不保证多个升级进程可并跑，不构成跨进程停写证明，不替代离线备份、维护窗口或数据库与
工作区一致性方案。健康端点的 liveness 语义没有被改成升级/业务 readiness 保证。
原地/在线恢复和隔离镜像激活仍关闭。

## 验证证据

- 032–049 每个实际脚本在更新版本前故障注入：结构和版本完整回滚；关闭数据库后重新打开，实际升级到 049，并验证重复启动及 integrity_check。
- 035、042、044 使用独立 SQLite 读连接：写事务内 DDL 完成而 watermark 尚未提交时，读者仍看到完整旧结构和旧版本；提交后读到新版本。
- 初次新回归按预期失败 1 项，修复后迁移原子性 21 项通过；schema guard 和初始化数据库相关 14 项通过；DB pool、setup lifecycle 与 fresh-install 工作流 32 项通过，合计 67 个不同用例通过。
- fresh-install 包含明确启用 OCTOP_TEST_DELIVERY_HTTP=1 的真实回环 HTTP 主路径和同进程服务 stop/start；Agent/模型和巡检上游仍是替身，没有真实外部调用。
- 未测试断电/磁盘硬件故障、PostgreSQL、Windows、生产部署或既存半升级业务库。未运行受限遥测或完整 make all。没有前端修改，不重复前端构建。
- make lint-all typecheck-all 通过，mypy 检查 674 源文件；最终修改文件 Ruff 检查及格式检查通过。已有 lint 警告保留，无新增依赖。

## 外层事务入口保护

后续审查确认：sqlite3.executescript 在执行脚本前会提交已有事务，即使脚本后来失败，
调用方先前的写入也已落盘。正常启动目前没有外层事务，但不能让复用入口依赖这个假设。
run_migrations 的 SQLite 入口现在在任何兼容修复前检查 in_transaction；032–049
脚本执行点在持有连接锁、进入自身异常回滚处理前再次检查。有外层事务或 SAVEPOINT 时
抛出 DATABASE_MIGRATION_TRANSACTION_ACTIVE，不提交、不回滚调用方数据。

新增 035/049、公开 runner/脚本入口、BEGIN IMMEDIATE/SAVEPOINT 组合回归：调用被拒，
外层事务仍有效，独立连接看不到未提交写入，调用方可正常回滚。此策略明确拒绝嵌套，
不把整个升级序列包装为一个事务，也不承诺同连接被其他线程任意并发操作时的安全性。
本轮迁移原子性、schema guard 和 setup database 共 43 项通过；make lint-all
 typecheck-all 通过（mypy 674 文件）。未运行完整受限门禁或 PG；无前端变更，未重复构建。
