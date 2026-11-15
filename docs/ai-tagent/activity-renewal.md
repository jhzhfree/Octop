# 显式活动续期：后台流量不再保持浏览器会话

2026-11-15，基线 `20ce95e770f7c0cd923692b8f1ce71374565a749`。
本阶段解决普通请求隐式续期，不宣称实现服务端空闲/绝对期限或完成 1.0。

## 原因与协议

原中间件在所有已认证 HTTP 响应后尝试滑动续期，只排除 logout；状态检查、
更新状态/下载进度轮询、资源存在探测、上传/下载/流式响应以及业务错误都可能续期。
因此仅排除 access-state 或依赖客户端自报“后台请求”不够。

现在中间件只鉴权，任何普通 HTTP 请求都不产生续期令牌。只有新的
`POST /api/auth/renew` 接受 Header Bearer，重新校验到期/会话撤销和持久化账号，
使用数据库当前角色签发同一 sid 家族的令牌；不接受仅 query token 的续期请求。
原阶段按严格小于 TTL 三分之一签发；后续[短 TTL 修复](short-token-renewal.md)改为
剩余时间不超过 max(1, TTL // 3) 时签发，并要求配置至少 2 秒；否则 204 不带令牌。
响应 no-store，`X-Octop-Renew-Check-After` 给出下一次活动检查间隔
`max(1, min(60, TTL // 6))` 秒，照顾短 TTL；不是定时重试指令。
现有允许的 CORS 配置暴露该响应头，不新增来源或放宽鉴权。

登录、SSO、邀请、初始化仍走各自已有的新会话签发逻辑。Bridge 的内部 owner token
签发是另一个服务身份机制，没有被当成浏览器用户活动或在本轮改造。
WebSocket/SSE 持续连接不会通过这套机制续期；已建立连接的终止策略未改变。

## 前端行为与竞争

- 一个 AccessSession 安装一个活动监听器。只有已通过权限重验、页面可见，且收到
  `isTrusted` 的 pointerdown/keydown（非按键自动重复）才尝试续期。
  mount、focus、pageshow、visibility 恢复、定时器和后台 API 完成都不是续期触发源。
- 默认每标签最多每 60 秒尝试一次；短 TTL 按成功响应提供的 1–60 秒间隔节流。
  同时最多一项在途请求，10 秒超时取消，失败没有重试定时器。极短 TTL、网络耗时、
  长时间阅读无操作、辅助输入或嵌入 iframe 内操作可能仍需重新登录，不能保证无感续期。
- 隐藏/pagehide/卸载取消在途结果；权限重验未通过时不发请求，也不采纳迟到结果。
  仅成功响应且请求时的令牌仍与当前存储精确一致时写入，保持 remember/session-only 模式。
  登出清理、新登录、其他标签续期后，旧响应不能覆盖新令牌或恢复已清理的令牌。
- localStorage 会话仍跨标签共享：一个活跃标签续期后，其他标签共享该家族的新有效期。
  sessionStorage 保留现有单标签语义。不声称每个标签有独立服务端空闲时钟。
- 普通 request/blob/probe/stream/upload 一律忽略续期响应头；新前端连旧后端时，
  后台响应不会更新本地令牌，新接口 404 也不回退到自动续期。
- 旧前端/客户端连新后端仍可使用尚未到期的令牌，普通功能协议不变；到期需重登。
  依赖原隐式续期的第三方客户端需迁移到显式协议，不能静默维持旧无限续期行为。

`isTrusted` 只是官方前端减少误触发的 UX 条件，不是后端可信人类身份或审批证据。
持有有效令牌的自定义客户端可以主动调用 renew，服务端没有绝对家族寿命或空闲计时；
因此本阶段不能抵御恶意客户端主动保持会话。操作审计不能据此标为“人工本人”。

登出仍撤销当前家族，独立登录不受影响。在签发过程中并发登出，即便响应携带了新
令牌，新令牌同属已撤销家族，后续请求仍拒绝。既有已入场请求、开着的流不会被撤回。
账号状态读取与签发不是全局原子事务；后续资源鉴权和权限定时重验仍不可省略。
未修改其他普通请求已有的全局 401 处理策略，不宣称所有跨登录迟到错误均已解决。

## 验证与发布限制

测试使用临时合成账号/密钥、ASGI 和 jsdom，无真实外部服务。jsdom 的可信事件分支
通过直接调用捕获的监听器验证，不伪称浏览器真人交互验收。OpenAPI schema 检查了
新路由的文档，未启动浏览器访问真实部署。

前端命令（dashboard 目录）：
`npm test -- src/api/activityRenewal.test.ts src/api/request.authToken.test.ts src/components/AccessSession.test.tsx src/components/AccessSession.sensitive.test.tsx src/components/AuthGuard.test.tsx src/pages/Organizations/RunEvidence.test.tsx src/pages/Organizations/RunOperationTrace.test.tsx`：55 passed。
覆盖不触发场景、节流/超时、迟到响应、会话存储、权限未确认、跨组织报告清理及既有路由导航。

Python 命令均使用 `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1`。
定向组合为 `uv run pytest tests/integration/test_explicit_session_renewal.py tests/integration/test_logout_revocation.py tests/unit/api/test_jwt_auth_middleware.py tests/unit/api/test_jwt_tokens.py tests/unit/auth/test_access_state.py tests/integration/test_scene_delivery_evidence.py tests/integration/test_scene_operation_trace.py -q`。
初次两个夹具问题（OpenAPI 开关、删除最后管理员触发初始化锁定）修正后 68 passed；
后加短 TTL 用例首次因不可变配置赋值失败，其余 68 项通过，修正夹具后单独重跑
`uv run pytest tests/integration/test_explicit_session_renewal.py -q`：10 passed。
其余 59 项在上述组合中通过；最终所有选定用例均通过，没有尚未解决的测试失败。

`make lint-all typecheck-all` 通过：Ruff、Prettier、mypy（659 个源文件）、TypeScript；
ESLint 0 errors / 69 warnings。后端有两项既有 ldap3/pyasn1 弃用提示，前端有既有 jsdom/act 提示。
`npm run build` 最终通过，有既有 chunk/Browserslist 提示。
不运行全量 make all、testmon 或受限遥测测试。
本轮未安装依赖、改变安全/网络设置、运行生产服务、发送消息、合并或部署。
真实浏览器多标签/冻结/输入法/辅助输入、生产负载、部署及模型/AKB/Mattermost仍待验收。

1.0 还须确定服务端空闲及绝对会话生命周期。备份恢复的 ACL/JWT 密钥回退、锁外密钥
写回及跨资源故障回滚仍是独立安全阻断，撤销摘要合并不等于所有旧令牌都不能复活。

## 后续独立审查：仍未修复

- TTL≤5 秒时整数阈值为 1，合法令牌可能没有可用续期窗口。
- 前端节流不是服务端限流；持令牌客户端可以自行持续请求 renew。
- localStorage 的相等检查与写入不是跨标签原子 CAS，不能宣称多标签无竞态。
- 当时遗留的普通请求迟到 401 问题已由后续[会话归属保护](stale-auth-responses.md)修复公共封装范围；跨标签原子性及其他响应路径仍未解决。

以上与服务端强制 idle/absolute 生命周期一起列入 1.0 安全可靠性待办。
