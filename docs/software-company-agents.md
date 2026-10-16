# 软件公司岗位 Agent 与团队配置

本目录按 Octop 当前架构提供可从“专家库”复制创建的岗位模板。岗位模板是 Agent 的职责、工作方法和输出约定；不会自动创建用户、组织部门、账号、权限或工作流。

## 岗位图谱

| 部门 | 可选岗位模板 |
|---|---|
| 经营管理 | 软件公司总经理 `software-general-manager` |
| 产品与项目 | 产品经理 `software-product-manager`、项目经理 `software-project-manager` |
| 研发 | 架构师 `software-architect`、后端工程师 `software-backend-engineer`、前端工程师 `software-frontend-engineer` |
| 质量与交付 | 测试工程师 `software-qa-engineer`、发布经理 `software-release-manager`、运维/SRE（复用 `ops-engineer`） |
| 安全与数据 | 应用安全工程师 `software-security-engineer`（或复用 `ai-safety-guardian` 做 AI 安全评审）、数据分析师 `software-data-analyst` |
| 设计与客户 | 体验设计师 `software-ux-designer`、客户成功与技术支持 `software-customer-success`、售前解决方案工程师 `software-sales-engineer` |
| 市场与企业支持 | 产品营销 `software-marketing-manager`、HR `software-hr-partner`、财务 `software-finance-analyst`、法务合规 `software-legal-compliance`、技术文档/培训 `software-technical-writer`（亦可复用 `office-automation`） |

企业可复制模板后按本地岗位说明、流程、产品、术语、SLA 和输出模板修改 Agent 工作区。模板没有连接器要求，也不会授予任何系统权限；按需在现有 Agent 配置中单独添加经批准的技能、插件、知识库、MCP/Connector 和模型。

## 用现有功能组建协作团队

1. 在“专家库”复制所需岗位模板，创建岗位 Agent；岗位可按组织需要重复创建多个实例。
2. 给每个 Agent 配置单独的模型、知识库、工具、MCP/Connector 和访问范围。共享专家仍遵循现有分享权限。
3. 在“我的团队”创建团队主持人，从允许使用的普通专家中选择至少两名成员。建议一个团队对应一类稳定工作单元，例如产品交付小组或运维响应小组。
4. 在团队工作区写清楚工作目标、分工、派工格式、证据要求和审批边界；用实际任务试跑并观察成员回答。
5. 需要定期工作时，使用现有 Cron/任务页给有权限的 Agent 配置提示词、计划和通知目标；确认创建状态后再依赖该计划。

例如，一个 SaaS 交付小组可由项目经理、产品经理、架构师、后端工程师、前端工程师、测试工程师和运维/SRE 组成；故障响应小组可由运维/SRE、应用安全工程师、客户支持和技术文档岗位组成。团队主持人接收需求、派工和汇总，成员保留各自工作区。

## 当前架构边界

- Team 成员目前只能是有权限使用的普通专家 Agent，不能将 Octop 用户账号直接列为成员；人类员工仍通过自己的 Octop 账号协作和访问授权的 Agent。
- Team 是平面成员名单，不支持部门树、岗位编制关系、汇报线或嵌套团队。可先按现有 Team 建立工作小组，但不应把它描述成完整的 HR/组织管理系统。
- Agent Team 通过成员 Agent 做协作，不会继承主持人或其他成员的工具授权；调用仍应满足成员的权限和运行状态。
- 定时任务、外部通知与安全扫描仍使用 Octop 已有 Cron、Connector/MCP 和权限配置。岗位模板本身不会创建这些集成或自动执行审批。
- 职责、岗位与 Octop 系统角色/RBAC 完全分离。不得依据“管理员”“财务”等岗位名授予系统权限。

以上约束是后续组织模型的兼容要求：引入真实部门/岗位/任职实体时，应继续引用 Agent 与 user 的稳定 ID，保持现有 Agent 所有权、分享规则和团队执行权限校验。
