# 数字分身对话规划：调用保护及恢复集成说明

此模块为进程内防重复与突发保护，不是跨副本限流或金额预算。恢复时先重建独立模块，再由规划接口整合；当前服务、路由、翻译和 UI 说明已接入。以下片段说明集成位置，不是待手动安装步骤。

## 行为

- 每个已认证用户最多同时 1 次、滚动 60 秒最多受理 6 次，跨组织、目标和浏览器窗口共享。
- 每个进程最多同时 4 次、滚动 60 秒最多受理 60 次。不排队或自动重试。
- 在组织、员工、目标、Agent 和模型验证通过后、实际模型调用前受理。
- 已受理的失败、超时和取消计入频率；本地调用退出后释放并发槽位。
- 超限为 HTTP 429，带本地化错误与 `Retry-After`。频率拒绝给出窗口剩余时间；并发拒绝的 5 秒仅是退避建议，不保证届时有空位。
- 状态只包含用户 ID 和时间，不记录对话。过期记录在后续检查时清理，至多保留 60 条。
- 重启清空配额，多进程/副本各有独立配额。取消/超时不保证供应商停止生成或计费；本地并发不等于上游实际并发。

## 规划服务集成

```python
from octop.infra.digital_workers.planning_limits import PLANNING_LIMITER

# 完成鉴权、资源与显式默认模型校验之后。
# 限流异常不得放进将所有异常转换为 HTTP 502 的 try 中。
with PLANNING_LIMITER.admit(self.actor.id, locale):
    try:
        text = await ainvoke_text(llm, messages, timeout=45.0)
    except TimeoutError:
        self._fail("timeout", locale, 504)
    except Exception:
        self._fail("failed", locale, 502)
```

## 路由集成

在通用 `DigitalWorkerError` 分支前处理：

```python
except PlanningLimitError as exc:
    raise HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
        headers={"Retry-After": str(exc.retry_after)},
    ) from exc
```

应更新端点 OpenAPI 描述，说明 429 和单进程范围。每个测试独立替换 `planning.PLANNING_LIMITER = PlanningLimiter()`，不要在生产每个请求新建限流实例。

## 后端翻译（planningDialogue 命名空间）

| key | English | 中文 |
|---|---|---|
| rate_limited | Planning request frequency exceeded. Wait before trying again; no plan was saved. | 规划请求过于频繁，请稍后再试；未保存任何计划。 |
| busy | A planning request is already running for you, or planning capacity is busy. Try later; no plan was saved. | 您已有规划请求正在运行，或规划服务当前繁忙。请稍后再试；未保存任何计划。 |

## 前端说明

可在现有数据发送和费用警告下增加 `planningDialogue.serverLimits`：

- en: Server protection allows one active request and six admissions per minute per user, plus process-wide caps. This resets on restart and is not a shared gateway or monetary budget.
- zh: 服务端限制每用户同时 1 次、每分钟受理 6 次，并设进程级上限。重启会重置，不是跨实例网关限额或金额预算。

不自动重试 429，不清空用户草案；服务端本地化错误应通过已有错误提示展示。

## 验证

独立测试覆盖过期窗口、每用户/全进程并发和频率、8 线程原子受理、取消/异常释放和失败配额。规划服务测试已补接口 429 与 Retry-After、授权失败不占模型受理额度、不同服务实例共享额度、取消释放、达到额度时模型不再调用。

此前被安全审查拒绝的全量测试不在本次恢复验证范围内，不得将定向测试通过称为全量验收。
