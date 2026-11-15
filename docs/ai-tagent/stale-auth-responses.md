# 迟到响应不得清理后续登录

2026-11-15，开发基线 `d2d89dfcfde9e185c87e1e2c735c92af98e34d43`。

## 修复范围

原公共请求封装收到普通路径 401 后会无条件清理当前令牌并跳转；收到带
`setup_required: true` 的 503 也会清理令牌并设置全局初始化锁。旧请求在注销、
重新登录或显式续期之后才返回时，会影响已经改变的会话。

现在每个请求记录当时的令牌、本标签登录代次及实际发送的 Authorization 是否匹配
该令牌。只有响应处理时仍属于同一代次、同一令牌且认证头相符，才允许清理令牌、
清理活动 Agent、设置初始化锁或发出登录导航事件。503 在异步解析响应体之后再次
判断归属，防止解析期间发生登录切换。同标签退出后用相同令牌重新登录，也会因
代次不同而拒绝旧响应的全局副作用。

覆盖 JSON、Blob 下载、资源探测、流式请求和 XHR 上传。各通道统一按 HTTP 头名称
不区分大小写合并 Headers/键值数组/对象，调用者的 Authorization 覆盖默认值；
独立向导令牌的失败不能清理存储中的另一个登录令牌。新登录重置导航去重状态，
保证下一次真实失效仍能导航。

旧请求本身仍然抛出错误；没有把失败伪装为成功，也不自动重放可能有副作用的请求。
当前会话真正收到 401/初始化锁 503 时，原有退出、清屏与导航行为保留。原先由调用者
负责的 `/auth/*` 401 策略不变。

## 验证与边界

新增回归使用受控延迟 fetch/XHR 和异步 503 响应体，覆盖五种通道的旧 401/503、
当前 401/503、相同令牌重登录、匿名请求后登录、另一个标签写入不同令牌、自定义
Authorization、新登录后的导航重新触发；验证活动 Agent 与 session-only 登录
不被旧响应清理。另回归显式续期、AuthGuard、AccessSession 撤权/敏感内容清理及
初始化向导。本轮验证：

- `npm test -- src/api/request.sessionRace.test.ts src/api/request.unauthorized.test.ts src/api/request.setup.test.ts src/api/request.authToken.test.ts src/api/activityRenewal.test.ts src/components/AuthGuard.test.tsx src/components/AccessSession.test.tsx src/components/AccessSession.sensitive.test.tsx src/pages/Setup/wizardClient.test.ts`：9 个文件、79 项通过；新增竞态测试 31 项。
- `npm run build` 通过，保留既有构建体积/依赖提示。
- `make lint-all typecheck-all` 通过；mypy 661 个源文件，ESLint 69 warnings / 0 errors。
- 初次 lint 因测试替身中的 this 别名失败，已改用测试状态容器并重跑上述 79 项通过。
- 未修改后端实现，没有重跑后端 pytest；不将历史后端结果作为本轮证据。

限制：登录代次仅在本标签内存维护；localStorage 比较和清理不是跨标签原子 CAS，
不保证跨标签 ABA（令牌变更后又变回原值）或检查与写入之间的并发行为。此改动仅
保护公共封装上述 401/503 的全局副作用；不替代各页面的迟到成功响应、403 事件、
独立 fetch 或组件自身错误处理的生命周期控制。不是服务端 idle/absolute 超时、
会话全撤销或真实浏览器多标签验收，服务端授权仍是权限边界。

本阶段未改后端认证协议，未触达真实凭据、生产或外部消息服务。全量 make all、
testmon、受限遥测和真实外部联调未运行。恢复激活/ACL 映射/一致性与回滚阻塞仍保留。
