# KZOCR v0.5 AMEND — 架构师终局验证报告（round 9，rc4）

- **评审角色**：首席架构师
- **评审日期**：2026-07-10
- **评审对象**：`docs/plans/ocr-engine-unification.v0.5-AMEND.md` v0.5-rc4（`69ac37b`）
- **本轮焦点**：对 rc3 → rc4 增量变体的架构影响进行终局验证，确认无增量退化
- **前置评审**：
  - `docs/reviews/2026-07-10-round8/architect.md`（**批准实施** — 所有阻断性问题关闭）

---

## 总体裁决：**批准实施（Approved for Implementation）**

rc4 在 rc3 已通过全维度架构审查的基础上，仅包含 4 项收敛性修正。全部修正均非架构层变更，不引入新的结构风险。方案的可实施性保持良好。

---

## 1. rc3 → rc4 增量变更验证

### 1.1 `RateLimitedError` 构造函数新增 `retry_after` 参数

**rc4 变更**：`RateLimitedError.__init__(message="", retry_after: float | None = None)`，`retry_with_policy` 在捕获 `RateLimitedError` 时优先使用 `retry_after` 作为退避延迟（覆盖指数退避计算）。

| 维度 | 评估 |
|------|------|
| **向后兼容** | ✅ `retry_after` 默认为 `None`，所有现有构造调用不受影响。多态 `isinstance(e, ApiError)` 不变。 |
| **语义正确性** | ✅ `Retry-After` header 是 HTTP 协议的权威限流信号，优先于客户端退避计算是正确行为。 |
| **集成路径** | ✅ 适配器层仅需在构造 `RateLimitedError` 时传入 `retry_after` 值；不传则退化到纯指数退避（与实施注意事项 #8 一致）。 |
| **测试验证** | ✅ 版本历史注明测试 B1 已覆盖此路径。 |

**裁决**：✅ 无架构风险。一个数据字段的扩增，类型安全。

### 1.2 `_compute_config_hash()` 完整定义

**rc4 变更**：补充了此前伪代码未展开的 `_compute_config_hash` 函数体，使用 SHA256[:16] 对 5 个参数做摘要。

| 维度 | 评估 |
|------|------|
| **参数 allowlist** | ✅ engine, sensenova_model, sensenova_base_url, vlm_host, vlm_port — 这 5 项构成影响 VLM 输出的充分参数集。`api_key` **未被纳入**（正确：密钥变更不应使有效缓存失效）。 |
| **摘要长度** | ✅ SHA256[:16] = 64 bit ≈ 2^64 种可能，冲突概率远低于任何实际使用场景的误检概率。 |
| **稳定性** | ✅ `json.dumps(sort_keys=True)` 确保相同配置产生相同摘要。无时间戳、随机数等运行时动态值。 |
| **模块定位** | ⚠️ `_compute_config_hash` 定义在 D3 章节的伪代码中，未明确说明最终实现位置。建议放置在 D3 的缓存模块中（如 `run.py` 或 `engines/cache.py`），不要放在 `errors.py` 中——哈希计算与异常体系无逻辑耦合。 |

**裁决**：✅ 无架构风险。参数 allowlist 选择得当，摘要算法够用。实施过程中注意模块定位即可。

### 1.3 `base_delay` 2.0s → 1.0s

**rc4 变更**：`BACKOFF_CONFIGS["api"]` 的首退间隔从 2.0 秒降至 1.0 秒。

**分析**：纯性能调参，非架构决策。与此变更有相互作用的是 `AdaptiveRateLimiter` 的 `base_interval=3.0`（已在 v0.4 H1 设为此值），两者在各自身份域内独立。1.0s | base_delay + 3.0s | AdaptiveRateLimiter.base_interval 不存在冲突——`retry_with_policy` 管理单请求的重试时间线，`AdaptiveRateLimiter` 管理全局请求间的冷却间隔。

**裁决**：✅ 无架构风险。

### 1.4 `on_exhausted` lambda 参数命名修正（`pn` → `_attempt`）

**rc4 变更**：D2 中 `on_exhausted=lambda pn, exc: ...` → `on_exhausted=lambda _attempt, exc: ...`，同时修正了字典 key 误用 `pn` 为 `page_num` 的 bug（round 7 must-fix C 的最终落地）。

**裁决**：✅ 正确。`_attempt` 命名符合 Python 未使用参数惯例，闭包捕获 `page_num` 的语义与 rc3 评审中的分析完全一致。无新风险。

---

## 2. 累积重试时间跟踪建议

rc4 版本历史备注"新增累积重试时间跟踪建议（批量场景）"，但 **此内容未出现在计划正文中**——仅为版本历史中的记录性描述。

**分析**：这在当前版本中是可接受的状态。这是一个"未来观察项"级别的建议，不是 v0.5 的需求。当批处理用户真正遇到"50 页每页重试 3 次 = 最坏 ~15 分钟超时等待"的场景时再实现。不阻实施。

**裁决**：✅ 非阻断。建议保留为低优先级备查项（可在 `PROGRESS.md` 或 docs 中记录一笔）。

---

## 3. 当前源码对齐检查

评审期间对照以下文件确认源码状态与计划前提一致：

| 文件 | 状态 | 与计划一致性 |
|------|------|-------------|
| `kzocr/config.py` | ✅ 尚未添加 `kzocr_output_dir` 字段 | 符合预期（D0 为 P0，尚未实施） |
| `kzocr/engine/run.py:_run_vlm` | ✅ `except Exception: continue` 模式仍存在 | 符合预期（D2/D3 尚未实施） |
| `kzocr/engines/ratelimit.py` | ✅ `ExponentialBackoff` 已就绪，7000+ 调用验证 | 符合预期 |
| `kzocr/engines/atomic.py` | ✅ `_check_base` + `atomic_write` 已就绪 | 符合预期（D3 将复用） |
| `kzocr/engines/leakage.py:apply_leakage_defense` | ✅ L3 日志标记仍在第 192 行 | 符合预期（冲突-2 删除待 D2 实施后执行） |

4 个待修改的目标文件均处于计划预期的前置状态，无意外偏差。

---

## 4. 架构健康度终局评估

| 维度 | 评估 | 相比 rc3 变化 |
|------|------|-------------|
| **内部一致性** | ✅ D1-D4 各模块边界清晰，依赖方向正确 | 无变化 |
| **YAGNI 合规** | ✅ 4 异常 + 3 backoff 配置均被 D2 消费 | 无变化（`retry_kwargs` 仍存在，见 rc3 M-2） |
| **防御性编程** | ✅ config_hash + TTL + path traversal guard 三层防护 | `_compute_config_hash` allowlist 已明确定义 |
| **实施顺序** | ✅ P0→P1→P2→P3 符合依赖拓扑 | 无变化 |
| **安全合规** | ✅ C2 路径穿越防御覆盖 D3 缓存写入 | 无变化 |
| **性能** | ✅ base_delay 1.0s 更激进，首轮重试更快 | 无结构级性能瓶颈 |

---

## 5. 实施前终核清单

### 需实施工程师决定（非阻断）

1. **`_compute_config_hash` 模块定位**：建议放入 D3 缓存相关的模块内（`run.py` 或独立 `kzocr/engines/cache.py`），不要放入 `errors.py`。哈希计算与异常体系无关，放在 `errors.py` 会产生不必要的模块耦合。

2. **`retry_kwargs` 去留**（round 8 M-2，仍保留）：按 YAGNI 移除更简洁，保留则须在 docstring 注明"当前无消费者，为 strategy='reocr' 预留"。

3. **`_process_vlm_page` 子函数提取**（round 7 sweng 建议）：建议实施 D2 + D3 时提取。当前 `_run_vlm` 约 95 行，加入 D2 重试 + D3 缓存后预计 ~130 行。提取后主循环 ~50 行，其余逻辑在子函数中，可维护性更优。

### 实施边界确认

| 边界项 | 确认 |
|--------|------|
| 本次实施范围 | `_run_vlm` 路径，不涉及 `_run_real`（v0.5 范围外） |
| 适配器 `max_tokens` 兼容 | 若 PaddleOCRVl16Adapter 不持支，则 OverSizeError 不触发重试 |
| 适配器 `Retry-After` 支持 | 当前计划已标注降级为纯指数退避 |
| C1 L3 删除时机 | D2 实施后再删，避免半状态 |

---

## 最终裁决

| 检查项 | 结果 |
|--------|------|
| rc3 全维度架构审查 | ✅ **已批准**（round 8） |
| rc4 增量变更（4 项） | ✅ **全部无架构风险** |
| 源码预实施状态 | ✅ **一致** |
| 新引入的阻断性问题 | **0** |
| 新引入的架构风险 | **0** |
| 实施条件 | **充分** |

### **批准实施（Approved for Implementation）**

v0.5-rc4（`69ac37b`）在 rc3 已通过全维度架构审查的基础上，4 项增量变更均为收敛性修正：`RateLimitedError` 字段扩增、`_compute_config_hash` 函数体补全、backoff 参数小幅调整、lambda 命名修正。未引入任何新的架构风险或依赖冲突。方案已具备进入 P0–P3 实施的全部条件。

---
