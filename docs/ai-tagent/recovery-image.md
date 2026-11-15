# SQLite 隔离恢复镜像

2026-11-15，本轮基线 `a10f64728ca09cbfbebe6d2c478c7e1320d79723`。

**安全更正：该基线的裸 SQLite 文件发布存在已复现的 hot-journal 漏洞。**
目标主文件不存在也不代表 SQLite 命名空间为空：旧 journal 可在首次查询前回滚掉
隔离表并恢复历史管理员，integrity_check 仍可通过。此前 212 项测试没有覆盖此情况，
不能作为旧镜像安全的证据。不要继续使用该版本生成的裸文件；从受信任原归档重新
生成新格式目录。不要把旧文件/sidecar 搬入新目录，也不要手工删除隔离标记激活。
本轮不操作任何已有业务库或历史用户制品。

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
  --output /path/to/private-recovery/review-image \
  --identity-policy quarantine-all \
  --archive-sha256 <上一步核实的64位小写摘要>
```

`--output` 现在是**尚不存在的目录**，不是 SQLite 文件名；这是安全必需的开发版
兼容性变更，无旧裸文件发布开关。目录含 `octop.db`（0600）与外部隔离标记
`.octop-recovery-quarantine`，目录本身为 0700。成功 JSON 的 `format` 为 2，
`output` 为目录，新增 `database` 为主库路径，并含源归档/镜像 SHA-256、schema、
账号数和 `application_start_allowed: false`。

应用 SqlitePool 在调用 SQLite connect **之前**检查外部标记（包括悬空符号链接），
因此外部进程在发布后加入的 hot journal 也不会被该连接池自动回放。内部表门禁仍
保留，但不能单独防止 SQLite 的查询前恢复；不相信任何标记中的布尔值。
服务启动提前打开并复用实际控制面连接，在插件加载/依赖安装、移动能力探测、目录
扫描之前拒绝隔离镜像；准备阶段失败关闭连接。此处仍会解析本地环境/配置，不能
称为启动完全无本地副作用。正常空目录继续延迟数据库绑定。
原归档保留不动，原业务库也不被打开或替换，因此恢复前的
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
证明**；不能删除锁文件来解决占用。关键提交使用 Linux `renameat2(RENAME_NOREPLACE)`
独占发布**整个新私有目录**，库和全部 SQLite sidecar 获得新的目录命名空间。
不配合 flock 的其他进程抢先创建空目录、文件、旧 journal 目录或 symlink 都使提交失败，
不覆盖也不清理竞争者目标。系统/文件系统不支持此操作即拒绝，无普通 rename 回退。
旧输出同级的 `review-image-journal/-wal/-shm` 不属于新主库路径，不读取/删除它们；
新主库 sidecar 路径只能位于 `review-image/octop.db-*`。这不是发布前 sidecar 检查策略。
没有机制强制旧程序停止写原库，所以不提供原库替换/切换功能；本地有写权限的
操作者、旧版本程序和任意直接 SQL 不受新启动门禁保护。

准备阶段在私有副本一次性处理身份并写入隔离标记，完整性检查后关闭所有连接、转为
DELETE journal，检查私有准备副本无 WAL/SHM/journal sidecar；随后数据库与外部
标记文件 fsync、私有目录 fsync、目标父目录预检 fsync、一次独占目录发布、父目录 fsync。备份创建仍使用 SQLite backup API 汇入已提交 WAL，
不直接复制运行中主库或拼接旧 sidecar。

- 发布前失败：目标不存在（或竞争者文件保持不动）；源归档与原业务库不变。
- 发布后错误：返回 `BACKUP_RECOVERY_COMMIT_UNCERTAIN`，保留可能已提交的完整隔离镜像；
  不自动删除、覆盖或重试。先只读核对镜像元数据、摘要及完整性，再选择新输出名称。
- 进程突然退出可能遗留 `.octop-recovery-*` 私有目录。确认无构建者运行后由操作者
  审核清理；不能删除持久锁 inode。不得把异常退出模拟当作物理掉电证明。

## 验证记录

所有测试均为临时合成库，无真实凭据/业务库。覆盖不同历史/当前 JWT 密钥、原库权限
收紧/组织撤权保持不变、镜像账号禁用及撤销记录保留、WAL 归档、旧 schema 迁移、
无效归档、显式 CLI 参数、实际跨进程 flock、非协作占位、发布前/后进程退出、准备/
发布/目录同步失败，以及目录发布完成后才抛错的结果判定。新增真实异常退出写入者
留下 hot journal 的攻击对照、旧 WAL/SHM/符号链接、不配合锁的跨进程目录占位、
发布后 journal 注入、启动副作用哨兵、失败关闭连接、空库延迟绑定与 FIFO 输入拒绝。
### 本轮修复阶段

- `uv run pytest tests/unit/backup tests/unit/db/test_db_pool.py tests/unit/db/test_db_factory.py tests/unit/db/test_setup_lifecycle.py tests/unit/api/test_backup_restore_rehydrate.py tests/integration/test_online_restore_gate.py tests/unit/i18n tests/unit/auth/test_access_state.py tests/unit/gateway/test_versioned_history.py::test_backup_includes_archive_and_restore_refuses_partial_overwrite -q`：245 passed，2 项既有 ldap3 弃用提示；恢复镜像文件含 48 项测试。
- `make lint-all typecheck-all` 通过；mypy 660 个源文件，ESLint 69 warnings / 0 errors。
- 前端备份页回归：2 passed，保留既有 jsdom getComputedStyle 提示。
- `npm run build` 通过，保留既有构建体积/依赖提示。
- 初次 lint 因一个测试嵌套 with 风格失败，修正后通过；无未解决的测试失败。
- 普通启动的迁移与连接复用、空库延迟绑定均有回归；运行时/插件使用替身，
  不能据此宣称真实整机启动、插件安装或部署已验收。

### 历史 a10f647 阶段（不覆盖已确认漏洞）

- 历史 a10f647（未覆盖 sidecar 漏洞）：`uv run pytest tests/unit/backup tests/unit/db/test_db_pool.py tests/unit/api/test_backup_restore_rehydrate.py tests/integration/test_online_restore_gate.py tests/unit/i18n tests/unit/auth/test_access_state.py tests/unit/gateway/test_versioned_history.py::test_backup_includes_archive_and_restore_refuses_partial_overwrite -q`：212 passed，2 项既有 ldap3 弃用提示；其中新镜像测试 27 项。
- `make lint-all typecheck-all` 通过；mypy 660 个源文件，ESLint 69 warnings / 0 errors。
- `npm test -- src/pages/Settings/BackupRestore/index.test.tsx`：2 passed；jsdom 有既有 getComputedStyle 未实现提示。
- `npm run build` 通过；保留既有构建体积/依赖提示。
- 初次定向 mypy 发现 tar 输入类型 IO[bytes] 与 BinaryIO 不匹配，已修正后通过上述全源类型检查。

Python 检查使用 `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1`；没有安装依赖。

未验证：物理掉电、真实部署接管、旧/第三方进程停写、PG 恢复、全系统文件联合事务、
激活后管理员可操作性、真实身份/模型/AKB/Mattermost。Docker/bubblewrap 本轮不作为
部署证明；受限遥测和全量 make all/testmon 不运行。SQLite 版本证据见
[WAL 核查](sqlite-wal-review.md)，不能外推到生产镜像。

## 服务恢复闭环设计门槛（尚未实现）

1. **一致性来源**：现有 `create_system_backup` 先做 SQLite backup，再逐文件收集工作区，
   不能证明同一时点的一致性；聊天历史还有独立数据库。完整服务恢复需在受控维护态
   生成带一致性证明的归档或使用经过验证的文件系统快照。旧 manifest v1 的
   `workspace_included` 仅说明收集意图，不能升级解释为一致性证明。
2. **身份策略**：当前仅支持全部隔离。激活前必须有经独立可信来源确认的当前身份/ACL
   快照、明确的主体映射及拒绝恢复历史宽松授权的规则。受控恢复管理员必须为新身份，
   通过本地交互凭据创建而非从归档继承；现有 SSO/LDAP、令牌及自动化不能随之默认启用。
3. **发布与回滚**：数据库、工作区、配置、附件和历史分库准备为同一不可变代际，验证
   清单与引用完整性后才允许一次切换。需要所有受支持写入入口共同遵守、可检测旧进程
   的部署维护协议；不能以 bool、单进程锁或当前 builder flock 作为停写证明。
   切换前失败保留旧代际，切换后故障需要持久状态记录和明确的启动恢复规则。
4. **当前阻塞**：本仓库和环境没有上述完整协议或经过验证的目标部署快照机制。因此
   本轮不实现管理员激活、不开放原地/在线恢复，不声称实现系统级回滚。当前失败语义
   仅是新目录未发布或已发布但持久性确认不确定，不能替代服务恢复验收。

本地目录写入者可删除标记、添加 sidecar 或使用原生 SQLite；这不是对同 UID 恶意
进程/管理员的沙箱。人工审阅前需确认目录未被修改，不得将新格式安全结论外推到
旧版本程序、直接 SQL 或被重新打包/拆散的镜像。
