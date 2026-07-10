# KZOCR v0.7 E1 实现评审 — 安全（security）

- **评审角色**：安全
- **评审日期**：2026-07-10
- **评审对象**：`kzocr/scheduler/registry.py`（`1d52cae`）、`tests/test_registry.py`
- **范围声明**：E1 聚焦首版，仅注册中心 + 候选选择；egress 校验（§4.5）属 E2/E4 未实现，不计入本 E1 阻塞。

---

## 1. 【阻塞 / 必须修复】

**无。** 经核查 `register_adapter` 与 `EngineRegistration.config` 在代码与测试（`test_registry.py`）中均仅存环境变量名引用（如 `{"api_key_env": "X"}`），未出现 `sk-xxx` 明文落地，符合 §3.3「config 不存明文」，无凭证明文泄露风险。

## 2. 【重要 / 强烈建议】

- **`EngineStats`/`EngineRegistration` 未实现 `__repr__`/`__str__` 掩码**（`registry.py:24-46`）。§3.3 明确要求「EngineStats 添加 `__str__`/`__repr__` 掩码敏感字段」。当前为裸 `@dataclass`，默认 `repr` 会完整打印 `config` 字典及 `last_error`。一旦调试 `print(reg)`、`list(registry)` 或日志打印，`config` 内容（含 `api_key_env` 名、`base_url`）与 `last_error` 原文全部暴露。建议为两者补充 `__repr__`：省略 `config` 值、仅显示键名，并对 `last_error` 截断/脱敏。
- **`config` 类型应为 `EngineConfig` 而非裸 `dict`**（`registry.py:43,89` 用 `dict`，`types.py` 已定义 `EngineConfig` 并注明「用于 EngineRegistration.config，替代裸 dict」）。用裸 `dict` 与设计 `types.py` 自述矛盾，失去类型约束与单点校验，后续极易误存明文。建议统一改为 `EngineConfig(api_key_env=…, base_url=…, extra=…)`。

## 3. 【优化 / 可选】

- **`register_adapter` 无防御性校验**（`registry.py:78-91`）：未阻止调用方误传 `{"api_key": "sk-xxx"}`。建议加纵深防御——检测含明文凭证的键名/值模式时告警或拒绝。
- **`last_error` 存原始 error 字符串**（`registry.py:128`）可能含上游 URL/堆栈。虽非凭证，建议持久化或日志前截断。
- `record` 的 `KeyError` 消息仅含引擎名（`registry.py:114`），无敏感信息，可接受。

**正向确认**：`ProbeResult.keys` 已为 `dict[str, bool]`（`types.py`），与 §3.3 对齐，不存值只存是否存在，无问题。

> 结论：无阻塞漏洞；但 §3.3 的 `__repr__` 掩码与 `EngineConfig` 类型两处属 E1 自身范围、设计硬性要求，强烈建议合入前补齐。
