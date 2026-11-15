export const mattermostPublicationMessages = {
  en: {
    open: "Mattermost publication review",
    close: "Close publication review",
    create: "Prepare local publication preview",
    refresh: "Read current publication",
    boundary:
      "Not connected. Nothing has been sent to Mattermost. Approval only records a local intent; it does not authorize execution or enable delivery.",
    prerequisite:
      "This view reads an existing server-generated preview of this run’s independently accepted inspection report. Only the requester can prepare a local preview using server-verified report and configuration versions. No credentials are entered here.",
    failed:
      "Unavailable or result uncertain. The preview may be missing, stale, or no longer authorized. Read the current record before another decision; no automatic retry or sending occurs.",
    loading: "Processing…",
    content: "Exact server-generated report content",
    destination: "Configured destination (unverified)",
    instance: "Instance alias",
    team: "Team ID",
    channel: "Channel ID",
    bot: "Bot ID",
    proposer: "Proposed by (user ID)",
    approver: "Approved by (user ID)",
    hash: "Publication fingerprint",
    reportHash: "Report fingerprint",
    approve: "Approve local publication intent",
    revoke: "Revoke local publication intent",
    confirm: "Confirm decision",
    cancel: "Cancel decision",
    approveConfirm:
      "Approve this exact report and configured destination? This is separate from report acceptance and will remain blocked, not sent.",
    revokeConfirm:
      "Permanently revoke this local intent? It cannot be restored. Nothing will be sent.",
    independent:
      "Only the original acceptance reviewer or a current administrator may approve. The requester/proposer cannot approve their own publication. The server rechecks all permissions.",
    pending_approval: "Awaiting independent publication approval · not sent",
    blocked_not_connected:
      "Approved locally · blocked: not connected · not sent",
    revoked: "Revoked locally · not sent",
    unknown: "Unknown outcome · no retry or further decisions permitted",
  },
  zh: {
    open: "Mattermost 发布审阅",
    close: "关闭发布审阅",
    create: "准备本地发布预览",
    refresh: "读取当前发布记录",
    boundary:
      "未连接，尚未向 Mattermost 发送任何消息。批准仅记录本地意向，不授予执行权限，也不会启用发送。",
    prerequisite:
      "此入口读取本次运行已独立验收的巡检报告所生成的服务端预览。仅运行发起者可使用服务端核验的报告与配置版本准备本地预览。不在此输入任何凭据。",
    failed:
      "记录不可用或操作结果不确定。预览可能不存在、已过期或权限已变化。再次决定前请读取当前记录；不会自动重试或发送。",
    loading: "处理中…",
    content: "服务端生成的报告原文",
    destination: "已配置的目标（未验证）",
    instance: "实例别名",
    team: "团队 ID",
    channel: "频道 ID",
    bot: "Bot ID",
    proposer: "提议人（用户 ID）",
    approver: "批准人（用户 ID）",
    hash: "发布指纹",
    reportHash: "报告指纹",
    approve: "批准本地发布意向",
    revoke: "撤销本地发布意向",
    confirm: "确认决定",
    cancel: "取消决定",
    approveConfirm:
      "批准这份报告原文及已配置目标？此审批独立于报告验收，批准后仍被阻止，不会发送。",
    revokeConfirm: "永久撤销这条本地意向？撤销后不可恢复，不会发送任何消息。",
    independent:
      "仅原验收人或当前平台管理员可批准。运行发起者及提议人不能批准自己的发布；服务端会重新验证全部权限。",
    pending_approval: "等待独立发布审批 · 未发送",
    blocked_not_connected: "本地已批准 · 未连接而阻止 · 未发送",
    revoked: "本地已撤销 · 未发送",
    unknown: "结果未知 · 禁止重试或继续决定",
  },
};
