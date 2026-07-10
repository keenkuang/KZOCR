# KZOCR v0.7 E1 实现评审 — 性能（performance）

- **评审角色**：性能
- **评审日期**：2026-07-10
- **评审对象**：`kzocr/scheduler/registry.py`（`1d52cae`）、`tests/test_registry.py`
- **范围声明**：E1 聚焦首版，仅注册中心 + 候选选择；benchmark 持久化（§7.1）与 decay（§4.2）属 E1.5/E2 未实现，不计入本 E1 阻塞，但可标注「后续性能契约」。

---

## 1. 【阻塞 / 必须修复】

**① 除零崩溃（确认存在，真实崩溃风险）**
- 位置：`registry.py:140`（`_bayesian_score` 的 `1.0 / latency`）← `registry.py:60-65`（`avg_latency_per_page_ms`）。
- 依据：设计 §3.5 明确要求「无延迟数据 → `latency_avg = AVG_LATENCY_DEFAULT_MS`」，即兜底应针对**无延迟样本**。
- 现象：`avg_latency_per_page_ms` 仅在 `total_pages == 0` 时返回默认值，否则返回 `total_latency_ms / total_pages`。而 `record()` 中 `pages` 默认 1、`latency_ms` 为 `Optional`。当调用方**不传 `latency_ms`**（或传 0）但 `pages` 累计 >0 时，`total_pages>0` 且 `total_latency_ms==0` → `latency = 0.0` → `1.0/0.0` 抛 `ZeroDivisionError`。
- **复现条件**：注册任一引擎后 `reg.record("x", success=True, glyph="PASS")`（不传 latency_ms）→ `select_candidates(reg, tier=1)` 即崩溃。测试 `test_cold_start_defaults` 只断言属性值、未走 `select_candidates`，故该路径无回归测试。
- 建议：将兜底改为「无延迟样本」判定，`registry.py:63` 改为 `if self.stats.total_latency_ms == 0: return AVG_LATENCY_DEFAULT_MS`；并补一条「`record` 不传 `latency_ms` 后 `select_candidates` 不崩溃」的回归测试。

## 2. 【重要 / 强烈建议】

- **② 缺少除零路径回归测试**：`test_select_candidates_*` 均传入 `latency_ms`，未覆盖「有页无延迟」；建议随 ① 一并补。
- **③ `avg_latency_per_page_ms` 边界**：若调用方传 `pages=0` 且带 `latency_ms>0`，`total_pages` 仍 0 → 错误返回 10s 默认，掩盖真实延迟。建议用「`total_latency_ms==0`」做唯一兜底可顺带修复此点。

## 3. 【优化 / 可选】

- **贝叶斯公式一致性**：`registry.py:140` 与 §3.5 公式一致（`n=total_pages`、`pass_rate`、`C=7`、`prior=0.7`），冷启动退化 `0.7×(1/10000)` 与设计吻合，结论正确。
- **排序开销**：`sorted(key=_bayesian_score)` 为 O(n) 次 key 求值（非每次比较），派生指标重算开销可忽略；`list_by_tier`（`registry.py:99`）每次线性扫描 O(n)，E1 规模无碍，列为**后续性能契约**（引擎规模扩大时考虑按 tier 分桶索引）。
- **§7.1 benchmark 追加式持久化**：E1 未实现，提示后续实现必须 O(1) 行级 NDJSON 追加，禁止 JSON 全文覆写防 O(n²)——属后续契约。
- **10s 默认偏保守**：所有冷引擎同落 10s，靠 tier/注册序/5% 轮询兜底相对公平，非阻塞；若长期冷启动占比高，可考虑下调（如 3000ms）。
