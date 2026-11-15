export const reportAttachmentMessages = {
  en: {
    open: "Link an inspection report",
    close: "Close report link",
    reportId: "Report run ID",
    preview: "Preview my report",
    confirm: "Share and attach this snapshot",
    cancel: "Cancel preview",
    boundary:
      "Manually attach a report you own to this run. The server verifies the same Agent and inspection sources. This does not execute a task, approve sending, or accept delivery.",
    sharing:
      "Confirm sharing this exact report snapshot with the authorized independent reviewer of this run. One immutable attachment is allowed. It cannot be replaced, and no attachment is allowed after a review decision. Closing does not cancel a submitted request.",
    failed:
      "Unavailable or result uncertain. Permission, report, or review state may have changed. Close and reopen to read the current state. No write is retried automatically.",
    attached: "Report snapshot attached (not a delivery acceptance)",
    loading: "Loading…",
    hint: "Copy the report run ID from your enterprise assistant reports. Preview does not share it.",
    version: "Version",
    hash: "Content hash",
  },
  zh: {
    open: "关联巡检报告",
    close: "关闭报告关联",
    reportId: "报告运行 ID",
    preview: "预览我的报告",
    confirm: "共享并附加此快照",
    cancel: "取消预览",
    boundary:
      "手动将本人报告附加至此运行。服务端验证同一 Agent 和巡检来源；此操作不执行任务、不批准发送，也不代表交付验收。",
    sharing:
      "确认将当前报告快照共享给此运行获授权的独立验收人。每次运行仅允许一个不可变附件，不能替换；已有验收决定后不能再附加。关闭不保证取消已提交的请求。",
    failed:
      "无法访问或提交结果不确定，权限、报告或验收状态可能已变化。请关闭后重新打开读取当前状态，系统不会自动重试写入。",
    attached: "已附加报告快照（不代表交付验收）",
    loading: "加载中…",
    hint: "从企业助手报告中复制报告运行 ID。预览不会共享报告。",
    version: "版本",
    hash: "内容哈希",
  },
};
