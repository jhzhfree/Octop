export const deliveryAcceptanceMessages = {
  en: {
    feedback: "Read acceptance feedback",
    feedbackEmpty: "No acceptance decision yet.",
    note: "Review feedback (up to 2000 characters)",
    noteMissing: "No feedback recorded (including legacy decisions).",
    noteRequired: "Explain what needs to change before confirming.",
    sources: "Evidence sources for this review",
    sourceBoundary:
      "Source labels describe persisted evidence, not an autonomous workflow. Acceptance is your explicit human decision. No Mattermost delivery or AKB write is performed here.",
    same_run_http_report:
      "Automatic local report · same-run one-time HTTP inspection (no LLM)",
    assistant_summary:
      "Stored assistant summary · model/tool execution not verified by this text",
    owner_shared_report:
      "Manually shared report snapshot · not proof of same-run generation",
    sourceRun: "Source run",
    sourceHash: "Source text SHA-256",
    sourcesUnknown:
      "Source metadata unavailable from this server. Do not infer manual or automatic generation from the text.",
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
    feedback: "查看验收反馈",
    feedbackEmpty: "尚无验收决定。",
    note: "验收意见（最多 2000 字符）",
    noteMissing: "未记录验收意见（含历史决定）。",
    noteRequired: "确认前请说明需要修改的内容。",
    sources: "本次验收的证据来源",
    sourceBoundary:
      "来源标识描述已保存的证据，不代表自主工作流。验收由你明确作出人工决定；此处不发送 Mattermost 消息，也不写入 AKB。",
    same_run_http_report: "自动生成本地报告 · 同次单次 HTTP 巡检（无模型调用）",
    assistant_summary: "已保存的助手摘要 · 此文本不证明模型或工具执行已验证",
    owner_shared_report: "人工共享报告快照 · 不证明由本次运行生成",
    sourceRun: "来源运行",
    sourceHash: "来源正文 SHA-256",
    sourcesUnknown: "服务端未提供来源元数据，请勿仅凭正文推断人工或自动生成。",
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
