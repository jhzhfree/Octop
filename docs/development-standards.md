# Octop 工程开发规范

> 本文根据当前 Octop 仓库的 Python/FastAPI、React/TypeScript、SQLite/PostgreSQL、Agent 与 Connector 实现整理，供功能设计、代码修改、评审和交付使用。
>
> **规则来源：** 具体模块边界和代码约束以根目录 AGENTS.md 为准；分支、PR、版本和发布流程以 CONTRIBUTING.md 为准；本文提供团队可读的工程概览。冲突时不复制或覆盖上游规则，应先同步本文。

## 1. 工程目标与变更原则

- 先复用现有 Agent、Harness、Gateway、Cron、Connector、Skill、Plugin、workspace backend 和 API，不另造平行机制。
- 一个改动解决一个明确问题；优先最小 diff，不顺手重构无关代码。
- 面向用户的功能必须说明权限、失败状态、数据来源和验收方式。
- 业务逻辑只保留一个权威实现；路由、CLI 和 UI 是适配层，不复制领域规则。
- 不声称系统已完成未实现的连接、调度、审批、审计或自动修复能力。Agent 提示词不能替代服务端授权和代码级限制。

## 2. 分层与依赖方向

Octop 是单 Python 包，包含 FastAPI 控制面、Click CLI、React 仪表盘和运行时服务。

```text
dashboard --HTTP--> api --> infra --> infra/utils, config
cli -----------------------> infra / launch
```

- `octop.config` 只处理环境配置；不得导入业务层。
- `infra/` 是领域和编排层；不得依赖 `api/`、`cli/`、`launch.py`。
- `api/routers/` 负责请求校验、身份验证、调用服务和映射错误；不写 SQL、不承载领域规则。
- `infra/db/repos/` 每个 repo 负责相应表的 SQL；不依赖 Agent、Gateway 或 API。
- `infra/utils/` 保持为纯工具层，不依赖其他领域子包。
- `cli/*_cmd.py` 调用 infra 的能力；不得复制业务规则或反向导入 API。
- 前端只能通过 `dashboard/src/api/` 的类型化 HTTP 客户端访问服务；不得直接访问 DB 或 Python 模块。
- 新文件放入拥有该职责的现有模块，不为单次调用新建宽泛的“common”层。

## 3. Python 后端标准

- 使用 Python 3.12+、类型注解和仓库现有 Ruff 规则；生产代码由 `mypy --strict` 检查。
- async 路径保持非阻塞。磁盘、子进程或同步网络操作不得直接阻塞事件循环；按现有模式使用异步 API 或 `run_in_executor`。
- 复用既有的服务、RepoBundle/SharedServices、错误码、路径工具、ULID 和 locale helper；不要再造第二套注册表或错误封装。
- 通过构造函数或现有 server/runtime 装配注入依赖；避免模块级初始化连接、读取用户状态或启动后台任务。
- 捕获预期异常并给出明确错误；不要用宽泛异常吞掉程序错误，不为不现实路径添加多层兜底。
- 对外可见的服务端文案使用现有 i18n（zh/en）；新增 key 时保持两种语言树一致，并运行 i18n 测试。
- API 请求/响应使用 Pydantic 模型和类型化返回值。新路由必须有 summary、必要的 description 和 response_model，并保持 Scalar 文档可读。

## 4. 数据库与数据兼容

- SQLite 与 PostgreSQL 是并行支持的正式数据库；表结构或索引变化必须同时维护编号迁移文件对：`NNN_name.sql` 与 `NNN_name.pg.sql`。
- 遵守 `AGENTS.md` 的未发布迁移规则；已发布迁移不可随意改写。SQLite 重建逻辑要幂等，并与对应迁移保持等价。
- 每次 schema 变更都更新迁移版本断言及相关 repo、初始化、备份/恢复测试。
- API 资源使用公共字符串 ID；子表按现有模式引用公共 ID。不要把内部自增主键暴露为公共标识。
- Repo 负责 SQL 和行映射；跨 repo 的业务事务放在 domain/service 层。
- SQL 使用参数绑定；不得拼接用户输入形成 SQL。新增表和字段要考虑旧数据、默认值、索引、回滚/备份及 SQLite/PostgreSQL 语义差异。

## 5. API、身份与安全

- 每条资源路由都要验证当前用户对用户、Agent、线程、文件、Connector 等目标资源的访问权；只验证“已登录”不够。
- 使用现有 JWT、角色和资源 owner 检查。跨用户/共享专家访问必须显式沿用现有共享规则。
- 用 `OctopError` 与现有 `ErrorCode` 表达领域错误；服务端和前端错误文案按现有映射维护。
- OAuth token、API key、SSH 私钥、MFA 信息和用户密码不能写入日志、提示词、报告或浏览器存储；凭据通过现有加密 Connector 配置管理。
- 外部网络 URL、文件路径、命令参数、上传文件和 MCP 配置视为不可信输入；复用已有 SSRF、路径、命令和大小限制，不绕过其校验。
- 对外部写操作、生产变更、通知发送、扫描和删除，要明确作用范围、权限来源、审批门和审计事件。UI 隐藏按钮不是安全边界。
- 为 Agent 工具设置最小权限和清晰的读/写语义；不能只靠 system prompt 限制高风险工具。

## 6. Agent、Connector 与 workspace

- Agent 专家模板放在 `src/octop/infra/agents/experts/library/<id>/`，manifest 使用稳定 ID、双语 label/description，并检查快捷提示和任务样例的结构。
- 新技能放在其所属专家的 `skills/<name>/SKILL.md`，遵循现有 front matter 和 metadata 格式；Skill 是指引，不是权限隔离机制。
- 插件、MCP 工具和 Connector 的来源、授权范围、超时、错误和敏感数据处理必须可配置且可审计。
- Connector 凭据按现有加密和用户授权模型保存。不要将个人凭据复制成共享服务凭据；不要让定时任务扩大创建者权限。
- Agent 工作区文件一律经 `HarnessAgent.workspace` / `BackendWorkspace` 访问；不要直接拼接本地路径读写 workspace，也不要假设后端一定是本地磁盘。
- 新的定时任务沿用 CronManager、现有持久化和 gateway delivery；明确时区、任务所有者、失效凭据行为与失败结果。

## 7. Dashboard 标准

- 使用 React 18、TypeScript、Vite、Ant Design 和现有组件/Hook/i18n 模式。
- HTTP 请求封装放 `dashboard/src/api/modules/`，类型放 `dashboard/src/api/types/`；页面与组件不得直接散落 fetch。
- 只在确有多处复用时抽取通用组件；页面私有状态留在页面或专用 Hook。
- UI 新增用户可见文案时维护 `dashboard/src/locales/{zh,en}.json`；服务端错误与工具名称则维护其后端 canonical bundle 和同步测试。
- 时间显示使用服务端 `default_timezone` 与现有格式化 Hook/helper。不要用浏览器默认时区格式化服务端时间。
- 修改源文件 `dashboard/`，不要直接改 `src/octop/dashboard/` 构建产物；前端变更后按要求构建产物。
- 交互必须呈现加载、空、错误、权限不足和部分失败状态；危险动作需说明影响并执行现有确认/审批流程。

## 8. 测试与质量门禁

### 按变更选择验证

| 变更 | 必做验证 |
| --- | --- |
| Python 后端 | 相关 pytest；`make lint`、`make typecheck` |
| 数据库迁移 | SQLite 与 PostgreSQL 迁移/Repo 测试；更新 schema 版本断言 |
| API | unit/integration API 测试；检查 `/api/docs` 的模型与描述 |
| Agent、Cron、Connector、权限 | 覆盖成功、拒绝、权限隔离、工具/凭据失败和恢复路径 |
| Dashboard | 对应 Vitest；`cd dashboard && npx tsc -b`、`npm run lint` |
| 全栈提交 | `make check-all`；仓库默认 ship bar 为 `make all`，前端变更还需 build |
| 用户可见行为 | 更新文档；符合发布要求时更新 CHANGELOG |

### 测试约束

- 使用 `uv run pytest`，不直接调用系统 pytest。常规测试不依赖真实 LLM、公网服务、凭据或个人环境；需要真实服务的测试标记为 live。
- 测试临时文件和用户目录必须放进 `tmp_path`，通过环境变量指向临时 `OCTOP_HOME`；不要读写开发者真实 `~/.octop`。
- CI 同时运行 Linux 和 Windows。使用 `pathlib` 和跨平台 helper；POSIX 专属测试按仓库约定显式 skip。
- 单元测试验证纯业务逻辑，集成测试覆盖 API/持久化/装配边界；不要只复制实现逻辑写同构测试。
- 每个 bug 修复至少有能复现该 bug 的回归验证；新行为覆盖边界条件、异常路径和授权拒绝。
- 不因本地无法运行就宣称测试通过；记录未执行命令、阻塞原因和待 CI 验证项。

## 9. 文档、依赖与交付

- API 行为变更同步更新 `docs/api.md` 或对应模块文档；新配置项同步配置说明、环境样例和默认值说明。
- 新依赖必须有明确必要性，检查许可证、维护状态、Python/Node 支持范围、供应链风险和体积；不得添加仅为少量代码引入的大型依赖。
- 需要运行时外部服务的功能要说明部署、权限、网络、密钥保存和降级行为；Octop 的自托管与离线可用路径不可被无意破坏。
- PR 说明目标、设计选择、受影响模块、数据迁移、验证命令与结果、已知限制。行为变更补测试；遵循 CONTRIBUTING.md 的分支和发布流程。
- 提交前运行 `make install-hooks`（每个 clone 一次）并通过仓库 hook；除紧急情况外不得跳过质量门禁。
- 不发布、合并 PR、打 tag 或推生产版本，除非任务明确要求。

## 10. 功能完成定义

一个改动只有同时满足以下条件才算完成：

1. 需求与受影响边界明确，且没有破坏现有用户/Agent/数据隔离。
2. 实现遵循模块职责，错误与权限行为可预期，敏感数据未泄露。
3. 新旧路径、SQLite/PostgreSQL、Linux/Windows 等适用兼容场景有验证。
4. 用户可见行为具备文档与 i18n；API 和前端交互完整。
5. 相关测试通过，或明确标出未执行的门禁及原因；CI 结果可复核。
