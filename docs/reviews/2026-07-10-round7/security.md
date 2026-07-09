# 安全评审 — v0.5 AMEND 第二轮（D3 缓存模型复核）

> 评审对象：`/home/keen/KZOCR/docs/plans/ocr-engine-unification.v0.5-AMEND.md` (v0.5-rc2)
> 本轮焦点：R6 安全评审 3 条建议的采纳情况 + 3 个遗留问题确认
> 前置评审：`docs/reviews/2026-07-10-round6/security.md`

---

## 结论

**通过。** 3 条 R6 建议已采纳。TOCTOU 和内容完整性属于观察项（O1/O2），非 blocker。

逐项裁决：

| 问题 | 来源 | 裁决 |
|------|------|------|
| D3 缓存安全模型 | R6 建议 1–3 | **已闭环 → 通过** |
| TOCTOU 缓解 | R6 O1 | **O1 观察项 — 按声明上下文无实际风险** |
| 内容完整性 (sha256) | R6 O2 | **O2 观察项 — 延迟可接受** |

---

## 1. D3 缓存安全模型 — R6 3 条建议追踪

| R6 建议 | v0.5-rc2 采纳情况 | 状态 |
|---------|-------------------|------|
| **1. 缓存 TTL** — 建议 `KZOCR_CACHE_TTL_HOURS` 环境变量 | **已采纳**。`KZOCR_CACHE_TTL`（秒，默认 86400=24h）+ `KZOCR_CLEAR_CACHE=1` 全量清除 | ✅ 闭环 |
| **2. `atomic_write` 传 `allowed_base`** — 深度防御路径穿越 | **已采纳**。实施说明第 3 条明确："缓存路径必须经过 C2 路径穿越校验——调用 atomic_write(cache_path, text, allowed_base=cfg.kzocr_output_dir)" | ✅ 闭环 |
| **3. 权限声明** — `vlm_cache/` 继承 `output_dir` 的 `0600` | **部分采纳**。`atomic_write` 默认 umask 行为（无显式 `0o600`），但 plan 未声明权限策略。留作实现细节提醒 | ⚠️ 需实现时确认 |

### 1.1 新增防御机制（v0.5-rc2 额外吸收）

除 R6 3 条建议外，plan 追加了以下与安全相关的改进：

- **`engine_tag` 参与缓存路径** — 防止 SenseNova ↔ PaddleOCR-VL 切换时误用对方缓存。跨引擎缓存污染风险已排除。
- **`config_hash` 参数签名校验** — 缓存首行存 `# config_hash={sha256(VLM_PARAMS_JSON)[:16]}`，配置变化（engine、max_tokens、prompt 等）自动使缓存失效。既解决 C2 对"参数变→缓存该不该用"的语义冲突，也提供了比 `is_complete` 更精确的命中判定。
- **P0 `kzocr_output_dir` 经 C2 `_check_base` 校验** — D0 新增字段纳入路径穿越防护基线。

### 1.2 剩余观察（非 blocker）

- **`KZOCR_CACHE_TTL` 语义模糊**：plan 写"24 小时"但注单位秒（86400），实现时需统一为秒并明确语义为"文件 mtime 超过 TTL 即视为过期"。不是安全风险，但实现 bug 可使 TTL 形同虚设。
- **缓存目录权限未显式声明**：`atomic_write` 使用 `Path.write_text`（默认 umask），不保证 `0o600`。如果系统 umask 为 `0022`，`vlm_cache/` 内文件对同机其他进程可读。**建议**：实现时在 `_get_vlm_cache_path` 的父目录创建处显式 `chmod(0o700)`，或在 `atomic_write` 中增加 `mode` 参数。非 blocker，但低成本。

### 1.3 裁决

**D3 缓存安全模型：可接受（Acceptable）。** R6 三项建议的主要骨架——TTL（残存时间窗口上限）、路径穿越防护（`allowed_base`）、跨引擎隔离（`engine_tag`）——已全部落地。`config_hash` 作为额外防御层进一步收窄了参数不匹配场景的误缓存风险。

---

## 2. TOCTOU（R6 O1）— 窗口在检查与读取之间

### 当前实现

`_cache_is_valid` (D3.2) 使用 check-then-use 模式：

```python
def _cache_is_valid(cache_path: Path, cfg: Config) -> bool:
    if not is_complete(cache_path):          # check
        return False
    first_line = cache_path.read_text(...)   # use（细微时间差）
```

测试要求中列了 "`is_complete` 检查后再读取（TOCTOU 防护，security O1）" 但实现代码并未采用 R6 建议的 try-read-except 模式或文件锁。

### 风险分析

| 场景 | 窗口 | 实际风险 |
|------|------|---------|
| **单进程单线程（本地 FS）** | `stat()`→`read_text()` 之间无竞争线程，内核不会在该时间窗口内删除/替换文件 | **可忽略** |
| **共享文件系统 / NFS** | 另一节点可能删除/替换文件；`read_text()` 抛出 `FileNotFoundError` | **低**（但 NFS 不在设计目标） |
| **同一进程内多线程** | Python GIL 存在，但 POSIX 层面另一线程可 `unlink()` | **极低**（当前 `_run_vlm` 是单线程循环） |

### 裁决

**O1 观察项，非 blocker。** 理由：

1. Plan 的隐式设计假设是单进程单线程独占 `output_dir`。在此假设下，`stat()` 到 `read_text()` 之间无任何竞争路径。TOCTOU 的经典前提（存在可篡改文件系统的并发写入者）不成立。
2. 若实现时需要应对多进程场景（高级，非当前目标），建议使用 `try/except FileNotFoundError` 包裹读取，将 TOCTOU 窗口收窄为"文件被删除→`read_text()` 抛出异常"的瞬间错误路径。
3. **但测试要求提及 TOCTOU 容易产生误导**：测试中若构造"缓存存在→`is_complete` 返回 True→立即删除"的场景来验证 TOCTOU 防护，该测试在当前实现下必然失败。**建议**：要么将实现改为 try-read 模式，要么从测试要求中明确移除 TOCTOU 防护文字（改为"测试正常缓存命中路径"）。

---

## 3. 内容完整性 (sha256 snippet) — R6 O2 延迟

### R6 O2 原文

> 建议：`is_complete` 可考虑在缓存写入时存储一个简单校验和（`sha256[:8]` 到文件名后缀），读时校验。磁盘错误写入乱码不会使重跑时重新 OCR。

### 当前状态

**未实现。** `config_hash` 解决的是"命中是否由当前配置生成"的参数一致性校验，不解决"文件内容是否因磁盘静默错误损坏"的内容完整性校验。

### 延迟可接受性分析

| 因素 | 评估 |
|------|------|
| **磁盘静默数据损坏概率** | 极低（企业级 SSD / 云持久化盘的 UBER < 1e-15） |
| **损坏后果** | 返回乱码文本（非空白/空），可通过下游 `baseline.feed(text)` 的字数分布检测异常 |
| **TTL 兜底** | 即使写入乱码，24h 后缓存自动过期 → 下一次运行重新 OCR |
| **实现成本 vs. 收益** | sha256[:8] 的读/写/比较 ≈ 20 行代码，但实际收益（捕获磁盘损坏事件）极低 |

### 裁决

**O2 观察项 — 延迟可接受。** 待 `config_hash` 和 TTL 机制上线后，若实际运维中观察到缓存损坏事件，再引入内容校验。在当前场景下，三机制（`is_complete` + `config_hash` + TTL）已提供充分的缓存有效性保障。

**如果未来实现**，最轻量的方案是在 `atomic_write` 末尾追加一行 `# sha256={hash}\n`（而非文件名后缀），读缓存时跳过元数据行后用剩余内容校验，避免对文件系统的额外操作。

---

## 4. 其他观察（R6 未覆盖）

### 4.1 `KZOCR_OUTPUT_DIR` 默认值 `/tmp/kzocr/output` 的安全隐患

D0 将默认值设为 `/tmp/kzocr/output`。Linux `/tmp` 的权限是 `1777`（粘滞位），任何用户可在 `/tmp` 下创建目录，但创建后目录权限取决于 umask。

- 若系统 umask `0022` → `/tmp/kzocr/output` 创建时权限 `drwxr-xr-x` → 同机其他进程可读缓存文件
- `atomic_write` 默认 umask 行为不保证 `0o600`

这不是 D3 特有风险——M-f（归档落盘）也有相同情况——但在 `/tmp` 下更突出，因为共享访问可能更大。

**建议（非 blocker）：** 确保 `vlm_cache/` 目录创建时显式 `os.chmod(dir, 0o700)`，与 `atomic_write` 的 `0o600` 文件配合，形成完整的"目录 700 + 文件 600"权限链。

### 4.2 D1/D2 异常安全无变化

D1 `retry_with_policy` 和 D2 VLM 主循环的异常处理改进与 R6 判断一致：**不劣于现状，部分场景（失败分类记录）优于现状**。无需额外安全关注。

### 4.3 D4 层级异常检测无变化

输出 `hierarchy_anomalies.json` 仅含方剂编号与页码，不含全文文本。与 R6 判断一致：**无敏感数据风险**。

---

## 总结

| 项目 | R6 建议 | v0.5-rc2 状态 | 本轮裁决 |
|------|---------|---------------|----------|
| D3 TTL | 建议 | `KZOCR_CACHE_TTL` (86400s) | ✅ 闭环 |
| D3 allowed_base | 建议 | `atomic_write(cache_path, text, allowed_base=cfg.kzocr_output_dir)` | ✅ 闭环 |
| D3 权限声明 | 建议 | 未显式声明，`atomic_write` 默认 umask | ⚠️ 实现时注意 |
| TOCTOU | O1 观察 | 实现未改，测试要求提及但矛盾 | 非 blocker，测试文字需同步 |
| 内容完整性 | O2 观察 | 未实现 | 延迟可接受 |
| engine_tag + config_hash | — | 新增 | ✅ 增强 |

**一句话裁决：** D3 缓存安全模型已达可接受水平。TOCTOU 在单进程单线程本地文件系统上下文中无实际风险；内容完整性因 TTL 兜底和磁盘损坏概率极低，延迟合理。安全视角不做进一步阻塞要求。
