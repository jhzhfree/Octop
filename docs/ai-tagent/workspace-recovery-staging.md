# 工作区恢复暂存与一致性证据

2026-11-16，开发基线 `134024c1a54c2ad668e6a5877944cabdc80c02d8`。

## 实际可用路径

现有 `recover-image` 只发布隔离数据库。本阶段增加 `--include-workspaces`，将
同一归档中的工作区普通文件实际恢复到新目录，供管理员审查和后续迁移准备：

```bash
octop backup recover-image /path/to/backup.tar.gz \
  --output /path/to/private-recovery/review-image \
  --identity-policy quarantine-all \
  --archive-sha256 <已核对的归档SHA256> \
  --include-workspaces
```

父目录仍须事先存在并满足私有、本地文件系统约束；输出必须不存在。新目录包含
原有 `octop.db`、隔离标记，以及 `workspaces/<agent-id>/<归档相对路径>` 和
`workspace-recovery.json`。CLI 返回新增文件数量和清单 SHA-256。未带该选项时
保持数据库镜像行为，旧调用兼容。没有读取实际用户归档做本轮测试。

清单记录源归档 SHA-256、准备后数据库 SHA-256、每个文件的相对路径/字节数/SHA-256
以及每个 Agent 的 `files_recovered`、`not_observed` 或 `omitted_by_manifest` 状态。
`not_observed` 不能区分空目录、缺失目录或备份时未成功读取；`files_recovered` 仅
表示归档中这些文件已暂存，不代表工作区完整。报告始终明确
`point_in_time_consistency_verified=false`、`completeness_verified=false` 和
`application_start_allowed=false`。

## 安全与一致性契约

- 只接受数据库与 manifest Agent 集合完全匹配、无重复且类型正确的声明；有文件
  却未声明 Agent 或声明 omitted 时拒绝。漂移归档仍可使用原数据库单独审查路径，
  不会为了恢复文件放宽检查或静默重分配归属。
- 只读精确 `workspaces/` 路径下的普通、非 sparse 文件；拒绝链接、FIFO、重复文件、
  路径穿越、反斜杠和文件/目录冲突。不调用 tar extractall，不解析或执行文件内容。
  文件最多 128 MiB、工作区总量最多 512 MiB；完整归档已有的成员数、压缩及展开
  大小门槛仍适用，超限拒绝整次准备。
- 文件模式 0600，目录 0700，包含隐藏文件但不恢复执行位。归档中的 `workspace_dir`
  或其他宿主路径只作为数据库历史数据留存，不用来选择写入目的地。配置、插件、
  knowledge 和独立 history 数据库不在此阶段恢复范围内；原 history 归档限制保持。
- 工作区文件及清单均在私有 staging 内写入、fsync，子目录依次 fsync，然后与隔离
  数据库一起通过既有 RENAME_NOREPLACE 发布。没有任何写入现有业务目录的步骤。
  发布前失败清理 staging，不覆盖来源或竞争目标；发布后同步失败保留整个隔离
  目录并返回 COMMIT_UNCERTAIN，不把删除可能已提交产物称为回滚。
- builder flock 仅协调本命令的构建者；不能作为停服、跨进程业务停写或原地恢复
  证明。原有 inode xattr、外部隔离标记、全部账号禁用和 JWT 换钥继续生效。
  同 UID 可修改目录、删除标记或替换文件的边界没有消失，清单哈希不是签名或沙箱。

## 为什么仍不能激活

现有备份先取数据库快照，再遍历工作区；manifest 的 workspace_included 只是
采集意图。新清单证明恢复产物与同一输入归档的绑定，无法倒推出源数据库与文件
同一时刻的一致性，也无法找出归档从未包含的文件。没有新增“已一致”布尔开关。

管理员激活仍缺当前身份/ACL 映射、备份时可信停写或存储快照、工作区/数据库代际
切换协议和跨进程故障恢复验收。此阶段提供真实文件救援暂存，但**没有完成服务
恢复**，不开放在线或原地恢复，不启动 Agent，也不把暂存文件作为插件自动加载。
生产、PostgreSQL、远程文件系统及真实硬件掉电均未验收。

## 本轮验证

使用临时合成 SQLite 库与归档，验证当前备份生成器→文件暂存成功路径、原宿主
工作区不变、逐文件/数据库/归档哈希、缺失与省略状态、畸形 manifest、数据库
Agent 漂移、路径穿越、链接/特殊文件、重复、文件目录冲突、大小限制、模拟写入
失败、目录 fsync 失败、发布后同步失败与竞争目标保留。跨进程 builder 锁测试
同时覆盖开启与关闭工作区模式，既有 hot-journal/路径别名/隔离回归保留。

首轮 4 项失败源于新测试夹具未关闭/checkpoint 合成 WAL，导致 Agent 行未写入
归档主库；修正夹具后 19 项工作区定向测试通过，没有放宽数据一致性校验。
最终 `uv run pytest tests/unit/backup/test_recovery_image.py
tests/unit/backup/test_system_archive.py tests/unit/backup/test_restore_session_revocations.py
tests/unit/i18n -q`：197 项通过，无失败。`UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1
make lint-all typecheck-all` 通过；mypy 664 文件、ESLint 69 warnings / 0 errors。
未运行全量 make all、受限遥测或
真实外部联调，未合并、部署或发送消息；无前端源码变更，不重跑生产构建。

## 后续只读核验

可使用创建时单独留存的清单哈希执行 [`verify-image`](recovery-inventory-verification.md)，
检测产物损坏和可观察的并发变化。核验成功不证明源快照一致，也不授权服务激活。
