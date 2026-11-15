# SQLite 隔离恢复镜像

2026-11-15，开发基线 `1b491fd1e41b09ef96155dcacac616116a6fb3c3`。

本阶段交付可实际读取业务数据的**新建数据库镜像**，不是系统原地恢复、服务接管或
可登录部署。在线和旧 CLI/底层恢复入口继续关闭。受控管理员初始化、ACL 审核后启用、
数据库与工作区联合恢复仍是 1.0 阻断；不能靠删除标记或修改布尔值绕过。

## 操作路径

只在单机 Linux、受信任本地归档和操作者拥有的私有目录中使用。不要指定运行中的
Octop home，也不要把镜像作为服务数据库。此命令不打开当前业务数据库，不加载配置、
不连接模型、AKB、Mattermost 或 PG。下面路径仅为示例，使用事先完成的备份归档：

```sh
mkdir -m 700 /path/to/private-recovery
sha256sum /path/to/completed-backup.tar.gz
octop backup recover-image /path/to/completed-backup.tar.gz \
  --output /path/to/private-recovery/review.sqlite \
  --identity-policy quarantine-all \
  --archive-sha256 <上一步核实的64位小写摘要>
```

成功返回 JSON，含源归档/镜像 SHA-256、schema、水印账号数和
`application_start_allowed: false`。可以用 SQLite 只读工具打开镜像审核业务表；
应用的 SqlitePool 会在修改 WAL 设置前拒绝带隔离表的数据库。它检查标记表存在性，
不是相信元数据中的布尔值。原归档保留不动，原业务库也不被打开或替换，因此恢复前的
安全副本就是所提供归档及不变的原库；本命令不冒充已为未知当前部署创建最新备份。
摘要确认所选字节，不提供签名、来源认证或对任意恶意 SQLite 文件的安全沙箱。

唯一支持的显式身份策略是 `quarantine-all`，没有默认保留历史身份选项：

- 所有归档用户禁用、密码清空、全局 permissions 清空、角色降为 user。
- JWT 密钥随机重建，归档密钥不能验证该镜像的新令牌体系；撤销记录保留。
- 历史组织成员、资源 ACL、所有权及业务记录保留供审核，**不认定其仍然有效**。
  不读取当前部署的账号/密钥/ACL，也不声称合并了当前收紧策略或所有会话种类。
- 其他归档服务秘密可能仍在业务库中；镜像不是脱敏数据，文件权限为 0600。
  正常应用启动被阻止，尚无重新启用账号/自动化/外发的成功路径。

支持 manifest v1 的 SQLite 主数据库归档，schema 不得新于本程序。迁移只在私有副本
执行，测试覆盖 037→042；不代表所有历史版本都已验收。拒绝 LightClaw 导入、PG、
含 history/ 的多数据库归档、主库 sidecar、重复必要成员、未知未来格式、坏水印和
完整性/FK 不通过的库。只读取两个精确 tar 成员，不执行 tar 路径提取；不恢复配置、
工作区、附件、技能、插件或历史分库。压缩归档/主库上限各 512 MiB，展开声明总量
2 GiB，成员 10000，manifest 64 KiB；不满足即拒绝。

## 排他性与故障边界

先读取实际 fstatfs，当前仅接受 ext4、XFS、btrfs、overlay；其他/未知文件系统拒绝。
这不是对目标硬件断电持久性或 overlay 下层部署的认证。目标目录必须为当前用户所有且
不可被组/其他用户写入。临时副本位于同目录的私有 0700 子目录，目录 FD 锚定操作。

持久 `.octop-recovery.lock` inode 上的非阻塞 flock 只协调本命令的构建者，**不是停服
证明**；不能删除锁文件来解决占用。关键提交使用 link(2) 独占创建目标：不配合 flock
的其他进程先创建目标也会使提交失败，任何既有目标（含 symlink）都不覆盖。
没有机制强制旧程序停止写原库，所以不提供原库替换/切换功能；本地有写权限的
操作者、旧版本程序和任意直接 SQL 不受新启动门禁保护。

准备阶段在私有副本一次性处理身份并写入隔离标记，完整性检查后关闭所有连接、转为
DELETE journal，检查无 WAL/SHM/journal sidecar；随后文件 fsync、目录预检 fsync、
一次硬链接发布、目录 fsync。备份创建仍使用 SQLite backup API 汇入已提交 WAL，
不直接复制运行中主库或拼接旧 sidecar。

- 发布前失败：目标不存在（或竞争者文件保持不动）；源归档与原业务库不变。
- 发布后错误：返回 `BACKUP_RECOVERY_COMMIT_UNCERTAIN`，保留可能已提交的完整隔离镜像；
  不自动删除、覆盖或重试。先只读核对镜像元数据、摘要及完整性，再选择新输出名称。
- 进程突然退出可能遗留 `.octop-recovery-*` 私有目录。确认无构建者运行后由操作者
  审核清理；不能删除持久锁 inode。不得把异常退出模拟当作物理掉电证明。

## 当前验证记录

所有测试均为临时合成库，无真实凭据/业务库。覆盖不同历史/当前 JWT 密钥、原库权限
收紧/组织撤权保持不变、镜像账号禁用及撤销记录保留、WAL 归档、旧 schema 迁移、
无效归档、显式 CLI 参数、实际跨进程 flock、非协作占位、发布前/后进程退出、准备/
发布/目录同步失败，以及硬链接完成后才抛错的结果判定。本轮门禁：

- `uv run pytest tests/unit/backup tests/unit/db/test_db_pool.py tests/unit/api/test_backup_restore_rehydrate.py tests/integration/test_online_restore_gate.py tests/unit/i18n tests/unit/auth/test_access_state.py tests/unit/gateway/test_versioned_history.py::test_backup_includes_archive_and_restore_refuses_partial_overwrite -q`：212 passed，2 项既有 ldap3 弃用提示；其中新镜像测试 27 项。
- `make lint-all typecheck-all` 通过；mypy 660 个源文件，ESLint 69 warnings / 0 errors。
- `npm test -- src/pages/Settings/BackupRestore/index.test.tsx`：2 passed；jsdom 有既有 getComputedStyle 未实现提示。
- `npm run build` 通过；保留既有构建体积/依赖提示。
- 初次定向 mypy 发现 tar 输入类型 IO[bytes] 与 BinaryIO 不匹配，已修正后通过上述全源类型检查。

Python 检查使用 `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1`；没有安装依赖。

未验证：物理掉电、真实部署接管、旧/第三方进程停写、PG 恢复、全系统文件联合事务、
激活后管理员可操作性、真实身份/模型/AKB/Mattermost。Docker/bubblewrap 本轮不作为
部署证明；受限遥测和全量 make all/testmon 不运行。SQLite 版本证据见
[WAL 核查](sqlite-wal-review.md)，不能外推到生产镜像。
