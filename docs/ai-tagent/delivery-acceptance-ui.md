# 人工交付摘要验收界面

独立组件 `dashboard/src/pages/Organizations/DeliveryAcceptance.tsx` 配合 `deliveryAcceptance.ts` 客户端。当前未接主页面，后续在成功运行条目的操作区集成：

```tsx
<DeliveryAcceptance
  currentUserId={currentUserId}
  target={{ organizationId, runId: run.run_id }}
  canReview={canManage && run.status === "succeeded" && run.requested_by !== currentUserId && (isAdmin || run.approved_by === currentUserId)}
/>
```

实际字段和身份须使用现有页面的真实上下文；前端筛选不是授权，GET/POST 服务端再次验证当前权限。必须传入真实数值 currentUserId；组件内部对组织、运行、权限和用户 ID 变化强制重新挂载，销毁旧证据与待确认状态，往返切换也不恢复；未经授权者不应看到入口。组件只有显式打开才取证，不预取。

## 行为

- 摘要以纯文本显示，不执行 HTML；显示证据和快照哈希。
- 用户选择“通过/要求修改”后，需二次确认不可覆盖的决定。
- POST 仅发送决定和两个服务端哈希，不上传正文、身份或权限。
- 本地同步锁阻止重复提交；关闭、运行或权限变化丢弃迟到响应。
- 失败、撤权、冲突或不匹配响应清除证据，显示结果不确定，不自动重试；重新打开重新取证。
- 已验收只展示决定、验收人和时间，不能覆盖。
- 关闭不保证已发出的决定被取消。未知结果必须重新读取，而不能当失败未发生。
- 执行成功不等于业务验收；摘要可能截断，不代表验证了全部产物，不自动完成目标或重跑。

中英文案在独立消息模块，不更改共享 locale。定向组件/API 测试不等同真实后端、浏览器或 PostgreSQL 验收。
