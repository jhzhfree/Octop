# 独立 AKB 身份 SDK

SDK 源码位于 `packages/akb-identity-sdk`，模块名 `akb_identity_sdk`，可独立构建为 wheel/sdist 并交付第三方。使用方法、API、Secret Manager 边界、MCP 示例见该目录的 README。

| 原 Octop 位置 | 提取后职责 |
|---|---|
| `external/native_identity.py` | 调用 SDK 生成密钥与 UUID，继续负责加密存储、归属和连接状态 |
| `external/agent_identity.py` | 调用 SDK 生成身份 UUID/调用 ID，继续负责 Agent 绑定与管理权限 |
| `external/request_auth.py` | 保留 ContextVar 的异步请求隔离，调用 SDK 签名精确请求字节 |
| SDK `crypto.py` | UUID、RSA 密钥、公钥格式/指纹、原始数据签名与公钥验签 |
| SDK `proof.py` | AKB V2 请求证明、验签、防重放接口 |
| SDK `client.py` | 独立 MCP 连接、工具发现、身份验证、目录和检索 |

现有 V2 绑定、密钥和策略不需迁移。Octop 私钥仍保存在原 SecretRepo 加密存储中，不改为 SDK 文件存储。SDK 不包含 Octop 依赖，不负责授权或替代 AKB 服务端验签。

开发时 `uv sync` 通过根项目 `[tool.uv.sources]` 安装本地 SDK。构建：

```sh
uv build packages/akb-identity-sdk --out-dir dist/akb-identity-sdk
```

发布依赖该 SDK 的 Octop 时，须先向部署所用包索引发布 SDK，或先安装一起提供的 SDK wheel；仅 Octop wheel 不包含 SDK 实现。这避免两个发行包重复拥有同一 Python 模块。当前没有向公网 PyPI 发布。

`make all` 包含 SDK Ruff、mypy 和单元测试。实际 Java 互通检查可另运行：

```sh
AKB_REPO=/path/to/compiled/akb uv run python packages/akb-identity-sdk/tests/java_interop.py
```

该检查使用合成凭证，直接调用 AKB Java 验签器验证 UTF-8 请求、网关路径、重放、凭证摘要和密钥撤销，不连接生产环境。
