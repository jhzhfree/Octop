# 短 TTL 的有效续期窗口

2026-11-15，开发基线 `acb78fe10fd1d8700c78abfe39a04b93aa2bcd7d`。

原续期条件要求剩余时间严格小于 `max(1, floor(TTL / 3))`。TTL 为 2–5 秒时，
令牌剩余至少 1 秒仍不续期，进入 0 秒时又已经过期，因此没有有效续期窗口。

当前使用整数计算 `max(1, TTL // 3)`，剩余时间**等于**阈值即可续期。超过阈值仍
返回 204 且不签发令牌；剩余时间为 0 或负数拒绝，避免解码后读取用户期间跨过到期
边界仍进入签发。续期继续使用持久化角色、同一 sid，返回 no-store；普通请求不续期。
这不是把已过期令牌延长，也没有放宽撤销、禁用或删除账号检查。

配置 `access_token_ttl_seconds` / `OCTOP_ACCESS_TOKEN_TTL` 最小值现为 2 秒，
文件、环境变量及直接构造 OctopConfig 均验证。小于 2 秒明确失败，不静默调高；
默认 86400 秒不变。JWT 使用整数秒 iat/exp，1 秒有效期没有一个仍有效且能使新 exp
更大的整数秒窗口。内部 sign_token 作为通用签名工具仍可构造过期令牌用于测试，
这不是绕过应用配置验证的产品配置路径。

短 TTL 仅说明边界算法可用，不代表适合浏览器部署。最小活动检查间隔仍为 1 秒；
网络延迟、调度暂停、标签冻结或没有用户活动都可能使短令牌来不及续期。没有增加
后台心跳、自动重试或客户端自报活动权限，不能承诺“无感续期”。

## 验证边界

临时合成 ASGI 客户端覆盖 TTL 2、3、4、5、6、7、60、86400；测试同时控制签发时钟
和 JWT 验证时钟，验证阈值前不签发、恰好阈值签发且 exp 严格增加、同 sid、到期拒绝。
另覆盖解码后用户查询期间到期、非法配置来源，以及既有撤销/禁用/角色收紧与后台
流量不续期回归。没有真实凭据、外部请求或生产操作。

后续已增加[服务端续期限流](renewal-admission.md)；空闲/绝对会话期限、真实浏览器
和跨标签验收仍未实现；默认会话
生命周期策略需确定，不能把本修复当作 1.0 认证安全全部完成。全量 make all、testmon
和受限遥测不运行。本轮结果：

- `uv run pytest tests/unit/test_config.py tests/unit/api/test_jwt_tokens.py tests/integration/test_explicit_session_renewal.py tests/unit/auth/test_access_state.py -q`：76 passed，2 项既有 ldap3 弃用提示。
- 前端 activityRenewal、request.authToken、request.sessionRace、AccessSession 与敏感内容清理五个测试文件：65 passed。
- `make lint-all typecheck-all` 通过；mypy 661 个源文件，ESLint 69 warnings / 0 errors。
- 无测试失败；前端源码未改，本轮未重新构建前端制品。Docker/bubblewrap 未复测，未安装依赖或修改安全/网络设置。
