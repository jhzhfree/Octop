# 恢复产物只读核验：证据与激活边界

2026-11-16，开发基线 `cace3408693395856232b1f0b4b405084efee884`。

## 管理员可执行的步骤

创建 `recover-image --include-workspaces` 时，将成功 JSON 中的
`workspace_report_sha256` 单独保存在可信记录中。稍后可在受支持的 Linux 私有目录
对产物运行：

```bash
octop backup verify-image /path/to/private-recovery/review-image \
  --report-sha256 <创建时单独留存的workspace_report_sha256>
```

不能从当前待检查目录重新计算哈希就称其可信：那只能证明产物自洽，无法检测同时
改写清单与文件。外部哈希本身的来源、存放和身份真实性由操作者负责，本命令不是
签名验证，也没有访问真实用户文件进行本轮验证。

成功输出 `inventory_match_observed=true`、工作区文件数、源归档哈希及清单哈希；
同时始终输出 `application_start_allowed=false`、
`point_in_time_consistency_verified=false`、`completeness_verified=false` 和激活
阻塞列表。**退出码 0 仅表示这次完整性观察通过，不得作为服务激活或部署授权。**
命令没有 activate 选项，没有打开 SQLite 连接、解析应用配置或启动 Agent。

## 检查范围

- 首先将清单字节与外部预期 SHA-256 比较，再检查受支持格式、路径、大小、摘要与
  强制禁止激活的声明。只支持有工作区清单的产物，清单上限 8 MiB；无清单的旧镜像
  或超过此核验上限的产物拒绝，不能用临时自造清单升级为可信产物。
- 对数据库、隔离文本标记、清单及所有列出的工作区文件逐字节哈希；检查根和子目录
  的成员集合，不允许额外 WAL/SHM/journal、未列文件或目录，也不允许缺失文件。
- 使用相对目录文件描述符和 O_NOFOLLOW 打开子项；拒绝 symlink、硬链接普通文件、
  非普通文件、非当前 UID、组/其他用户可访问的目录或文件。要求数据库隔离 xattr
  仍为格式 3。数据库仅按普通字节读取，避免核验过程触发 SQLite hot-journal 回放。
- 文件读取前后核对 inode/大小/mtime/ctime/模式/链接数，再作一次全树元数据核对，
  检测常规的写入、路径替换和目录变动；最后核对调用路径仍指向原根目录。
- 核验不写应用数据、不清理 sidecar、不修复标记、不删除失败目录。中断或不匹配
  返回拒绝，已有内容和并发写入者的改动留在原处；这不是业务数据回滚。

## 不能据此证明什么

哈希与两遍 stat 不是文件系统快照、停写屏障或同 UID 对抗协议。检查之间/之后的
变化、特权修改及恶意 ABA 不能全部排除；只能报告本次观察到的字节匹配，后续使用
前仍需完整部署维护协议。不能把稳定读取外推为备份源曾处于同一时刻的一致状态。
原备份数据库与工作区顺序采集、漏采文件不可识别的问题仍存在。

外部留存清单哈希仅绑定本次准备后的数据库、文件与源归档标识，不证明源归档签发者
或当前身份/ACL 合法性。首次发布返回 COMMIT_UNCERTAIN 且未留存可信清单哈希时，
不能把目录内自算哈希当恢复承诺；保留不确定产物，从可信原归档向另一个新目录重建，
仍不进行原地覆盖。此前数据库单独审查路径保持不变。

激活还需要：备份时可信业务停写/快照、当前身份与 ACL 映射、数据库和工作区代际
切换及跨进程故障恢复协议。当前缺少这些证明，因此在线/原地恢复及服务激活继续
关闭。真实 PostgreSQL、生产部署、硬件掉电和跨主机文件系统未验收。

## 本轮验证

临时合成产物覆盖：成功命令且不调用 SQLite、文件/数据库/清单损坏、缺失/额外文件、
WAL/SHM/journal、根/子目录/文件 symlink、硬链接、xattr 丢失、权限扩大、错误外部
锚点及畸形清单。受控读期间注入后写、路径替换、根目录替换、读取失败；另以独立
SQLite 子进程更新合成数据库，检查核验拒绝且不试图回滚该写入。

初步 26 项定向测试通过；加入独立进程写入后，完整定向回归
`uv run pytest tests/unit/backup/test_recovery_image.py tests/unit/backup/test_system_archive.py
tests/unit/backup/test_restore_session_revocations.py tests/unit/i18n -q`：224 项通过，
无失败。`UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all`
通过：mypy 665 文件、ESLint 69 warnings / 0 errors。新增进程写入用例另经
Ruff lint/format 通过。测试无真实凭据、
生产服务或消息发送；未运行全量 make all、受限遥测、真实外部联调。无前端源码
变化，不重跑前端生产构建。1.0 的服务恢复闭环仍未完成。
