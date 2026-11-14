export const serverReadinessMessages = {
  en: {
    title: "Server prerequisite observations",
    inspect: "Check current snapshot",
    boundary:
      "Read-only local observation, not execution permission. Unknown checks are not passed. Changes after observation are not detected automatically.",
    failed:
      "Observation unavailable, forbidden or stale. Reload the plan and check your access before retrying.",
    satisfied: "Observed",
    unmet: "Not met",
    unknown: "Unknown / unverified",
    worker_active: "Worker active",
    goal_active: "Goal active",
    plan_ready: "Plan marked ready",
    plan_content_complete: "Plan content complete",
    agent_enabled: "Agent enabled",
    agent_appointed: "Agent appointed",
    matching_scene_enabled: "Matching scene enabled",
    runtime_approval: "Runtime approval",
    knowledge_user_acl: "Knowledge user ACL",
    model_budget: "Model budget",
  },
  zh: {
    title: "服务端前置条件观察",
    inspect: "检查当前快照",
    boundary:
      "只读本地观察，不代表执行许可。未知项不算通过。观察后的变化不会自动检测。",
    failed:
      "无法获取观察结果、权限不足或版本已过期。请刷新计划并确认权限后重试。",
    satisfied: "已观察到",
    unmet: "未满足",
    unknown: "未知／未验证",
    worker_active: "分身已启用",
    goal_active: "目标进行中",
    plan_ready: "计划标记为就绪",
    plan_content_complete: "计划内容完整",
    agent_enabled: "智能体已启用",
    agent_appointed: "智能体已任职",
    matching_scene_enabled: "匹配场景已启用",
    runtime_approval: "运行审批",
    knowledge_user_acl: "知识库用户权限",
    model_budget: "模型预算",
  },
};
