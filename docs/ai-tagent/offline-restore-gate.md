# 离线及底层恢复入口安全拒绝

2026-11-15，基线 `6291ece8d1015b7b5b25483b83fb461618e6dd8a`。
本轮不实现恢复成功路径；关闭 CLI 和底层入口，避免绕过上一阶段在线拒绝。
**当前不能完成系统恢复，因此仍不具备 1.0 发布条件。**

## 结论与兼容性

CLI 原本只显示“先停止服务”，随后打开数据库调用底层恢复。SQLite `RLock` 仅保护
一个连接池，独立连接、其他进程与旧版本进程不受约束；没有可信的停服证明。
原流程在替换数据库之后才写回 users/JWT，不能原子保留权限与撤销策略，故障可能
停在数据库、配置与工作区不一致的中间状态。增加 bool 或复制更多字段无法修复这些边界。

现在以下入口均拒绝，错误码 `BACKUP_RESTORE_UNAVAILABLE`（底层 OctopError 409）：

- `octop backup restore`：退出非零，在加载配置、读取密钥、打开数据库或确认覆盖前
  停止。保留旧参数解析，`--yes`、`--no-config`、`--owner-user-id` 均不能启用恢复。
- `restore_system_backup`：保留导入位置/参数签名，但不读取/提取归档或写入目标。
  None/True/False 的 preserve_users、Path/bytes、旧迁移格式、空库也不例外。
- `restore_sqlite_file` 和 `restore_sqlite_into_pool`：同样拒绝，不创建缺失目标目录。

原非原子系统恢复流水线、专用提取/替换及已无生产调用的身份/密钥写回辅助代码已移除，没有另留 unchecked 私有版本
或测试绕过开关。在线入口仍返回既有 `BACKUP_ONLINE_RESTORE_UNAVAILABLE`。
新代码的系统恢复拒绝也覆盖 PG，但不代表 PG 恢复或维护机制已验证。
既有直接 PG 工具封装、SQL/文件操作和工作区导入不是本门禁提供的安全恢复协议。
旧程序/旧进程不会自动获得门禁，也不能取消它们已开始的恢复。

备份创建、自动备份、上传、导出/下载和归档格式不变。快照导出函数新增独占创建目标：
现有目标（包括符号链接）拒绝覆盖；生产调用使用私有临时目录中的新文件，归档输出
替换语义不变。快照失败可能留下新建的部分文件，由调用者清理；系统导出临时目录
自动清理。此改变也不是文件系统权限边界，不能限制有本地写权限的人自行操作文件。

由于恢复成功已不再是产品契约，原成功恢复/保留身份测试被明确替换，而非 skip 或
绕过门禁：保留原创建归档、聊天排除、技能/插件/知识文件导出等断言；新增拒绝后
安全状态不变、失败哨兵及跨连接竞争测试。历史成功测试只可在历史提交中查看，
不得把旧结果作为当前恢复可用证据。

## 验证边界

所有输入均为临时合成数据，无真实业务库/凭据。

- 当前和归档使用不同 JWT 密钥；当前账号已降权/禁用，permissions 清空，组织成员
  删除，存在撤销记录。各种参数和有效/无效/缺失/bytes 归档都被拒绝；数据库序列化
  哈希、归档哈希和工作区不变，旧密钥令牌仍不可验证，已撤销令牌仍被拒绝。
- 在 SQLite connect、tar 读取、文件复制、原子替换位置安装抛出磁盘异常的哨兵，
  证明拒绝发生在调用这些位置之前。**没有执行替换，所以不是替换后故障回滚验证。**
- 另一连接持有 BEGIN IMMEDIATE 写事务时，三个入口仍立即拒绝，其更新可正常提交；
  这验证无恢复写入竞争，不是跨进程停写或原子恢复证明。没有用布尔参数模拟停服。
- CLI --yes 等组合在配置/数据库访问哨兵前拒绝，目标 home 不被创建；CLI 创建备份仍成功。
- 合成 WAL 中的已提交数据进入快照，复制快照文件到独立目录后能读取且 integrity_check
  为 ok；现有目标快照被拒绝且原表/数据保留。

Python 使用 `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1`：
`uv run pytest tests/unit/backup tests/unit/api/test_backup_restore_rehydrate.py tests/integration/test_online_restore_gate.py tests/unit/i18n tests/unit/gateway/test_versioned_history.py::test_backup_includes_archive_and_restore_refuses_partial_overwrite -q`：158 passed，2 项既有 ldap3 弃用提示。

前端既有恢复门禁回归 `npm test -- src/pages/Settings/BackupRestore/index.test.tsx`：2 passed。
`make lint-all typecheck-all` 通过，最后收口后 `make lint typecheck` 复核通过；mypy
659 个源文件，ESLint 为既有 69 warnings / 0 errors。`npm run build` 通过，有既有构建提示。
收口时误删两个共享聊天纯函数曾导致 7 个测试收集错误和 2 个 mypy 错误，已恢复共享
函数后重跑；不会将这一中间失败隐藏为全程无失败。合成测试密钥长度警告也已消除。

PG 专用集成测试已按拒绝契约更新，但**未运行**；不会读取其连接凭据或执行 DROP SCHEMA。
不运行全量 make all、testmon、受限遥测、真实服务、生产或跨进程恢复演练。
没有安装依赖、改变安全/网络设置、运行容器、合并或部署。Docker/bubblewrap 本轮
未复测；之前 rootless Docker 可连接但无本地镜像，bubblewrap 的实际隔离受只读 uid_map
限制，不能把本轮 Python/SQLite 测试视为部署隔离验证。

## 仍需实现

恢复可操作性尚未交付。下一阶段必须实现并验证真实维护协调、所有授权数据的映射策略、
新密钥和受控恢复管理员、准备副本的一次性身份处理、数据库与文件代际发布、失败日记
及启动时拒绝未完成恢复。详见[恢复协议设计门槛](online-restore-gate.md#下一阶段恢复设计要求尚未实现)。
不能默默恢复历史身份，也不能把本轮“没有执行任何恢复”称作已经具备自动回滚。

WAL 版本/来源和并发核查另见[SQLite WAL 证据](sqlite-wal-review.md)。
