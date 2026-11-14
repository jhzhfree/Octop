# 人工交付摘要验收界面

组件 `dashboard/src/pages/Organizations/DeliveryAcceptance.tsx` 配合 `dashboard/src/api/modules/deliveryAcceptance.ts` 客户端，已在组织主页面 `dashboard/src/pages/Organizations/index.tsx` 的运行条目中接入。

页面仅在当前用户 ID 为安全整数时传入真实 `currentUser.id`，目标取自运行记录的 organization_id / run_id。入口条件为当前组织 owner/manager、运行 succeeded、非原请求者，并且当前用户是平台 admin 或原批准者。前端筛选不是授权，GET/POST 服务端仍验证当前权限；当前页面的 `canManage` 只取组织 owner/manager，不能把服务端支持平台 admin 理解为页面必然向非成员 admin 展示入口。

组件内部对组织、运行、权限和用户 ID 变化强制重新挂载，销毁旧证据与待确认状态，往返切换也不恢复；未经授权者不显示入口。组件只有显式打开才取证，不预取。以上为代码接入状态，真实登录、用户 ACL 与浏览器流程尚不能据此声称已验收。

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
