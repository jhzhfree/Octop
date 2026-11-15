# 初始化向导生命周期加固（schema 037）

## 安全边界

初始化权限不再由“系统只有一个管理员”判断。数据库保存 pending / active / finishing / completed 状态；用户创建和 pending → active 在同一事务内完成，绑定初始管理员。并发创建/完成通过数据库条件更新抢占，不在异步操作期间持有数据库事务。

管理员创建前仍需原有向导密码与短期令牌。创建时在同一事务内保存发起令牌的 SHA-256 摘要，active 阶段只接受与该摘要匹配的有效向导令牌或初始管理员 JWT；其他 worker 提前签发的令牌也不能继续操作。恢复向导必须提供初始管理员的有效登录 JWT，并实时检查数据库中的管理员身份及禁用状态。完成后所有向导令牌清除；重启、用户被禁用或仅剩一个用户均不会重开安装接口。

前端不再在丢失会话时自动匿名申请恢复令牌。

部署边界：向导令牌有效期存储仍为进程内存，跨 worker 不共享令牌会导致需要管理员 JWT 继续操作，但不会放宽权限。认证恢复会旋转持久摘要，之前的向导令牌随即失效；完成关闭与原子抢占均由数据库保证。

## 恢复与限制

- 管理员创建事务失败：整笔回滚，仍可重新初始化，不留下 creating 状态。
- 管理员创建后、开始 finish 前中断：状态保持 active；初始管理员登录后可用 JWT 调用 resume-wizard 或 finish。原有页面刷新可能转至登录，不提供新的恢复页面；也可登录后通过正常管理页面配置。
- 无效 provider 输入：在 finish 抢占前拒绝，可修正重试。
- finish 开始后出现数据库/运行时异常或进程中断：状态保持 finishing，向导永久关闭，不自动重放可能已保存的 provider。管理员仍可正常登录，检查模型配置并创建缺失的默认 Agent；不得手工重开向导以进行盲目重试。
- 默认 Agent 创建失败返回失败，不再虚报完成。此方案是安全的“至多一次尝试”，不是跨数据库及运行时的全事务安装或自动恢复。

## 升级影响

SQLite 与 PostgreSQL 同时提供 037 迁移。旧库只要存在任意用户（包括禁用用户）即标记 completed；历史安装是否完成不可可靠推断，因此采用关闭优先。旧版本中断安装的已有管理员应正常登录管理配置，不能再匿名恢复。没有用户的数据库为 pending。CLI 创建用户不开放 web 向导。

上线前备份数据库；迁移后不要直接回退到旧服务版本，否则旧接口仍存在重新开启漏洞。需要应用级回滚时同时保留本安全修复，不删除生命周期记录。

## 验证范围

离线 ASGI 集成测试覆盖真实首次安装、旧令牌、匿名恢复、重复完成、重启、禁用管理员、并发创建/完成、输入失败重试、创建事务回滚、完成中断关闭及旧库迁移。provider 探测使用离线替身，网络 socket 被禁止。PostgreSQL 迁移使用兼容 SQL 并与 SQLite 脚本对齐；未运行真实 PostgreSQL 服务验证。

## 类型检查修复与安全门禁（2026-11-15）

`DatabasePool.connect/transaction` 的连接、游标类型为 `Any`，导致
`result.rowcount == 1` 被 mypy 推导为 `Any`。`transition` 与 `rotate_token`
现沿用其他 repo 的 `bool(result.rowcount == 1)` 写法，明确返回布尔值。
比较仍要求恰好一行；没有改为 rowcount 真值判断，也没有修改 SQL、事务、
expected phase 条件、active 限制或初始管理员绑定。

新增 SQLite repo 回归覆盖：条件抢占成功/失败、重复抢占不能替换管理员、
finishing/completed 阶段不能旋转令牌、旋转使旧摘要失效、缺失生命周期行时
保持关闭且不能报告更新成功。既有初始化向导测试另覆盖并发、回滚和重启关闭。

执行前检查 Makefile：`all = lint-all + typecheck-all + test`。
静态部分为 Ruff、ESLint、Prettier、mypy、TypeScript；`test` 是
`pytest -n auto -m "not live"`，排除 live 标记并非禁网保证。
公共 fixture 清除代理也不代表禁止遥测；因此本轮不执行 `make all`、
全量 pytest 或可能退化为全量的 testmon，不重试此前被拒绝的遥测相关测试。
只运行静态门禁和明确选定的本地测试：repo 测试使用临时 SQLite；
`test_setup_wizard.py` 沿用现有禁止 socket connect/create_connection 的
fixture，provider probe 和 harness 使用替身，HTTP 为内存 ASGI。
uv 使用已安装依赖、`UV_OFFLINE=1` 和可写临时缓存；没有更改安全审查、
网络策略或禁网 fixture。此范围不构成真实服务或完整测试套件验收。

本轮结果：

- `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all`：
  全部通过，mypy 检查 656 个源文件无错误，TypeScript 检查通过。
- `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 uv run --frozen pytest
  tests/unit/db/test_setup_lifecycle.py tests/integration/test_setup_wizard.py -q`：
  **42 通过**（新增 repo 3 项、既有向导 39 项）；两条 ldap3 依赖弃用警告。
- `git diff --check` 通过。未运行全量测试、testmon、真实 PostgreSQL、模型、
  AKB 或 Mattermost；没有真实发送、部署或合并。未修改前端，不重复构建产物。
