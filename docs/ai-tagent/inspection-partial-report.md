# 企业只读晨检：异常与采集缺口报告

## 本轮范围

本轮只改进 `enterprise_assistant` 巡检及汇报工作流，不表示它已和组织数字员工的 `scene_run`、计划及人工验收记录统一。没有启动实际巡检、访问生产地址或发送报告。待发报告仍需要既有人工确认，所有外发开关、目的地址白名单和配置版本校验保留。

## 语义

- HTTP 健康接口正常返回的业务异常是事实，不是连接失败。`ok/healthy/up/pass` 归一为 `healthy`；`degraded/unhealthy/down/fail/failed/critical` 归一为 `abnormal`；缺失、无法识别及显式未知状态统一为 `unknown`。不保存未经识别的原始状态文本。
- `succeeded` 表示采集执行成功，不表示被巡检对象健康。HTTP 错误、超时或格式错误仍令源运行 `failed`，不会被改成成功。
- 除健康状态和可信配置名称外，仅接受 `cpu_percent`、`memory_percent`、`disk_percent` 的有限 0–100 数值；布尔、字符串、越界、NaN/Infinity 不保留。指标不自动推导健康或修复动作。
- 健康事实带采集时间；报告按源运行 ID 和尝试结束时间引用数据。来源任务引用其配置连接，不把地址和认证材料复制进报告。
- 报告可纳入**最新的 failed 巡检源**，但只写“巡检失败，无法得出当前健康结论”，不引用旧成功结果或原始错误字符串。Jira/OA 失败仍阻止汇报，不改变其原有语义。
- 报告按去重后的**巡检来源任务数**统计采集覆盖率，并区分 healthy/abnormal/unknown/unavailable。它不是资产总数或逐项指标覆盖率。成功的 SSH 原始事实没有健康裁决，计为健康未知。
- 仍要求来源在七天内、源任务和连接版本与采集快照一致。缺失、过期、未结束或配置变化不会被伪装为当前数据。七天是原有汇报时效，不保证数据采自当天；晨检生产验收仍需收紧其业务新鲜度。

## 可复现隔离场景

`tests/unit/enterprise_assistant/test_workflows.py::test_morning_inspection_partial_report_real_collect` 使用真实采集/工作流/SQLite，仅替换 HTTP 传输为 MockTransport：

1. A 返回 healthy、CPU 12%；B 返回 degraded、磁盘 95%；C 超时。
2. A、B 采集状态 succeeded，C 保持 failed/UPSTREAM_UNAVAILABLE。
3. 汇报草稿记录覆盖率 2/3、健康 1、异常 1、未完成采集 1，包含三次源运行编号及时间。
4. 任意上游 password/logs/错误内容不进入报告，发送适配器未被调用。

补充测试覆盖未知/恶意状态、无效指标、最新失败不回退旧成功、重复来源、历史 `ok` 兼容、连接配置变化，以及非巡检失败仍拒绝。

## 验证边界

仅定向离线测试，不等于真实服务器、模型、周期调度、PostgreSQL 或浏览器端到端验收。完整企业闭环仍需将这些事实关联到统一数字员工运行记录，并验证每个工作日调度、失败恢复、当天新鲜度、人工审核和领导视图。
