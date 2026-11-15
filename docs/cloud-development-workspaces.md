# 四工程云开发空间准备与验收

## 交付边界

本轮交付的是配置清单、只读预检和操作规程，**不是平台云环境已创建，也不是服务已运行**。
以 Octop / AI-TAgent（轻智能体系统）为主入口，AKB 为知识库，LiteLLM 为模型网关，
Mattermost 为人和数字员工协作入口。四工程分别保留仓库、分支、依赖、数据和测试结果，
不将 Mattermost 的 Go/Node 要求强加到其他项目，不混用 Python 虚拟环境。

当前开发空间没有可用 Docker；已有 bubblewrap 二进制不等于完整 namespace 可用。
不能通过 privileged、放宽 seccomp、改宿主安全策略或裸机启动绕过预检失败。
平台配置 UI 的创建/保存没有得到确认，本文件不承诺长期保留工作区。

## 配置模板

`dev/cloud-workspace/projects.json` 是无凭据的人工配置模板，不是平台 API 的导入格式。
基线 SHA 只用于防止漏掉已有开发成果，**不是最新远端证明**。后续提交可为基线的后代。

| 独立配置名称（建议） | 仓库 | 目标分支 | 构建/运行边界 |
| --- | --- | --- | --- |
| AI-TAgent-Octop-dev | jhzhfree/Octop | feature/akb-octop-litellm-dev | Python ≥3.12，uv；dashboard 单独 npm 锁文件；Docker + Compose v2；bubblewrap 真探针 |
| AI-TAgent-AKB-dev | jhzhfree/akb | feature/akb-octop-litellm-dev | backend Java 8 / Maven，frontend 单独 npm；数据库与 Redis 按该仓库配置，禁止共用生产数据 |
| AI-TAgent-LiteLLM-dev | jhzhfree/litellm | feature/akb-octop-litellm-dev | Python ≥3.10 且 <3.16，独立依赖锁；先 mock 测试，不填真实模型 Key |
| AI-TAgent-Mattermost-dev | jhzhfree/mattermost | feature/ai-tagent-integration | server/go.mod: Go 1.26.10；.nvmrc: Node 24.11.1；webapp/package.json: npm 11.6.2；独立 PostgreSQL 15 开发数据 |

Mattermost 版本来自当前 fork 锁定代码，不自动降级适配机器上已有工具。AKB/LiteLLM 的前端
Node 要求需结合各自锁文件与构建结果验证，不因为 Mattermost Node 检查通过就算全栈可用。

## 创建/复用规程（在用户可控、已授权的云开发平台执行）

1. 分别创建上述四个配置或复用对应项目的配置，使用已有 GitHub 授权选择仓库及明确分支。
   不选择默认 main/v1.7，不把四个项目伪装成一个同依赖环境。
2. 挂载独立的持久化开发盘（由平台提供并确认生命周期），分别保存源码及开发数据。
   代码推送远端用于代码备份，不代表数据库、未提交文件或工作区会永久保存。
3. 平台有仓库检出功能时优先使用它。仅在空的新目录中手工 clone；现有目录先检查，
   不执行覆盖式 clone、reset、clean、强制 checkout、自动 pull 或同步生产卷。
   凭据只使用平台 GitHub 授权或已有获批 credential helper，不写入 URL、脚本、模板、日志。
4. 使用平台审核过的工具链镜像。本文/预检脚本不安装软件，不拉取镜像，不启动 daemon，
   不执行依赖安装钩子。缺工具时停止并由管理员准备官方来源的审核构建镜像。
   每个项目的依赖安装需单独审阅锁文件与 install/postinstall，再在批准的构建环境执行。
5. 创建后记录四个空间的实际入口、分支和 HEAD，并运行下面的预检。已有目录重复检查无写入，
   不改分支、不生成 .env、不覆盖数据。分支错误、脏工作区、缺基线均需人工处理后重检。
   代理检出的 origin 不在 GitHub 规范地址白名单时检查会失败；须核实平台代理映射，
   不要自动改 origin 来制造通过。

## 只读预检

在 Octop 仓库根目录，用已有 Python 3 执行（不需要安装依赖）：

```sh
python3 dev/cloud-workspace/check.py Octop /your/workspaces/Octop
python3 dev/cloud-workspace/check.py akb /your/workspaces/akb
python3 dev/cloud-workspace/check.py litellm /your/workspaces/litellm
python3 dev/cloud-workspace/check.py mattermost /your/workspaces/mattermost
```

默认仅本地 Git 检查，不 fetch、不访问远端；检查 origin、分支、基线祖先与干净工作区。
浅克隆缺基线时失败，不自行联网补历史。报告不输出原始命令错误、远端 URL、环境变量或凭据。

具备运行能力且获准执行工具探针的宿主，可给各自命令追加 `--runtime`。
它检查 Docker 客户端、服务端、Compose v2，并检查项目主要工具链；Octop 会实际执行一次
`bwrap --unshare-all`（20 秒超时），Mattermost 使用 `GOTOOLCHAIN=local` 禁止 Go 自动下载。
任意检查失败返回退出码 1。没有 `--runtime` 的成功只表示源码前提通过；有该参数的成功
也不证明依赖安装、镜像内容、端口隔离、数据库、UI、模型调用或企业业务验收通过。
工具链检查不是完整依赖清单：未检测 Octop/AKB 的 Node、AKB 辅助 Python 服务、LiteLLM UI、
数据库/Redis 的真实连通性或所有锁文件依赖。报告的 `head` 为实际检出 SHA，应随构建证据保存。
所有报告固定 `deployment_verified: false`。探针不会启动容器或任何产品服务。
只应在已信任的工作区和工具 PATH 中运行本工具，不执行未知来源的二进制。

## 运行与产品验证顺序

1. **Octop 独立离线候选**：沿用 [本地试用说明](local-trial-delivery.md) 和
   `docker/local-trial/compose.yml`，不改原有默认配置、不与默认 Compose 合并。
   必须准备受审核镜像和全新空卷；当前默认镜像没有 bubblewrap，预期停止。
   宿主探针不替代容器入口探针；探针成功不表示每次 Agent 执行均已沙箱化。
2. **单工程构建与离线回归**：按各工程 README 的命令单独执行并记录 SHA、日志与结果。
   Octop 默认质量门为 `make all`，前端需 build；不把静态测试等同浏览器通过。
   仅在独立授权开发空间、已按锁文件完成审核安装后，在 `dashboard/` 目录执行
   `./node_modules/.bin/vite --host 127.0.0.1`。本地文件缺失即停止，不使用可能下载包的
   `npm exec` / `npx` 补齐；不直接用 Octop 的 `npm run dev`（默认 `--host`）。
   仅使用有认证的端口转发。
3. **业务基准场景**：企业运营周报。负责人提出目标 → 规划并分配数字员工 →
   使用无敏感数据的固定素材 → 生成带来源草稿 → 人工审批 → 交付并查看进度 →
   人工验收与知识沉淀。先跑已存在的 Mock/ASGI 流程，再验证真实浏览器、持久化重启和备份恢复。
4. **独立集成验证**：逐项配置获准的模型、连接器目标、预算、权限和出站白名单，
   才能验证真实采集、网关计费与 Mattermost 消息投递。当前 Mattermost 功能不能当作真实投递已完成；
   人工账号与数字员工身份需明确区分。AKB 权限先满足最小需要，不扩大本轮范围。
5. **发布门**：记录每项通过/失败/未运行、四工程提交 SHA、远端可见性、CI、依赖版本、
   数据备份恢复及回滚步骤。涉及真实外发的环节另按授权范围执行，不自动向企业频道发消息。

现有 Octop 启动仍可能加载插件并安装依赖；`OCTOP_LOCAL_TRIAL=1` 不是禁止全部后台活动的开关。
禁止导入旧任务/桥接/密钥或通过真实生产连接器来“试跑”。本轮脚本无安装行为，不能推广为整个产品无安装行为。
