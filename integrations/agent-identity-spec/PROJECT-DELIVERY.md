# Agent 身份插件的独立项目交付决策

## 结论

建议独立项目代码仓库，统一维护通用 SDK 和各平台薄适配器；无需重写现有实现，无需部署额外常驻插件服务。当前源码位于 Octop `integrations/`，尚未迁移或创建新远端仓库。

建议新仓库名称 `akb-agent-identity`（名称是建议，不代表仓库已存在），结构：

```text
akb-agent-identity/
  packages/agent-identity-sdk/   # 公私钥、登记、请求证明、AKB 客户端、管理 CLI
  packages/opencode-identity/   # OpenCode 上下文、权限审批、工具注册
  docs/                         # 协议、开发、部署、运维、兼容矩阵
  examples/                     # 引导及完整配置、其他平台适配示例
  .github/workflows/           # 检查、TGZ 交付、可选的受控发布
```

一个仓库包含多个独立版本包，避免同时创建 SDK / 每个平台 / 协议多个仓库增加维护成本。AKB 负责验证、授权、审计和管理界面；Octop 消费 SDK 或受信 IPC；OpenCode 消费插件包。平台插件不得重新实现 RSA/JWT 或让模型指定身份。

## 当前已具备

- SDK 和 OpenCode 插件有各自 package.json、构建、测试和打包入口。
- 插件 TGZ 捆绑 SDK，宿主只安装交付包即可，不需要运行 Octop。
- V2 独立 Agent 密钥、共享服务 Key、请求绑定、真实审批及管理 CLI 已实现；V1 显式兼容。
- 单元测试、真实 AKB / OpenCode 验收方法，以及完整配置、引导配置和部署说明。

## 独立交付尚需落实

1. 迁移源码、协议、示例、测试及原 MIT 许可证，保留版本来源；确认新仓库地址与维护者。只迁移代码，保留现有 Agent UUID、私钥、安装实例、工作区及 AKB 登记，不重新生成身份。
2. 根目录使用 npm workspaces 统一构建；发布物使用明确版本的 SDK 依赖或可复现的捆绑产物，不能让下游安装依赖 `file:../...` 源码位置。
3. 配置 repository / bugs / 文档链接，更新 AKB / Octop 文档及宿主 SDK 的安装定位。
4. 为新仓库配置兼容矩阵、干净宿主安装冒烟、包校验和及版本化交付；公共 / 私有 registry 发布方式需明确。当前 private 插件包不可直接 npm publish。
5. 发布后由 AKB / Octop 按版本依赖交付物，不复制维护多份私钥或认证核心代码。

仓库独立与发布到 npm 是两个动作。当前可以交付 TGZ；不能声称已创建独立仓库、已发布 registry，或所有 OpenCode / OS 版本都兼容。

## 验收标准

- 新机器只拿版本化 TGZ 可安装运行，无 Octop 源码和相邻 SDK 源码目录。
- 文档里的引导 / 完整配置均可导出平台信息，所有示例跟随测试；冲突的绑定配置必须拒绝。
- 管理员能从零完成两个 Agent 的 UUID / 密钥创建、AKB 登记、选钥、插件加载和审计验收。
- 同一 Key、不同 Agent / kid；MCP / REST 各自可读、交叉拒绝；审批拒绝 / 取消不发送请求。
- 实际 OpenCode 1.14.30 验证与普通适配器单元测试明确区分；其他版本标为未验证。
- 配置、权限、密钥备份、轮换、恢复、升级回滚及宿主信任边界说明完整。
