# 回执签名边界与现有巡检报告事务加固

2026-11-16，基线 `8959144341ae38bf07940f9e9a69dc41b0633567`。
本阶段推进离线认证原语与现有运行可靠性；没有开放堡垒机执行或回执入站 API。

## 为什么还不能直接接收入站回执

现有 connector Fernet 负责本机凭据加密，不能证明远端回执来自数字员工；其密钥
与应用数据库同属现有本机信任域，不能视为独立堡垒机签名根。当前也没有经过授权
的“组织/员工/外部主体/连接器版本/回执公钥”的持久化映射、密钥轮换及撤销账本。
不能从回执携带的公钥、用户 JWT 或 Agent ID 推导这种信任。

因此本轮不新增伪可信注册表，不允许任意客户端上传签名回执后写入真实验收证据。
签名回执尚未接入持久化防重放或场景报告。该缺口明确保留，真实联网执行继续关闭。

## 已交付：固定公钥的离线认证原语

`inspection_receipts.validate_signed_receipt` 复用上一阶段的严格解析与作用域校验：

- 仅接受调用方独立提供的 32 字节 Ed25519 公钥与 64 字节签名，不接受回执选择算法
  或密钥；先检查 64 KiB 上限，再验证签名，最后验证回执字段/绑定/时限。
- 签名消息是 `b"octop.inspection-receipt.v1\x00" + raw`，其中 raw 是原始 JSON 字节，
  不重新序列化；即使只是空格变化，也需要新的签名。该域前缀是本项目契约，尚未
  与任何厂商协商兼容，不宣称任意堡垒机原生支持。
- 错误公钥、损坏签名、跨协议签名和签名后修改均拒绝；签名正确也不能绕过身份、
  目标、操作、有效期和额外字段检查。签名通过不改变 simulation/unverified_external
  来源标识，不证明产物存在、实际命令执行或该公钥代表获授权员工。
- 此函数不保存私钥、不联网、不消费 request_id；重复纯校验允许。未来调用方必须
  从当前可信存储解析公钥并在最终提交事务重验授权/撤销，不能传入回执自带公钥。

所有签名测试使用内存中新生成的合成密钥，没有读取应用真实密钥或凭据。

## 已交付：复用现有运行与报告事务

审查发现 `execute_inspection` 生成报告后，`SceneInspectionRepo.finish` 已在事务中
重验当前账号/组织/Agent/岗位/目标/配置，但未检查生成正文时读取的子运行版本。
本轮记录每个已读取终态子运行的 version，提交时要求精确匹配持久化 batch 的来源
集合、关联关系、owner、job、终态及版本；PG 路径使用行锁，SQLite 使用既有
BEGIN IMMEDIATE。正文和场景报告哈希仍在同一事务提交，不另建报告系统。

现有 CAS 证据写入会递增 version；有正常并发写入改变子证据时，本轮提交拒绝。
这不是抵抗直接修改数据库且不维护版本的同 UID/数据库管理员的防篡改保证。
失败仍沿用现有 batch.fail，保留已经持久化的尝试，不自动重新采集。

持久化防重放复用既有 `scene_inspection_batches` 唯一 run claim 和来源关联，
没有新增 schema。测试验证两条独立 SQLite 连接竞争仅一个 claim 成功，且关闭后
重新打开连接仍拒绝重复运行，只产生一次采集。这是**现有 HTTP 巡检运行**的
防重放，不是堡垒机审计事件、跨 run 回执或外部命令的恰好一次保证。

## 隔离验证

合成 HTTP transport 与临时 SQLite 验证：签名篡改/错误公钥/身份错配/过期；
证据版本、owner、终态、关联变化；发布前撤销组织成员、禁用用户、撤销 tasks、
暂停员工；第二次数据库写失败触发事务回滚；双连接竞争、重开后重放；空库
巡检→报告→独立验收→要求修改→重新申请→再次验收。DNS/socket 外连被测试禁止。

复现命令：

```bash
UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 uv run pytest \
  tests/unit/enterprise_assistant/test_inspection_receipts.py \
  tests/integration/test_scene_inspection_execution.py \
  tests/integration/test_fresh_install_inspection_delivery.py \
  tests/unit/scenes/test_acceptance.py tests/unit/scenes/test_acceptance_api.py -q -rs
UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all
```

定向组合 114 项通过，2 条既有 ldap3/pyasn1 弃用提示；随后收紧撤权测试，
要求确认撤权实际成功，再重跑同 run 集成文件 32 项通过（与前述结果重叠，不累加）。
当前无失败或跳过。lint-all/typecheck-all 通过，mypy 666 文件，ESLint 69 条既有警告。
没有前端代码修改，不重复前端构建。PG 行锁仅代码路径，未做真实 PG/跨进程压力、
真实堡垒机/MCP/模型/AKB/Mattermost 联调。受限遥测和全量 make all 未运行，恢复
激活仍关闭。Docker/bubblewrap 本轮未重新探测，不引用此前版本作为当前部署验收。

## 下一步真实接线的必要设计

需先建立受控管理员注册的独立员工主体与公钥绑定、撤销/轮换、允许目标/操作及
当前策略版本；随后在现有运行 claim 中持久化逐次 request_id 和审计事件去重，
将当前授权复核、签名回执/产物校验、一次消费及报告提交纳入一致的事务边界。
外部调用无法随数据库回滚：超时必须保持未知并对账，不可自动重试。真实接口、
审计签名协议、测试身份和机器由授权环境提供，本轮不替用户假定这些条件已满足。
