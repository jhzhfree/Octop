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

## 逐次证据来源标识（2026-11-15）

组织待办和原场景运行页复用的人工验收界面，现在逐项展示服务端解析的
`evidence_sources`：来源类型、来源运行编号和该段正文的 SHA-256。

- `same_run_http_report`：同次单次 HTTP 巡检自动生成的本地报告。只有已保存批次
  为 reported、转录正文与批次正文一致、内容 hash 与场景绑定一致时返回此类型。
  不调用模型；这是本地来源与完整性证据，不是外部系统生产可用性认证。
- `assistant_summary`：该运行隔离转录中的可见助手摘要。可能被原有长度限制截断；
  不据此声称模型/工具调用已真实验证，也不推断自主或人工触发模式。
- `owner_shared_report`：申请人明确共享的报告快照，单独显示原报告 run ID；
  不把历史报告的人工关联冒充同次自动生成。hash 对应该快照正文，不包含组合标记。

最终 `evidence_sha256` 仍覆盖完整验收文本（包括手工附件标记），原有
snapshot hash、独立人员资格、当前权限复查及确认后提交协议保持不变。
来源元数据由服务端生成，不接受客户端提交来源声明。正文/快照变化仍需重新读取；
错误、组织/身份/权限切换继续清空旧验收会话。缺少来源元数据的旧服务端显示
“来源未知”，不猜测生成模式。前端拒绝错误同次 run ID、未知类型、非法指纹、
错误来源顺序或单来源指纹与整段证据不一致的响应。

这一步仅补齐验收前的来源证据，不实现周期自动模式、完整逐动作审计时间线或
自动验收。验收仍是明确人工决定；Mattermost 发布预览/审批边界保持不变，
无真实发送、AKB 写入或生产操作。无需新依赖、迁移或环境安全设置变更。

验证记录：

- `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 uv run --frozen pytest
  tests/unit/scenes/test_acceptance.py tests/unit/scenes/test_acceptance_api.py
  tests/integration/test_scene_inspection_execution.py
  tests/integration/test_scene_report_attachment.py -q`：**70 通过**。
  集成使用原有禁网 fixture、MockTransport 与确定性模型替身；没有真实服务调用。
- `npm test --` 指定 `DeliveryAcceptance.test.tsx`、`RunQueue.delivery.test.tsx`、
  `deliveryAcceptance.test.ts` 和 `MattermostPublication.test.tsx`：最终 **57 通过**。
  首轮暴露来源区域与确认框的测试选择冲突、参数化用例展开错误，修复后重跑通过。
- `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1 make lint-all typecheck-all` 通过；
  最终修改的前端文件另经 ESLint 与 `npx tsc -b` 通过。OpenAPI 测试验证来源类型、
  必需字段及字段说明；未宣称浏览器 `/api/docs` 视觉验收完成。
- 未运行 `make all` 全量 pytest 或 testmon，不重试此前被拒绝的遥测测试。
  未验证真实模型、Mattermost、AKB、PostgreSQL 或浏览器 E2E；未合并、部署。
- 最终 `npm run build` 通过，产物按仓库规则保持忽略；Vite 有既有 chunk/import
  警告，jsdom 有伪元素样式支持警告，均不等于浏览器验收。`git diff --check` 通过。
