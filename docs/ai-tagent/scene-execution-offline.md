# 企业场景单次执行链：隔离验证与兼容说明

## 结论与验收边界

选择“应用服务器只读晨检”为首个业务样例。本次验证的是一次场景运行的控制流程，而非真实服务器的巡检质量，也不是完整产品上线验收。

真实调用：运行申请 → 独立管理员审批 → Cron 创建/调度 → Gateway 会话串行锁 → CronDeliveryService → 流输出历史投影 → 运行状态同步 → 证据读取 → 独立人工验收。

模型流使用确定性 `OFFLINE FIXTURE` 替身，网络由测试 fixture 禁止。SQLite、会话、运行、投影及验收存储均使用生产代码；测试不直接写入 `queued`/`succeeded`，不注入证据，也不补写投影 ready。没有验证真实 AgentManager/harness checkpoint、外部模型、连接器、生产数据、PostgreSQL、浏览器或周期调度。

## 本次修复

1. 无计划申请以前可审批，但执行必然因快照无效失败。新申请缺少计划返回 `SCENE_PLAN_REQUIRED` / 422。计划仍须通过已有组织、员工、目标、ready 状态及能力验证。
2. 旧无计划或缺少有效工作主体/能力快照的待审批记录仍可读取和拒绝。尝试批准返回 409，不创建 Cron，不自动补造计划。用户需关联就绪计划重新申请。这是明确兼容变更，不是恢复旧无计划执行。
3. 内部 `cron` 会话使用进程内会话锁，结果保存在该运行转录，不路由至 IM 或 Dashboard 外发。误绑定外部 `channel_id` 的内部会话会被拒绝，不伪造通道标识。普通 IM/Dashboard 通路维持原有行为，推送失败仍向调用方传播。
4. 内部 Cron 转录必须成功写入：投影未就绪、存储异常、实际写入零条均导致运行失败，不将“生成了文本”误报为交付成功。零写入包括完全重复投影，保守视为失败；本次未实现幂等重试交付协议。
5. 支持的 `token/content` 与 `delta/text` 输出统一进入历史投影，避免运行文本存在而可验收证据缺失。

内部锁只提供单进程串行，不是分布式调度锁。取消、进程重启恢复、周期运行及跨进程重复调度不在此次验收结论内。独立验收 accepted 不自动完成目标，也不写入 AKB。

## 可重复验证

在仓库根目录，使用已有依赖：

```sh
UV_CACHE_DIR=/tmp/scene-repair-uv uv run --frozen --no-sync pytest \
  tests/integration/test_scene_execution_offline.py \
  tests/integration/test_enterprise_scenes_api.py \
  tests/unit/gateway/test_gateway_push.py \
  tests/unit/cron/test_cron_delivery.py \
  tests/unit/cron/test_cron_job.py tests/unit/i18n -q
```

`test_scene_execution_offline.py` 覆盖正常执行及 accepted/changes_requested、两类文本事件、自审批和重复审批拒绝、跨组织验收拒绝、无计划新申请与旧记录、模型异常/空输出、投影异常/未就绪/零写入、内部会话被绑定外部通道。旧 API 集成测试已迁移至就绪计划，保留两个审批者竞争的测试，并禁用网络。

`make all`、全量前后端门禁、真实环境联调未在此文档中宣称通过。测试精确计数与执行结果以本次交付记录为准。

## 后续业务验收

继续统一实际巡检数据与数字员工运行的关联，验证健康、异常、超时三种资产均可形成有来源和覆盖率的报告，再验证周期触发与重启恢复。模型网关、费用、知识沉淀等单独验收，不能由此次离线链路推定可用。
