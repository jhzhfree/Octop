# AKB Identity SDK · Python

独立 Python 3.10+ SDK，支持 Windows/Linux，不依赖 Octop、数据库或 Agent 框架。运行依赖仅 `cryptography>=41` 和 `PyJWT>=2.8,<3`。采用 AKB `AGENT_KEY_RS256_V2`，不会降级为其他身份协议。

## 安装与交付

```sh
pip install ./akb_identity_sdk-0.1.0-py3-none-any.whl
# 或从本源码目录安装（仅安装 SDK）
pip install .
```

发布包尚未上传 PyPI；不能假设 `pip install akb-identity-sdk` 已可从公网下载。SDK 目录可以单独复制给其他平台，wheel/sdist 都不含 Octop。发布依赖新 SDK 的 Octop 前，需先提供 SDK wheel 给部署环境，或先发布 SDK 到使用的包索引。开发仓库通过 uv 本地路径依赖安装，不要求公网发布。

## 公共 API

| 方法/类 | 用途 |
|---|---|
| `generate_uuid()` / `validate_uuid(value)` | UUID v4 生成 / 小写标准 UUID 校验 |
| `generate_key_pair(bits=3072)` | RSA-3072/4096，返回 `private_pem/public_pem/public_jwk/kid` |
| `public_jwk(material)` | 从 RSA 私钥、公钥或 PEM 导出不含秘密的公钥及稳定指纹 |
| `sign_data(data: bytes, private_key)` | SHA256 + RSA PKCS#1 v1.5，返回 base64url 签名 |
| `verify_signature(data, signature, public_key)` | 使用公钥 PEM/JWK 验证原始字节；不匹配返回 False |
| `create_request_proof(...)` | 签发 AKB V2 请求证明，供 `X-AKB-Workload-Assertion` 使用 |
| `verify_request_proof(...)` | 使用可信登记公钥/绑定验证签名、时效、请求、服务 Key 摘要及重放 |
| `MemoryReplayStore` / `ReplayStore` | 单进程防重放 / 多进程共享存储原子接口 |
| `AkbMcpClient` | 连接、发现工具、Agent 验证、目录和知识检索 |
| `tool_payload(result)` | 解析 MCP structuredContent 或 text JSON |

```python
from akb_identity_sdk import generate_uuid, generate_key_pair, sign_data, verify_signature

agent_id = generate_uuid()
keys = generate_key_pair()
# 私钥交给宿主已有 Secret Manager，不输出到日志，也不传给 AKB。
# 向 AKB 登记 agent_id 和 keys.public_jwk。
data = ("build:" + agent_id).encode("utf-8")
signature = sign_data(data, keys.private_pem)
assert verify_signature(data, signature, keys.public_jwk)
```

上述数据签名可供第三方自身协议使用；AKB MCP 使用下一节的 V2 请求证明，不能用裸的名称+UUID签名替代。

## 第三方平台对接 AKB MCP

1. 在宿主生成 UUID/密钥，私钥保存在宿主管理的受保护存储中。
2. 在 AKB Agent 凭证详情登记 UUID、公钥，绑定正确的凭证并启用 Agent、密钥与策略，配置知识库权限。
3. 从进程环境或 Secret Manager 读取服务 Key，构造 `AkbMcpClient`。每个已绑定 Agent 使用自己的客户端实例；不要让模型参数决定 UUID、私钥或凭证。
4. `connect()` 只检查服务凭证与初始化协议，`list_tools()` 只做发现。`authenticate_agent()` 获取服务器绑定，进行私钥签名与远端校验，并调用 MCP 身份工具比对 UUID/kid。
5. 通过后调用目录、检索；使用 `KB_...` 内部 ID。身份失败时清空本机客户端的就绪身份，不重用此前绑定。

```python
import os
from pathlib import Path
from akb_identity_sdk import AkbMcpClient, tool_payload

client = AkbMcpClient(os.environ["AKB_URL"], os.environ["AKB_SERVICE_KEY"])
client.connect()
client.list_tools()
client.authenticate_agent(
    os.environ["AKB_AGENT_UUID"],
    Path(os.environ["AKB_PRIVATE_KEY_FILE"]).read_text(encoding="utf-8"),
    session_id="trusted-host-session", agent_name="research-agent",
)
print(tool_payload(client.list_knowledge_bases()))
print(tool_payload(client.search(os.environ["AKB_KB_ID"], "公司的报销流程？", top_k=5)))
```

完整可运行示例在 `examples/mcp_search.py`。普通服务凭证使用 `auth_type="service"`，按 AKB 原授权可直接调用工具，不构造虚假 Agent。默认 HTTPS，本机 HTTP 允许；内网 IP HTTP 需显式 `allow_private_http=True`。地址支持网关前缀、`/api/mcp` 或 `/api/v1/external`，签名绑定真实 URL 路径与查询串。默认传输拒绝重定向、限制响应大小，不自动重试或降级。宿主需要其他 HTTP 栈时可注入 `transport`；它必须保持 body 原始字节、凭证保护、超时、TLS 和重定向限制。

## 请求签名/验签

见 `examples/sign_and_verify.py`。证明包含 UUID、服务主体、策略、工作区、宿主 Agent 名、session/invocation、一次性 jti、服务 Key 摘要，以及精确的 HTTP 方法、路径、查询串和 body SHA-256。TTL 最大 60 秒。必须先序列化再对同一份 UTF-8 字节签名和发送，不能由 HTTP 库再次变更 JSON。

`verify_request_proof` 的 `binding` 与公钥必须来自可信登记记录，不得取自未验签 token。验证方还须检查密钥有效/撤销状态、scope、知识库授权；密码学验签不会授予这些权限。SDK 公钥验签可用于第三方本地验证或代理服务，AKB 仍独立完成自己的验签和授权。

验签必须传入长期复用的 `replay_store`，不能每次创建新实例。`MemoryReplayStore` 只适合单进程；多进程/多副本应实现 `ReplayStore.claim(nonce, expires_at)`，例如 Redis 原子 SET NX EX/数据库唯一键，失败或容量耗尽时拒绝请求。客户端生成证明不需要重放库。

SDK 不负责秘密落盘、数据库加密、用户归属、Agent 权限或模型工具审批。这些仍由宿主处理。同步客户端可在异步框架中通过线程调用；Octop 保留现有异步 HTTP 和加密存储，仅复用 SDK 的密码学方法。

## 验证与构建

```sh
pip install '.[test]' build
pytest
python examples/sign_and_verify.py
python -m build
```

公共过期协议向量位于 `tests/fixtures`，与 Node SDK / AKB Java 共用；过期凭证始终拒绝。测试涵盖精确字节、凭证/路径篡改、协议降级、重放并发、发现与工具调用分离、身份失败清理。测试使用合成身份，不代表已验证某个生产 AKB 实例。

已编译 AKB 工程可用于可选的真实 Java 验签器互通检查：

```sh
AKB_REPO=/path/to/akb python tests/java_interop.py
```
