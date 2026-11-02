# 外部知识库调用上下文

`infra.knowledge.external.caller.current_caller()` 提供不可变、无秘密的宿主调用信息。`caller_scope` 使用 ContextVar，在并发协程之间隔离并在异常后恢复。签名私钥与服务凭证保留在原签名上下文和加密存储中，不暴露给业务工具。

Agent 模式在已选择的连接/资源和宿主 Agent 绑定基础上建立上下文；service 模式 actor_type 为 service，host_agent_id 仅记录发起方，不声明远端已认证 Agent。该上下文不能替代 AKB 验签或资源授权。

session_id 来自宿主 thread/session，invocation_id 标识单次操作，trace_id 用于链路关联。旧调用方仅有 trace_id 时仍兼容原会话值，但新的工具调用优先使用真正 session_id。

AKB V2 继续由原生 Python 签名，不运行 Node 子进程。公共过期协议向量位于 `tests/fixtures/identity/akb-v2-vector.json`；只在向量测试中关闭有效期检查以检验历史签名，正常请求仍拒绝过期 proof。

连接检查、Agent 身份检查、资源目录检查和实际检索保持不同语义：身份连接成功不会写入资源检索成功状态；现有检查记录的版本失效机制保持生效。无需迁移现有连接或重新导入密钥。
