# SQLite WAL 只读环境核查

日期 2026-11-15。未打开业务库、升级库或调整目标部署设置；只检查运行时元数据、源码，
并在临时合成库读取设置/测试快照。

## 实际运行时证据

- 本工作区 `.venv/pyvenv.cfg` 指向平台预置 CPython 3.12.14，uv 0.13.6。
- 解析后的解释器：`/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python3.12`。
- `sqlite3.sqlite_version`：`3.53.1`；`_sqlite3.__spec__.origin`：`built-in`。
- `SELECT sqlite_source_id()`：
  `2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9`。
- `ldd` 未列出独立 libsqlite3，dpkg 未找到该解释器路径的包归属。可确认是此平台
  Python 内置运行时，而不是据系统 sqlite3 CLI/apt 版本判断；没有供应商构建清单或
  amalgamation 原码/编译产物可用于进一步回补、供应链校验。

官方说明 WAL-reset 的已修复主线为 3.51.3 及以后，也列出早期回补版本；触发需要
WAL、多连接及罕见的并发写入/checkpoint 时序。[SQLite 官方 WAL 说明](https://www.sqlite.org/wal.html#the_wal_reset_bug)

本机 source_id 与 [SQLite 3.53.1 官方发布记录](https://www.sqlite.org/releaselog/3_53_1.html)
完全一致，且该版本包含修复。因此按可观察版本/source_id 证据，本开发解释器处于
已修复发布线，不需要假设早期版本获得了厂商回补。此判断不是二进制供应链认证，
也不是任何产品数据库发生/未发生损坏的判定。

部署镜像源码 `docker/Dockerfile` 使用 `python:3.12-slim`，没有在本轮拉取/运行镜像；
目标镜像及其他安装方式实际链接 SQLite、source_id、系统包来源/回补必须分别确认，
不能套用开发环境结论。未对未知目标回补情况作肯定判断。

## 模式、并发与 sidecar

SqlitePool 初始化设置 WAL。临时合成库实测 `journal_mode=wal`、`wal_autocheckpoint=1000`。
RLock 只保护单个池，另一连接/进程可写同一文件；提交可能自动 checkpoint。
源码还在桌面更新重启路径请求 `wal_checkpoint(TRUNCATE)`，该路径未在本轮执行。
旧恢复路径的 backup/手动 checkpoint 已随不安全恢复实现移除，不意味着所有运行场景
均没有 checkpoint 并发。真实目标是否存在该时序仍需部署级核查。

创建备份使用只读源连接加 SQLite connection.backup 到新暂存文件，并关闭连接，
没有直接复制活跃主库后丢弃 WAL。历史数据库与主库分别快照，因此不提供多文件一致时点。
官方将 WAL 视为持久状态的一部分，不能任意分离或删除。[SQLite WAL 文件说明](https://www.sqlite.org/wal.html#the_wal_file)

本轮合成测试显式保留一个已提交 WAL 记录，再通过 backup API 生成独立快照读取和
完整性检查；没有手动删除源 sidecar。测试证明该正常路径包含 WAL 数据，不证明所有
并发时序、断电或 WAL-reset 场景。没有运行破坏性/高负载 bug 复现，也没有声称发现产品损坏。
