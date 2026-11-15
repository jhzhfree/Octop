# SQLite 恢复时保留会话撤销记录

本阶段基于 `e2c78d8b192daafbc7d8dcb5970afd0e9b40891b`，日期 2026-11-15。
它修复系统备份恢复的一个安全缺口，不代表 1.0 发布或部署验收完成。

后续审查确认 ACL/密钥回退及非原子写回风险仍存在；在线入口现已
[安全拒绝](online-restore-gate.md)。以下是历史底层测试范围，不是 CLI 或底层恢复
安全可用的推荐，不能推断所有旧令牌都不会复活。

## 问题与行为

过去恢复旧数据库会覆盖 `auth_session_revocations`。备份后已登出的令牌及其
滑动续期家族，可能因撤销记录消失而重新被接受；`preserve_users=True` 也有此问题。
修复前以纯合成账号、签名密钥和真实签名令牌复现，两种 preserve_users 参数均失败。

SQLite 系统恢复现在先复制只读源至私有临时数据库，在副本迁移到当前 schema 并
检查完整性；然后持有共享连接池锁，将当前与归档的撤销摘要取并集，提交副本后
替换目标。每批最多读取 500 条，重复摘要保留归档记录，不复制原始令牌。
准备或合并失败不会替换目标；迁移错误仍使用 `BACKUP_SCHEMA_INCOMPATIBLE`。
目标记录为 schema 038 或更新、却缺少撤销表时拒绝恢复，不将丢失历史视为没有撤销。
空目标和真实 schema 037 归档仍可恢复。输入归档保持不变。

## 使用边界

- 仍按既有 CLI 维护流程停止其他 Octop 进程后恢复。锁只协调同一 `SqlitePool` 的
  读取/写入，不是跨连接、跨进程维护屏障；多进程在线恢复仍是发布阻断。
- 仅覆盖系统恢复使用的 `restore_sqlite_into_pool`。PostgreSQL 与底层独立文件复制
  `restore_sqlite_file` 没有加入此保证；不能把本轮结果扩展到这些路径。
- 保留当前可读的撤销历史；目标磁盘已丢失或恢复到全新机器时，旧备份无法提供备份后
  才产生的撤销。此类灾难恢复需要额外的密钥轮换/强制重新登录策略。
- 不提供数据库、配置、工作区文件及后续账号保留步骤的跨资源整体事务。
  账号/组织权限恢复与当前安全策略的协调也未在本轮验收。
- 增加一个临时数据库副本，需要额外磁盘空间。准备失败不动目标；不宣称掉电、
  磁盘耗尽或替换之后后续步骤失败时具备完整自动回滚。

## 本轮验证

所有 Python 命令使用现有依赖及 `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1`。
未新增安装开发软件，也没有读取真实凭据。

- `uv run pytest tests/unit/backup tests/unit/api/test_backup_restore_rehydrate.py -q`：70 passed。
  新增 8 项测试覆盖两种账号保留模式、备份前后登出与不同续期令牌、独立活跃会话、
  关闭后重开真实 SQLite、源不变、准备/合并异常、共享池并发读取、撤销表丢失、
  空库以及真实 schema 037 升级。PG 命令测试使用替身，不是 PG 联调。
- `uv run pytest tests/unit/api/test_jwt_tokens.py tests/unit/api/test_jwt_auth_middleware.py tests/integration/test_logout_revocation.py -q`：24 passed，2 项既有 ldap3/pyasn1 弃用提示。
- `make lint-all typecheck-all`：通过。Ruff、Prettier、mypy（659 个源文件）、TypeScript 通过；ESLint 为 0 errors / 69 warnings。
- 修复前复现的 2 项失败已经通过；最终定向用例没有失败。
- `make all` 包含 `lint-all`、`typecheck-all` 和全量 `pytest -m "not live"`。
  仅运行允许的静态门禁和上述定向用例；不运行全量、testmon 或被限制的遥测测试。
- 本轮未改前端代码，未重新构建前端或运行浏览器验收。

## 环境与未运行项

Docker 29.9.2 daemon 可访问，rootless、默认 seccomp、cgroupns、vfs；本地镜像为空。
实际 bubblewrap 隔离探测失败：`bwrap: setting up uid map: Read-only file system`。
没有修改安全/网络设置，也没有绕过本地试运行的 bubblewrap 前置检查。
没有拉取镜像、运行应用容器或启动完整 Octop 服务。数据库重开是持久化恢复测试，
不是应用启动/重启验收。没有真实模型、AKB、Mattermost、生产访问、发送、合并或部署。
