# AKB 授权能力探测（诊断，不是授权）

MCP initialize 的 `capabilities.experimental["akb.authorization"]` 提供部署级 v1 声明。
Octop 在既有探测返回的 `authorization_support` 中解析该声明，不增加网络调用，也不更改工具、权限、绑定或检索开关。

- `unknown`：旧服务未提供、版本未知、字段缺失、类型错误或声明与当前已支持协议冲突。
- `unsupported`：已识别声明，但旧 Agent 委托未配置或内容标签模式不是 ENFORCE。
- `configured_not_verified`：旧 Agent 委托已配置且为 ENFORCE，仅说明配置，不证明当前用户、Agent 或任何内容已授权。
- `retrieval_authorized` 与 `user_acl_verified` 永远为 false。V2 用户委托仍不受支持。

`probe.ok` 保持原有连通/可用工具探测含义，不代表授权。即使报告 configured_not_verified，数字分身规划的知识映射仍不启用检索。
`PUBLIC_ONLY` 表示空标签授权仍可能返回公开资料，并非 DENY_ALL。

解析器只接受严格类型的 v1 必需字段，忽略无关扩展，不把原始额外字段复制进诊断结果。原始 `capabilities` 继续兼容透传，是未经授权证明的上游元数据，不是清洗后的安全结果。新扩展只做诊断，不能作为未来授权门禁的唯一证据。

验证：`uv run pytest tests/unit/knowledge/test_authorization_capabilities.py tests/unit/knowledge/test_external_akb.py -q`。
测试使用本地 mock，不联系真实服务、不证明同知识库两用户内容 ACL 隔离。开放检索仍需独立双主体协议、真实内容/块权限测试和撤销测试。
