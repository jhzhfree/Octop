export const deliveryAcceptanceMessages = {
  en: {
    open: "Review delivery summary",
    close: "Close review",
    title: "Human delivery review",
    boundary:
      "Execution success is not business acceptance. This summary may be truncated and does not verify all artifacts. Closing does not cancel a submitted decision. Decisions cannot be overwritten; requesting changes does not restart the run or complete its goal.",
    failed:
      "Review unavailable or result uncertain. Permission or evidence may have changed. Reopen to read the current state; no decision is retried automatically.",
    accept: "Accept summary",
    changes: "Request changes",
    confirm: "Confirm permanent decision",
    cancel: "Cancel decision",
    confirmation:
      "Confirm the decision for the displayed summary and snapshot? The decision cannot be overwritten.",
    accepted: "Accepted by a human",
    changes_requested: "Changes requested",
    reviewer: "Reviewer",
    time: "Reviewed at",
    evidence: "Evidence hash",
    snapshot: "Snapshot hash",
  },
  zh: {
    open: "验收交付摘要",
    close: "关闭验收",
    title: "交付摘要人工验收",
    boundary:
      "执行成功不等于业务验收。此摘要可能被截断，不代表已验证全部产物。关闭不保证取消已提交的决定。决定不可覆盖；要求修改不会自动重跑，也不会将目标标记为完成。",
    failed:
      "无法读取或提交结果不确定，权限或证据可能已变化。请重新打开读取当前状态；系统不会自动重试决定。",
    accept: "验收摘要通过",
    changes: "要求修改",
    confirm: "确认不可覆盖的决定",
    cancel: "取消决定",
    confirmation: "确认对当前展示的摘要与快照作出决定？保存后不可覆盖。",
    accepted: "人工验收通过",
    changes_requested: "要求修改",
    reviewer: "验收人",
    time: "验收时间",
    evidence: "证据哈希",
    snapshot: "快照哈希",
  },
};
