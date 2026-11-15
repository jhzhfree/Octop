# 权限定时重验与敏感内容清理

2026-11-15；`feature/akb-octop-litellm-dev`；开始时本地/远端均为
`1c391037103f40c6f72681b4daac96f29d9d2c02`，工作区干净。无新增依赖或数据库迁移。

## 可信数据来源

原 AuthGuard 只在启动/离线重试时调用 `/auth/me`。该端点使用请求身份（可能来自进程内
UserManager），不包含组织成员角色变化。因此新增轻量定时只读端点：
`GET /api/auth/access-state`，只接受当前认证账号，不接受页面自报角色、权限或用户 ID。

领域服务直接读取数据库账号（包括禁用状态）和该账号的全部组织成员 ID/角色。
返回最小用户档案、当前有效模块权限和这些权限事实的 SHA-256 变化标记。
不返回组织名称、报告、原始审计、密码、令牌或组织成员列表。变化标记不是授权凭证。
缺失/禁用账号返回 401；查询期间账号改变返回 409（禁用除外），不把普通资料/权限变化
误判为会话撤销。正常响应和领域错误均 no-store，客户端同样禁用缓存。

数据库查询为两次按主键读取账号及一次按 user_id 读取成员角色；后者已有索引。
不遍历运行历史、不调用模型、Connector、SSO、头像文件或外部服务。接口使用既有认证
会话校验；关键报告/操作记录请求仍独立检查当前访问权限，不能拿变化标记替代鉴权。

## 统一前端生命周期与周期

AuthGuard 的受保护应用树内只有一个 AccessSession，与页面导航无关。

- 启动必须取得有效的服务端权限状态，才挂载受保护页面。
- 可见前台每次成功检查后 30 秒发起下一次，单次超时 10 秒。正常运行定时器时，
  下一次检查在成功确认后约 30–40 秒完成或失败关闭；在途响应可能跨越服务端撤权，
  不能据此推导从服务端撤权时刻算起的固定清屏上限，这也不是硬实时保证。
- 一次只允许一个在途请求，超时/挂起/卸载时 abort；迟到结果用代次标识丢弃。
  聚焦/恢复事件合并，距离上次检查开始至少 5 秒，避免一串事件触发并发请求。
  普通情况下每标签页约 2 次/分钟；反复聚焦时最多约 12 次/分钟，不是每个内容组件独立轮询。
- 未变化的权限状态保留对象引用和组件实例，不因轮询触发用户依赖的重复请求或清空普通页面。
- 账号身份、全局角色、模块权限或任一组织成员角色变化，重建整个受保护子树，销毁旧内容、
  确认框和异步读取状态。新组件只能按新权限显式读取；不自动重新打开报告。
- 网络错误、异常状态、格式不合法、超时、offline 或认证失效事件关闭整个受保护页面，
  只展示重试入口；停止自动检查，用户显式重试成功后挂载新页面，不恢复旧内容。
- 标签隐藏/pagehide 时先卸载敏感视图并取消在途检查，暂停轮询；可见/pageshow 后重验。
  普通页面框架/非敏感草稿在此过程中保留，只有敏感阅读/确认会话被关闭。

敏感视图边界覆盖 RunEvidence、RunOperationTrace、DeliveryAcceptance、MattermostPublication，
因此同时作用于组织原场景页和组织待办详情，不只某一个组织或弹窗。
启动 AuthGuard 本身也忽略卸载后到达的 setup/me 结果，不清掉新会话的令牌。

## 不保证的事情

- 这是轮询，不是服务端推送。浏览器冻结、休眠或事件/定时器暂停时不能承诺即时清屏；
  恢复事件会触发重验。尚未用真实浏览器验收后台冻结、BFCache、多窗口/多标签体验。
- 只能清除应用控制的已挂载视图，无法撤回截图、复制文本、浏览器历史快照或外部保存的内容。
- 检测范围是账号及组织成员权限。资源级授权、运行参与者被手工 SQL 改写、短时间撤权又
  恢复且全部发生在两次检查之间等，不保证被此标记捕捉；资源请求仍需独立鉴权。
- 非敏感页面隐藏时保留状态。检测失败/权限改变时整个受保护树重建，未保存的页面草稿可能
  丢失；这是关闭旧权限上下文的明确取舍，不宣称所有 Octop 页面均有无损恢复。
- 每个浏览器标签页各有一个监视器，没有跨标签主节点，也没有验证生产规模的查询负载。
- 旧客户端不具备这一清理行为；新客户端连接缺少 access-state 的旧后端会安全关闭，
  必须协同升级，不能为兼容回退到仅信任旧的 /auth/me 缓存。

真实模型/AKB/Mattermost、PG、部署隔离和最终发布验收仍未完成，整体不宣称达到 1.0。

## 后续安全审查（2026-11-15）

在 `e2c78d8` / `20ce95e7` 阶段，`jwt_auth.py` 仅为 logout 排除滑动续期，`/api/auth/access-state` 仍可返回新的
访问令牌。因此可见但无人操作的页面轮询可能持续延长会话；此机制不构成空闲退出。
需要单独定义并验证服务端空闲/绝对期限以及后台检查是否续期的策略。该历史阶段没有
修改这个行为。后续已由[显式活动续期](activity-renewal.md)移除普通 HTTP 请求续期；
服务端空闲/绝对期限仍是独立发布阻断。30 秒检查间隔从前次成功返回后开始，受请求耗时和浏览器调度影响，
不能与 10 秒超时相加宣传为撤权后 40 秒清屏上限。

## 本阶段验证记录

Python 命令均使用 `UV_CACHE_DIR=/tmp/octop-uv-cache UV_OFFLINE=1`。

- `uv run pytest tests/unit/auth/test_access_state.py tests/integration/test_scene_delivery_evidence.py tests/integration/test_scene_operation_trace.py tests/unit/i18n -q`：115 passed。
  包含当前数据库身份、旧管理员缓存、跨组织成员变化、禁用、检查中身份变化、只读/脱敏、
  HTTP no-store 和 OpenAPI；报告/操作记录既有权限重验仍通过。
- `npm test -- src/components/AccessSession.test.tsx src/components/AccessSession.sensitive.test.tsx src/components/AuthGuard.test.tsx src/pages/Organizations/RunEvidence.test.tsx src/pages/Organizations/RunOperationTrace.test.tsx src/pages/Organizations/RunQueue.delivery.test.tsx src/pages/Organizations/DeliveryAcceptance.test.tsx src/pages/Organizations/MattermostPublication.test.tsx`：85 passed。
  包含不变状态引用稳定、账号/权限/组织变化、离线/错误/坏响应/超时、隐藏/恢复事件合并、
  卸载/Abort/迟到响应、真实组件的跨组织报告/操作记录清理、路由导航不重复启动验证。
- `npm run build`：通过；有既有构建 chunk 提示。前端测试有既有 jsdom/act 提示。
- 静态检查：Ruff、ESLint（69 warnings / 0 errors）、Prettier 通过；修正后 `make typecheck-all` 通过（mypy 659 个源文件、TypeScript）。
  初次导航测试捕获了导航前已卸载 DOM 节点，改为等待稳定页面后通过；一处测试格式修正；
  mypy 发现数据库 disabled 为整数而异常构造要求 bool，显式转换，未改变真假语义。
- 不运行 `make all` 全量测试、testmon 或受限遥测测试；不把当前定向结果推断为完整发布门禁。
  未运行真实浏览器、真实 PG、模型、AKB、Mattermost、生产或部署验证。
- Docker daemon 29.9.2 可连接，bubblewrap 0.12.0 已安装；本轮只查版本，没有改变环境安全
  设置。此前 bubblewrap 实际隔离受只读 uid_map 限制，仍不能宣称隔离可用。
