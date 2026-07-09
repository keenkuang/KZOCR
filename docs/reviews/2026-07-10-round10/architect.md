# KZOCR v0.5 AMEND — 建筑师终局验证报告（round 10，rc5）

- **评审角色**：首席架构师
- **评审日期**：2026-07-10
- **评审对象**：`docs/plans/ocr-engine-unification.v0.5-AMEND.md` v0.5-rc5（`948f42e`）
- **本轮焦点**：对 rc4 → rc5 增量变体（运维 FAIL + PM 条件）进行架构终局验证，确认 rc3→rc4→rc5 迭代序列无退化

---

## 总体裁决：**APPROVED**

rc5 是 rc4 的运维硬化增量（TTL 强制实施）。全部变更均为非架构层的防御性增强，不引入新的结构风险。方案经过 10 轮迭代、6 角色评审、全部 2 项 Blocking、3 项 Major、2 项 must-fix、1 项 ops FAIL 均已关闭。

---

## 1. rc4 → rc5 增量变更验证

| # | 变更 | 架构影响 | 评估 |
|---|------|---------|------|
| 1 | Config 新增 `cache_ttl_seconds` 字段（映射 `KZOCR_CACHE_TTL`） | **无** | 纯数据字段扩增，与 D0 `kzocr_output_dir` 相同模式，类型安全。`int` 类型无注入面，无跨模块耦合。 |
| 2 | `_cache_is_valid()` 加入 mtime TTL 检查 | **无** | 已有函数新增一个检查条件，线性扩展。TTL 检查在 config_hash 检查之前执行（先检过期再检参数），短路顺序正确。 |
| 3 | `from_env()`/`load_config()` 须更新读取环境变量 | **无** | 实现细节，架构无关。 |
| 4 | 生产部署必须覆盖 `KZOCR_OUTPUT_DIR` 为非 `/tmp` 路径 | **无** | 文档约束。强调此点是因为 `/tmp` 在 tmpfs/systemd-tmpfiles 场景下会被清理，导致 VLM 缓存不可预期。属运维纪律，非架构设计。 |
| 5 | 测试用例 6→12（TTL 超期场景） | **无** | 测试扩展，无架构影响。 |

**裁决**：✅ rc5 增量全部为非架构变更。

---

## 2. 全版本迭代完整性检查

| 版本 | 增量重点 | 裁决 |
|------|---------|------|
| rc1 | 初始方案 D1-D4 完整 | — |
| rc2→rc3 | 吸收 round6+7 全部阻断性/重大问题（D3-1 Blocking、D3-2 Blocking、D1-1 Major、D2-1 Major、D2-2 Major、冲突-2 Medium、顺序-1 Medium、2 must-fix） | ✅ **已批准**（round 8） |
| rc3→rc4 | 收敛性修正：`RateLimitedError.retry_after`、`_compute_config_hash` 补全、base_delay 1.0s、lambda 命名修正（4 项均无架构风险） | ✅ **已批准**（round 9） |
| rc4→rc5 | 运维硬化：TTL 强制实施（Config 字段 + mtime 检查 + 测试扩展） | ✅ **本次确认无风险** |

**结论**：所有版本迭代方向一致——没有架构逆转、没有功能膨胀、没有引入新耦合。

---

## 3. 架构健康度终局验证（rc5）

### 3.1 内部一致性（跨全部 D0-D4）

| 依赖链 | 验证 |
|--------|------|
| D0 `kzocr_output_dir` → D3 `vlm_cache/{engine_tag}/...` | ✅ 路径清晰 |
| D0 `cache_ttl_seconds` → D3 `_cache_is_valid` mtime 检查 | ✅ 新增闭环 |
| D1 异常类型 → D2 消费者（ApiError/RateLimitedError/OverSizeError/RetryExhaustedError） | ✅ 均有消费者 |
| D1 `retry_with_policy` → D2 两处调用 | ✅ 无 hand-coded loop 替代路径 |
| D2 OverSizeError 实时处理 → C1 L3 移除 | ✅ 冲突-2 已修订 |
| D3 缓存写入 → C2 路径穿越防御 | ✅ `allowed_base=cfg.kzocr_output_dir` 已在计划中 |
| `ExponentialBackoff` → `retry_with_policy`（直接接受实例，无影子配置层） | ✅ 极简 |

### 3.2 架构原则合规

| 原则 | 评估 |
|------|------|
| **YAGNI** | ✅ 4 异常 + 3 backoff 配置 + 1 TTL 配置，每项都有明确消费者。`retry_kwargs` 仍为 M-2 留心项（非阻断） |
| **单一职责** | ✅ `errors.py`（异常）、`ratelimit.py`（退避）、`run.py`（编排）各司其职 |
| **开闭原则** | ✅ 新增异常类型继承 `OcrError` 即可 |
| **防御性编程** | ✅ config_hash + TTL + path traversal guard 三层防护 |
| **运维可管理性** | ✅ `KZOCR_CLEAR_CACHE=1` 手动清除 + TTL 自动清理 + 生产路径覆盖文档说明 |

### 3.3 实施风险（相较 rc4 无新增）

rc5 未引入任何新的实施风险。rc3→rc4 时已识别的风险项保持原评级不变。

---

## 4. 实施前最终确认清单

### 实施工程师在实现时需决定（非阻断，保留自 rc8/rc9）

1. **`retry_kwargs` 参数去留**（rc8 M-2）：按 YAGNI 移除，或保留并标注"当前无消费者，为 strategy='reocr' 预留"
2. **`_compute_config_hash` 模块定位**：放 `run.py` 或独立 `cache.py`，**不要放 `errors.py`**（哈希与异常体系无逻辑耦合）
3. **`_process_vlm_page` 子函数提取**：建议 D2+D3 实施时一并提取，降低 `_run_vlm` 主循环复杂度

### 实施边界确认

| 边界项 | 确认 |
|--------|------|
| 实施范围 | `_run_vlm` 路径，不涉及 `_run_real`（v0.5 范围外） |
| 适配器 `max_tokens` 兼容 | 若适配器不支持，OverSizeError 不触发重试 |
| 适配器 `Retry-After` 支持 | 不支持时降级为纯指数退避（已标注） |
| C1 L3 删除时机 | D2 实施后再删，避免半状态 |

### 源码预实施状态

| 文件 | 状态 | 预期 |
|------|------|------|
| `kzocr/config.py` | ✅ 尚未添加 `kzocr_output_dir` / `cache_ttl_seconds` | 符合预期（D0 为 P0，未实施） |
| `kzocr/engine/run.py:_run_vlm` (L438) | ✅ `except Exception: continue` 模式仍存在 | 符合预期（D2/D3 未实施） |
| `kzocr/engines/ratelimit.py` | ✅ `ExponentialBackoff` 已就绪 | 符合预期 |
| `kzocr/engines/atomic.py` | ✅ `_check_base` + `atomic_write` 已就绪 | 符合预期（D3 将复用） |
| `kzocr/engines/leakage.py:apply_leakage_defense` | ✅ L3 日志标记仍存在 | 符合预期（D2 实施后移除） |

---

## 5. 最终裁决

| 维度 | 结果 |
|------|------|
| round 6 问题（2 Blocking + 3 Major + 2 Medium） | ✅ **全部解决** |
| round 7 问题（2 must-fix + 1 建议修复） | ✅ **全部解决** |
| round 9 ops FAIL | ✅ **已解决（TTL 强制实施）** |
| rc4 → rc5 增量架构风险 | **0** |
| 方案内部一致性 | ✅ 一致 |
| 架构原则合规 | ✅ 合规 |
| 实施风险 | **低**（主要风险均有标注和缓解措施） |
| 实施顺序（P0→P1→P2→P3） | ✅ 合理 |
| 源码预实施状态 | ✅一致，无偏差 |

---

**APPROVED**

v0.5-rc5（`948f42e`）方案经过 10 轮迭代和 6 角色（架构师、软件工程、测试、安全、领域、运维）评审，历经 rc1→rc2→rc3→rc4→rc5 五版修订：

- **2 项 Blocking** 已关闭（D3-1 Config 缺失 → D0 新增；D3-2 缓存语义冲突 → engine_tag + config_hash）
- **3 项 Major** 已关闭（D1-1 YAGNI 过度设计 → 4 类型精简；D2-1 retry_with_policy 脱节 → D2 实际消费；D2-2 Oversize 静默 fallback → RetryExhaustedError 抛出）
- **1 项 ops FAIL** 已关闭（D3 缓存无 TTL → `_cache_is_valid` mtime 检查 + `cache_ttl_seconds`）
- **6 项 must-fix/Medium/建议修复** 全部关闭
- 剩余 3 项 Minor 留心项均为**设计偏好**（`retry_kwargs` 去留、`_compute_config_hash` 模块定位、`_process_vlm_page` 子函数提取）——非阻断

方案已具备进入 P0–P3 实施的全部条件。建议实施工程师遵循计划的实施顺序（P0 → P1 → P2 → P3）和注意事项（同人实施 D1+D2、D2+D3、适配器兼容检查等）推进开发。

---

*附录：本次终审参照的文档*
- `docs/plans/ocr-engine-unification.v0.5-AMEND.md` v0.5-rc5（`948f42e`）
- `docs/reviews/2026-07-10-round8/architect.md`（架构师终审，批准 rc3）
- `docs/reviews/2026-07-10-round9/architect.md`（架构师终局验证，批准 rc4）
- `kzocr/config.py`
- `kzocr/engine/run.py`
- `kzocr/engines/ratelimit.py`
- `kzocr/engines/atomic.py`
- `kzocr/engines/leakage.py`
