# Octop 本地开发环境指南（macOS 实测）

> 面向刚 clone 完仓库、准备动手改代码的开发者。本文按本机（macOS / Apple Silicon）**实测通过**的步骤整理，命令可直接复制执行。
>
> 规则类内容以根目录 [`AGENTS.md`](../AGENTS.md)（模块边界 / 硬规则）、[`docs/development-standards.md`](development-standards.md)（工程规范）、[`CONTRIBUTING.md`](../CONTRIBUTING.md)（分支 / PR / 发布）为准；本文负责"把环境跑起来"。

## 1. 当前环境现状（已就绪）

| 项 | 值 |
|---|---|
| 代码目录 | `/Users/mac/octop`（版本 `1.0.2b7`） |
| Python | 3.12.15（`.venv`，由 uv 管理；**不要用系统自带的 3.9**） |
| 包管理 | uv 0.13.1；前端 npm 11.11.0 / Node v25.8.1 |
| 后端服务 | `octop run` → `http://127.0.0.1:8088` |
| 前端 dev | `npm run dev` → `http://127.0.0.1:5173`（`/api` 代理到 8088） |
| 数据目录 | `~/.octop/`（默认 SQLite：`~/.octop/octop.db`） |
| 本地管理员 | `admin` / `Dev12345`（初始化时自建，仅本机开发用） |
| 基线结果 | `ruff check` 通过；`mypy --strict` 582 个源文件无问题；前端 `tsc -b` 通过；`vite build` 约 20s；单测 4060 passed / 9 skipped（约 90s） |
| git hooks | 已执行 `make install-hooks`（`core.hooksPath=.githooks`） |

## 2. 前置工具链

必需：

- **uv**：`brew install uv`（或官方安装脚本）。后端一律走 `uv run …`，不要直接 `python` / `pytest`。
- **Python 3.12**：`uv python install 3.12`（`uv sync` 也会自动准备）。系统 Python 3.9 不满足 `requires-python = ">=3.12"`。
- **Node**：`mise.toml` 锁定 20（对齐 CI/Docker）。本机 Node 25 已实测可跑 dev server、`tsc -b`、生产构建；若遇到与 CI 不一致的诡异行为，用 nvm/mise 切到 20 再验证。
- **npm**：前端依赖安装用 `npm ci`（有 `package-lock.json`）。

可选（按需）：

- **Docker**：构建镜像 / 沙箱工作区（`docker/`）才需要；本机未安装，不影响日常开发。
- **Go**：仅 `desktop/` 原生客户端需要。
- **Playwright 浏览器二进制**：使用"浏览器 AI+" / 远程桌面功能前执行 `uv run playwright install chromium`（Python 依赖已装，浏览器二进制需单独下载）。

网络注意：本机直连 GitHub 的 443 会被拦（TCP 可连、TLS 握手超时）。克隆 / 拉取需走本地代理（ClashX 默认 `127.0.0.1:7890`）：

```bash
git -c http.proxy=http://127.0.0.1:7890 -c https.proxy=http://127.0.0.1:7890 clone <repo-url>
# 持久化（只对 github.com 生效，不影响其他源）
git config --global http.https://github.com.proxy http://127.0.0.1:7890
```

## 3. 一次性搭建

```bash
cd /Users/mac/octop

# 1) 后端依赖（含 dev 组）→ .venv
uv python install 3.12
uv sync                      # 等价于 make install

# 2) 前端依赖
cd dashboard && npm ci && cd ..

# 3) 启用仓库 git hooks（每个 clone 一次）
make install-hooks

# 4) 初始化数据目录与首个管理员（密码需 ≥8 位且含字母和数字）
uv run octop init --yes --admin-username admin --admin-password 'Dev12345'
```

第 4 步会创建 `~/.octop/`（配置、SQLite、Agent 工作区）。重来一次：`uv run octop init --force`（**会清空 `~/.octop`**）。

## 4. 日常开发：两种运行模式

### 模式 A — 前后端分离（改前端时推荐）

```bash
# 终端 1：后端（--reload 改动即重启）
uv run octop run --port 8088 --reload

# 终端 2：前端（Vite HMR，/api 自动代理到 8088）
cd dashboard && npm run dev
```

浏览器打开 `http://127.0.0.1:5173`。改 `dashboard/src/**` 即时热更新，无需构建。

### 模式 B — 单进程（改后端 / CLI，或验证真实产物）

```bash
cd dashboard && npm run build          # 产物写入 src/octop/dashboard/
cd .. && uv run octop run              # 浏览器打开 http://127.0.0.1:8088
```

- `dashboard/` 是**源码**；`src/octop/dashboard/` 是**构建产物**，不要手改（AGENTS.md / 开发规范明令禁止）。
- 模式 B 下前端每次改动都要重新 `npm run build`；若后端是在构建完成**之前**启动的，`/` 会 404，重启 `octop run` 即可。
- 想一条命令起两个服务：`make dev`（Ctrl-C 同时停）。

### 端口与登录

- 后端端口：`--port` > `OCTOP_PORT` > `~/.octop/config.json`；前端 dev 端口：`VITE_DEV_PORT`（默认 5173），代理目标用 `VITE_API_PORT`（默认 8088）。
- 控制台登录：`admin` / `Dev12345`。CLI 无需登录（信任本机文件系统访问），可用 `octop config set-user` / `octop agent use` 固定默认用户与 Agent。

## 5. 目录与分层速查

```
src/octop/
  config.py     环境变量 → OctopConfig（不得导入 infra/api/cli）
  launch.py     组合根：OctopServer + FastAPI + uvicorn
  infra/        领域核心：agents / db / gateway / cron / connectors / skills / users / setup / backend / history
  api/          HTTP 适配层（FastAPI 路由，保持薄）
  cli/          Click 命令（run / init / chats / agent / provider / acp …）
  dashboard/    构建产物（勿改）
dashboard/      前端源码（React 18 + TS + Vite + Ant Design）
tests/          pytest：unit/、integration/、live/
docs/           人工维护的参考文档
```

依赖方向（硬规则，违反会被 review 打回）：

```
dashboard --HTTP--> api --> infra --> infra/utils, config
cli -----------------------> infra / launch
```

- `infra/` **不得**导入 `api/`、`cli/`、`launch.py`；`api/` 不得导入 `cli/`、`launch.py`；`cli/` 不得导入 `api/`。
- `infra/db/repos/` 只写 SQL，不碰其他 `infra` 子包；`infra/utils/` 保持纯工具。
- 领域规则只在 `infra/` 有一份实现，路由 / CLI / UI 都是适配层，不复制业务规则。
- 前端只能通过 `dashboard/src/api/` 的类型化封装访问 `/api`。

常见改动的落点：

| 想做的事 | 改哪里 |
|---|---|
| 新增 / 修改 HTTP 接口 | `src/octop/api/routers/*.py`（薄）+ 领域逻辑放 `infra/` |
| 新增数据库表或字段 | `infra/db/migrations/00N_x.sql` **和** `00N_x.pg.sql` 成对添加，并同步 repo 与版本断言 |
| 新增专家模板 | `src/octop/infra/agents/experts/library/<id>/`（双语 label/description） |
| 新增技能 | 对应专家目录下 `skills/<name>/SKILL.md` |
| 新增斜杠命令 | `infra/gateway/slash/`（dispatcher + handlers + catalog） |
| 新增前端页面 / 组件 | `dashboard/src/pages/`、`components/`、`api/modules/` |
| 服务端面向用户文案 | `src/octop/i18n/`（zh / en 同步），前端文案放 `dashboard/src/locales/` |

## 6. 常用命令

后端（在仓库根目录）：

| 命令 | 作用 |
|---|---|
| `make install` / `uv sync` | 安装后端依赖 |
| `make all` | 全栈门禁：format-all + lint + typecheck + test（**交付基线**） |
| `make lint` / `make format` | Ruff 检查 / 自动修复 + 格式化 |
| `make typecheck` | `mypy --strict src/octop` |
| `make test` | `pytest -m "not live"`（并行） |
| `make test-fast` | 跳过 `slow` 标记的慢用例 |
| `make test-live` | 需要真实凭据的集成用例（`-m live`，缺凭据自动跳过） |
| `uv run pytest tests/unit -q -n auto` | 只跑单测（约 90s） |
| `uv run pytest tests/unit/test_x.py -q` | 单文件调试 |
| `make build` | 前端构建 + Python wheel |

前端（在 `dashboard/`）：

| 命令 | 作用 |
|---|---|
| `npm run dev` | 开发服务器（5173，代理 `/api` → 8088） |
| `npx tsc -b` | 类型检查（改 UI 后必跑） |
| `npm run lint` / `npm run format` | ESLint / Prettier |
| `npm run test` | Vitest 单测 |
| `npm run build` | 生产构建 → `../src/octop/dashboard/` |

调试 Agent 会话（不经过浏览器）：

```bash
uv run octop agent list            # 查看 Agent
uv run octop chats repl            # 交互式对话（内置 server + CLI 通道）
uv run octop provider test         # 验证模型供应商连通性
uv run octop --help                # 全部命令
```

## 7. 配置与环境变量

- 运行时配置：`~/.octop/config.json`（端口、绑定地址等，由 `octop run --port` 自动回写）。
- 额外环境变量：`~/.octop/env`（裸机安装）或 `docker/.env`（Docker Compose）。模板见仓库根目录 `.env.example`。
- 常用项：`OCTOP_PORT`、`OCTOP_LOG_LEVEL`、`OCTOP_BIND_HOST`、`OPENAI_API_KEY` / `DASHSCOPE_API_KEY`、PostgreSQL 相关 `OCTOP_DATABASE_*`。
- 模型供应商、连接器、知识库等**优先在控制台 Settings 配置**，CLI 仅作补充（`octop provider`）。
- 交互式 API 文档：`/api/docs`（默认关闭，需在 `config.json` 设 `"enable_api_docs": true`）。

## 8. 数据库

- 默认 SQLite（WAL），路径 `~/.octop/octop.db`；与 PostgreSQL **共用同一套 schema**。
- 切 PostgreSQL：设置 `OCTOP_DATABASE_DRIVER=postgresql` 与 `OCTOP_DATABASE_URL`（见 `.env.example`）。
- 改 schema 必须提交成对的 `00N_*.sql` + `00N_*.pg.sql`，并同步 `tests/unit/db/test_db_pool.py` 里的版本断言；未发布版本的新改动**并入当前未发布的 `00N`**，不要另起编号（详见 AGENTS.md §7）。

## 9. 测试与已知坑

- 分层：`tests/unit`（快）→ `tests/integration`（启动开销大，标 `slow`）→ `tests/live`（真实外部服务，标 `live`，缺凭据自动跳过）。
- 前端单测用 Vitest：`cd dashboard && npm run test`。
- **已知 flaky**：`tests/unit/test_fnos_native_stop.py::test_native_main_start_term_cleans_unready_child` 在 `-n auto` 高并行下偶发失败（该用例有 2s 级时序断言，负载高时超时）；单跑必过。遇到时先 `uv run pytest tests/unit/test_fnos_native_stop.py -q` 确认，不要直接改断言。
- 跨平台：CI 同时跑 Linux 与 Windows，用例里请用 `tmp_path` / `pathlib`、Unix-only 用 `posix_only` 标记。
- `mypy --strict` 只覆盖 `src/octop`（已排除 dashboard 产物与专家/插件库目录）；个别路由模块有 `disallow_untyped_defs = false` 豁免。

## 10. 提交与分支

- 已启用 `.githooks`：每次 `git commit` 会跑 `make precommit`（`format-all` + lint + typecheck + testmon 选取的受影响测试）**并**构建 dashboard。会比较慢，这是项目要求的门禁。
  - 应急绕过：`SKIP_PRECOMMIT=1 git commit …` 或 `git commit --no-verify`（不要用它们来掩盖红灯测试）。
- 分支：`feature/*` → **`develop`**（PR base 打 develop，不是 main）；发版走 `release/x.y.z` → `main`（用 merge commit，不要 squash）；`hotfix/*` → `main` 再回 `develop`。
- 面向用户的行为变更要更新 `CHANGELOG.md`。
- 提交前自查：`make all`（后端）或 `make check-all`（含前端）。

## 11. 排错 FAQ

| 现象 | 原因 / 处理 |
|---|---|
| `git clone` 卡在 443 超时 | 本机直连 GitHub 被拦 → 走代理（见 §2），或配 `http.https://github.com.proxy` |
| `uv sync` 报 Python 版本不满足 | `uv python install 3.12` 后重试 |
| `npm ci` 报 `safe-delete … BULK_CONFIRM_REQUIRED` | 本机 npm 有删除保护，`node_modules` 已存在时批量删除被拦。**依赖已装好就不要重跑 `npm ci`**；确需重装：先 `rm -rf dashboard/node_modules` 再 `npm ci` |
| 构建报 `Rollup failed to resolve import "xxx"` | 依赖树被中断的安装破坏 → `cd dashboard && npm install --no-audit --no-fund` 补齐 |
| `tsc: command not found` / `.bin` 空了 | `cd dashboard && npm rebuild`（重建 `node_modules/.bin` 链接，不删除包） |
| `octop` 命令找不到 | 用 `uv run octop …`；或先 `uv sync` |
| 8088 / 5173 端口被占用 | `lsof -nP -iTCP:8088 -sTCP:LISTEN`；改 `OCTOP_PORT` / `VITE_DEV_PORT` |
| 想彻底重置本机数据 | `uv run octop clean`（危险，会清 `~/.octop`） |
| 浏览器 / 远程桌面功能不可用 | `uv run playwright install chromium` |
| 改了前端但 8088 页面没变 | 模式 B 需重新 `npm run build` 并重启 `octop run`；或改用模式 A |

## 12. 建议的上手路径

1. 先跑一遍 `make all` 建立"绿灯"基线（记住 §9 的 flaky 用例）。
2. 按 §4 起服务，用 `admin` 登录控制台，熟悉 Agent / 专家 / 供应商配置。
3. 小改动切入：改一个前端页面（模式 A，即时反馈）或加一个只读 API 路由（模式 B + `uv run pytest`）。
4. 动代码前先读 [`AGENTS.md`](../AGENTS.md) 的相关章节与 [`docs/development-standards.md`](development-standards.md)，避免踩模块边界。
5. 涉及配置细节再查 [`docs/configuration.md`](configuration.md)、[`docs/api.md`](api.md)。
