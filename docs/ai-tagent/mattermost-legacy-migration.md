# 旧 broker 人工迁移：放弃旧投递，仅接收新身份

本阶段针对 schema 046 禁用旧 broker 后没有安全续用路径的问题。
**不尝试重建未知历史高水位。** 管理员可以明确放弃该 broker 的全部已保存投递，
保留数据与 nonce，并永久封锁这些 delivery key，然后恢复新投递的待授权受理。
这不是恢复旧任务、生产接收器激活或真实执行授权。

## 策略与升级影响

schema 047 增加 `mattermost_retired_intakes` 永久隔离表。
迁移时对当前已禁用 broker 的所有历史 intake 建立隔离记录，enabled 不变。
因为无法区分 046 自动禁用与其他原因禁用，这一规则刻意保守：有可信历史但被禁用的
broker 也会失去旧投递的继续处理能力。启用中的 broker 不受这一迁移隔离影响。
原 intake 的状态、版本、指纹、来源和业务审计不改写；隔离记录不伪造来源版本。
SQLite 的建表、写入隔离记录和 schema 更新同事务，错误时回滚；PG 提供配对 SQL，未实测。

受理、查询、取消均在当前权限和身份范围校验后拒绝已隔离 key（410 INTAKE_RETIRED）。
旧投递即使收到更高版本也不解除隔离；新身份仍须独立 broker 签名、proof、实时来源事实
及当前本地权限检查。不得自动把旧 payload 换 receipt 重投；来源方需建立新的合法投递。
既有硬容量上限仍适用；工具不会删除 intake、nonce 或审计以腾出容量。

## 本地维护命令（仅 SQLite schema 047）

工具明确指定数据库，不读取默认配置、真实私钥或服务凭据，不调用网络，不运行迁移。
操作者权限来自本机数据库写权限，审计主体固定 `local-maintenance`，不冒充用户或 broker。
这不是远程管理员 API；拥有同 UID/数据库写权限的人本来就可以直接篡改数据库。
命令通过现有 SqlitePool 打开数据库，可能建立 WAL/获取写锁；`plan/status` 不承诺文件级只读。
恢复隔离镜像仍受 SqlitePool 拒绝规则约束，不能用此工具激活恢复镜像。

仅在已核实所有使用此库的接收进程均升级到 047 对应代码后使用；不支持混用旧二进制，
也不宣称阻止绕过仓库事务或不识别隔离表的写入者。工具不把自报停服布尔值当作证明。
当前生产 app 未挂载接收路由；以下示例中的路径须替换为隔离验收库，本轮未对真实业务库操作。

```sh
uv run octop mattermost-ingress --database /tmp/fixture/octop.db --broker fixture-broker plan
uv run octop mattermost-ingress --database /tmp/fixture/octop.db --broker fixture-broker retire-and-resume \
  --plan-digest '<plan 输出的完整摘要>' --reason '已审核清单，放弃这些旧投递，仅允许新的合法投递'
uv run octop mattermost-ingress --database /tmp/fixture/octop.db --broker fixture-broker status
```

`plan` 列出全部将永久隔离的 delivery key、已有隔离项、broker revision、组织/员工与保留 nonce 数量。
确认摘要绑定完整 broker 配置、全部 intake 内容、隔离清单及 nonce 数量。
`retire-and-resume` 在同一 SQLite 写事务重新比较摘要、保留/新增永久隔离记录、
提升 broker revision、启用、重验当前本地用户/组织/员工/Agent 权限并写入审计。
任何一步失败整体回滚；两个连接使用同一计划只有一个可成功。
较旧的在途 mapping 会因 revision 变化被拒绝。

达到 128 intake 或 4096 nonce 的 broker 拒绝续用，不假称容量已经恢复。
本工具没有通用注册、新 broker 创建、隔离凭据配置或安全容量回收功能。

## 故障与补偿回退

输出丢失或 CLI 返回 `MAINTENANCE_FAILED_CHECK_LOCAL_AUDIT` 时，不能假定事务未提交。
先使用 `status` 核查 enabled/revision 和最近 20 条维护审计，按原 plan_digest 对账。
若数据库本身不可读，保持接收服务停用，不能换身份绕过未知结果。

要撤销本次启用，使用 status/成功结果的当前 revision：

```sh
uv run octop mattermost-ingress --database /tmp/fixture/octop.db --broker fixture-broker suspend \
  --revision 2 --reason '撤销本次新投递受理能力，保留隔离及审计'
```

这是补偿操作：重新禁用并提升 revision，审计同事务；失败则保留原已提交状态。
**永久隔离不可撤销**，不会删除 fence、回放旧备份或复活旧任务；启用后新接收的数据也不删除。
不支持数据库降级到不识别 fence 的旧程序。

## 验收边界

自动测试使用临时合成 SQLite、实际 Click 命令和隔离 ASGI：旧 proof/查询/取消被拒绝、
新合法身份可受理（execution_authorized=false）、补偿停用、nonce 重放防护保留、
缓存 mapping 失效、双连接同计划竞争、计划变化/撤权拒绝、审计失败原子回滚、
schema 047 故障回滚及重试、提交后确认丢失与 status 对账。
未接触生产、未发送消息、未运行受限遥测；真实 Mattermost 双服务/密钥隔离、
PG 运维、旧版本混跑、部署验收、来源恢复 epoch 和容量回收仍未验证或实现。

本阶段执行记录：

- 维护/入站/签名源适配/数据库池/恢复镜像/proof/发布 API 组合回归：282 passed，2 条既有 ldap3 弃用警告。
- 最终维护专项：15 passed（与组合回归有重叠，另覆盖启用中 broker 迁移不受影响及错误确认拒绝）。
- `make lint-all typecheck-all` 通过；最终后端修改后 `make lint typecheck` 通过，mypy 674 文件。
  前端 ESLint 有 69 条既有 warning、0 error；本轮未修改前端。
- 初次测试的新 receipt 使用了不符合契约的字符串，测试被正确拒绝；修正为合法合成 HexID 后通过。
- 全量 `make all`、受限遥测、真实双服务、PG 与生产部署未运行。
