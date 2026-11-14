# 数字员工知识映射基础（本轮实现）

这是恢复工作区上的新实现，不是原云工作区代码找回。

## 已实现

- `033_worker_knowledge_mappings` 提供 SQLite/PostgreSQL 迁移文件；不重写旧能力 JSON、场景或运行快照。
- `WorkerKnowledgeMappingService` 提供显式草稿创建、检查和按 revision 撤销。HTTP 入口见下文；UI 另行交付。
- 创建要求组织管理者、同组织员工、激活状态、有效岗位任命、可访问 Agent、现有岗位知识能力和启用场景。
- 外部来源必须是明确 ID，AKB Agent 模式，KB ref 精确一致，来源/连接可使用且启用，并显式挂载在该 Agent。不会按名称自动匹配。
- 保存服务端规范化能力摘要、员工/能力/来源/连接版本；插入原子复核持久化版本并阻止重复映射。
- 检查会重新读取可检测的本地权限与快照；撤销记录不可恢复，旧 revision 不能覆盖新状态。撤销后可以显式创建全新草稿，重新验证权限和预期版本，在父资源仍存在时保留旧记录。

## 严格边界

记录只有 `draft` 和 `revoked`。所有结果固定 `retrieval_enabled=false`，`authorization_status=delegated_user_acl_unverified`。`local_snapshot_current` 仅表示本地数据与保存的快照相符，不是实时 AKB 授权或可检索许可。

本轮没有用户 ACL 验证、实际身份联调、检索调用、凭据创建、能力授权、模型知识增强或自动执行。不读取或返回凭据。

鉴权读取与插入不是跨所有资源的串行化事务；在并发撤权后可能保存一个立即失效的 **草稿**，但它没有读取权限。每次检查重新验证；将来启用检索必须重新完成实际身份、双用户 ACL、资源版本及撤权门槛，不能使用本地 `draft` 或 `local_snapshot_current` 代替授权。

## 历史与审计保留限制

映射表对组织、员工、外部来源和连接使用 `ON DELETE CASCADE`。删除这些父资源会级联删除对应草稿和撤销记录；这里的“保留旧记录”仅指正常撤销与重新草拟，不是永久历史或不可篡改审计承诺。

创建和撤销通过现有 `AuditRepo.write` 向独立 `audit_log` 写入主体、动作、映射 ID 和时间。该日志没有引用映射或父资源的外键，因此父资源删除不会以外键方式删除这些事件。但事件不含完整映射快照，且审计仓库提供 `delete_before` 清理方法，所以它既不能恢复完整映射，也不保证永久保留。

当前映射写入与审计事件写入是两个事务；不能宣称两者原子提交或绝无日志缺失。本轮不扩展审计架构、保留期限或删除策略。

## 验证范围

定向测试覆盖 SQLite 迁移重复运行、持久化、重复映射、跨组织和普通成员拒绝、乐观锁、连接/来源/岗位/Agent 挂载撤销失效、陈旧版本插入拒绝。不运行受阻的全量测试，不调用外部服务。PostgreSQL 迁移文件已提供，真实 PostgreSQL 迁移尚未运行。

## 第一轮：显式映射管理 HTTP API（2026-11-14）

基址：`/api/organizations/{organization_id}/digital-workers/{worker_id}/knowledge-mappings`。

- `GET` 基址：组织管理者分页读取，`limit` 默认 50、最多 100，`offset` 默认 0；响应为 `items/limit/offset`，按记录新到旧排列。列表仅返回持久状态，`local_snapshot_current=null` 表示未检查，不逐条重查场景；显式单项 GET 才检查本地新鲜度并返回布尔值。并发新增/删除可能使 offset 分页移动，管理界面刷新后重新读取，不用于一致性导出。
- `POST` 基址：仅接受 MCP server、KB ref、外部来源 ID 和预期能力/连接/资源版本，创建草稿。拒绝多余的权限、身份或凭据字段，重复草稿和版本变化返回 409。
- `GET /{mapping_id}`：组织管理者检查当前本地快照；跨组织员工/映射不能通过此 URL 访问。
- `POST /{mapping_id}/revoke`：仅接受 `expected_revision`，原子撤销，陈旧或重复撤销返回 409。重新草拟必须显式创建新草稿并重新验证。

所有路由均为同步薄适配层，由 FastAPI 线程池执行同步 DB 服务，不在事件循环直接进行 SQLite 操作。响应使用明确 schema，排除内部整数主键，不包含连接凭据、endpoint、签名或知识内容。错误说明支持中英文。

**管理 API 仅管理映射草稿，并非检索 API。管理者读取本地映射的权限不等于 AKB 用户 ACL。** 无远端请求、无 AKB 授权同步、无来源启用操作；`retrieval_enabled` 永远为 false，`authorization_status` 固定为 `delegated_user_acl_unverified`。本轮未验证 PostgreSQL 实际部署、真实 AKB 用户 ACL 或跨产品联调。定向 HTTP 测试使用真实 SQLite 仓库和领域服务，不使用真实模型或 AKB。

此 API 对非管理者先检查成员资格，已有与不存在组织均返回相同 403，不泄漏组织存在性；公共数字员工 API 语义未改动。
