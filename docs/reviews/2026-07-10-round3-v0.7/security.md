# 安全评审 — Round 3 (v0.7 详细设计)

| 字段 | 值 |
|------|-----|
| 审查对象 | `docs/plans/ocr-engine-unification.v0.7-DETAILED.md` |
| 审查角色 | 安全工程师 |
| 审查日期 | 2026-07-10 |
| 本轮焦点 | S1–S3 在详细设计中的落地质量 + 新引入风险 |

---

## 总体判断

**有条件通过 (APPROVED WITH RESERVATIONS)。** S1–S3 的核心修复方向正确，详细设计中已充分体现第一轮安全建议。但详细设计阶段暴露出 3 个新的或残留的隐患，建议在 Phase 1/Phase 2 实施中解决后再进入 Phase 3 集成。

---

## 1. S1–S3 复查

### S1 — API key config 明文暴露

| 评估项 | 详细设计状态 | 判定 |
|--------|-------------|------|
| `EngineRegistration.config` 存环境变量引用而非明文 | §3.1 定义 `api_key_env` 模式；§3.2 `_resolve_config()` 运行时从 `os.environ` 读取；§1.2 标注 `# 仅存环境变量名引用，无明文凭证` | ✅ 已修复 |
| `ProbeResult.keys` 改为 `dict[str, bool]` | §3.3 定义 `keys: dict[str, bool]` | ✅ 已修复 |
| `_compute_config_hash()` 移除 API key 入参 | §3.3 明确提到 | ✅ 已修复 |
| `EngineRegistration.__repr__` 掩码敏感字段 | §1.2 自定义 `__repr__` 省略 config 字段 | ✅ 已修复 |

**残留风险（中等）：**

- **`config` 字段类型为裸 `dict`，缺乏结构性约束。** 当前 `EngineRegistration.config: dict = field(default_factory=dict)` 无运行时校验。未来开发者可能误向其中添加明文 key。建议使用 `NamedTuple` 或 `dataclass` 限定允许的字段：

  ```python
  @dataclass
  class EngineConfig:
      api_key_env: str = ""      # 环境变量名，非 key 值
      base_url: str = ""
      extra: dict = field(default_factory=dict)  # 非敏感额外参数
  ```

  或者至少在 `config` setter 中加 `_assert_no_plaintext_key()` 守卫。

- **Benchmark NDJSON `error` 字段未做凭证过滤。** NDJSON 格式（§8.2）包含 `error: str | null`。当引擎调用异常包含 API key（如 `requests.exceptions.HTTPError("401 Invalid API key: sk-...")`），该错误会被写入 NDJSON 文件。建议 `registry.record()` 中对 `error` 参数执行凭证模式过滤（如 `re.sub(r'(api_key|token|secret)[=:]\s*\S+', '...', text)`），或至少截断至前 100 字符且去除可能的 key 模式。

| S1 总体 | 核心路径已关闭，有 2 条残留需 Phase 1 处理 |

---

### S2 — Tier 2 B3 egress 校验旁路

| 评估项 | 详细设计状态 | 判定 |
|--------|-------------|------|
| Orchestrator 层显式调用 `validate_url()` | §4.5 Tier 2 循环内的伪代码包含 `validate_url(engine.config.get("base_url", ""))` | ✅ 已修复 |
| 导入路径为 `kzocr.security.egress` | §4.5 注明确认为 `kzocr.security.egress`（非 `kzocr.engines.egress`） | ✅ 确认正确 |
| 校验失败后的行为 | §4.5 `EgressBlockedError` → `mark_unavailable()` + `continue` | ✅ 合理 |
| Probe 阶段的出站请求 | 详细设计未明确 probe 阶段的网络请求是否接 B3 | ❌ 未修复（见第 3 节） |
| 注入点完整性 | §7.1 `orchestrate_book()` 中 Tier 2 循环前有 `validate_url()`（§7.1 第 1161 行），与 §4.5 一致 | ✅ 一致 |

| S2 总体 | 主线已关闭。probe 阶段残留 |

---

### S3 — `allow_cloud_vision` 检查缺失

| 评估项 | 详细设计状态 | 判定 |
|--------|-------------|------|
| `select_candidates()` 中过滤云端引擎 | §4.1 第 4 步包含 `if not budget.allow_cloud_vision: 过滤 requires_network` | ✅ 已修复 |
| `Budget.allow_cloud_vision` 从 Config 传入 | §7.1 `Budget(allow_cloud_vision=config.allow_cloud_vision)` | ✅ 已修复 |
| 引擎是否设置了 `requires_network` 标记 | `AdapterMeta.requires_network: bool = False`（§1.1） | ⚠️ 需确认每个云端引擎的 registration 正确设置此标记 |

- **注意：** 全部默认 `False`。如果某个云端引擎（如 sensenova）的 `AdapterMeta` 初始化时忘记设为 `True`，过滤将失效。建议在引擎注册表初始化时增加 `_validate_probe_consistency()` 检查——当 `probe.method == "api"` 但 `requires_network == False` 时，应 warn 或抛异常。

| S3 总体 | 架构正确。实施时需确保每个云端引擎的 `requires_network` 值正确 |

---

## 2. 详细设计新增隐患

### 2.1 ToxinDoseDetector 的 `re.escape` 防注入

**结论：设计正确。** `re.escape(herb)` 防止 `toxic_herbs.json` 中的药名含有 `+`, `(`, `)`, `.`, `*` 等正则元字符时被错误解释。攻击者若可以控制 `toxic_herbs.json` 内容，也无法注入正则语法。

**潜在威胁模型：**

- `toxic_herbs.json` 的来源需受控——若该文件可从外部加载（如配置指定路径），恶意的 JSON 内容无法通过 `re.escape` 注入 regex，但可控制 `max_dosage_g` 导致误报（DENIAL_OF_SERVICE 级别）。**建议：** 记录该文件为"受控资产，不可从用户输入路径加载"。

**副作用风险（低）：**

- `re.escape` 对中文无副作用（中文字符在正则中无特殊含义）。对含拉丁学名的药名（如 `Ephedra sinica` 中的 `.` 会被转义为 `\\.`），转义后仍能正确匹配字面量。✅

| ToxinDose re.escape | 通过。威胁模型仅限文件完整性，属 ops 域 |

---

### 2.2 Benchmark NDJSON 凭证隔离

**结论：设计层面已正确实现凭证隔离。** NDJSON 格式（§8.2）不含 config 字段；`persist_benchmarks()` 仅为统计值写入。**但需确认以下 2 点：**

1. **`error` 字段的凭证风险（重复 S1 残留）** — 见 S1 残留第 2 条。

2. **写入路径不可被用户控制。** §8.1 文件路径为 `$KZOCR_OUTPUT_DIR/benchmarks/{engine_name}.ndjson`。若 `engine_name` 来自不可信源（如配置中传入），可能路径穿越到预期目录之外。建议：
   - 对 `engine_name` 做 `re.sub(r'[^a-zA-Z0-9_-]', '_', engine_name)` 消毒
   - 或者将 `engine_name` 作为 NDJSON 行内字段而非文件名的决定部分

3. **文件权限在 §3.4 中提及但未在 `persist_benchmarks` 伪代码中实现。** `persist_benchmarks()` 目前为 `...`（待实现）。实施时需确保：
   ```python
   fd = os.open(path, os.O_WRONLY | O_APPEND | O_CREAT, 0o700)
   ```
   或使用 `atomic_write` 后 `os.chmod(path, 0o700)`。

---

### 2.3 NDJSON 并发写入竞态

#### 2.3.1 进程级并发

**风险等级：中**

当前设计：
- `persist_benchmarks()` 每本书完成后调用一次（批量 flush，§8.3）
- 追加式写入（行级追加）
- 复用 `kzocr/engines/atomic.py` 的原子写入

**问题分析：**

1. **`atomic.py` 的原子写入与追加式写入矛盾。** 典型的 `atomic_write` 实现是：写入临时文件 → `os.replace()` 重命名覆盖原文件。这与"追加式写入（行级追加）"不兼容——每次 flush 都会覆盖整个文件，不是追加。如果两个进程先后调用 `persist_benchmarks()`，后一个进程会覆盖前一个进程的 benchmark 数据。

2. **即使改用 `O_APPEND`，多进程并发时也存在行交错风险：**
   - Linux 内核保证 `O_APPEND` 下 **不超过 PIPE_BUF（4KB）** 的写入是原子追加
   - 但 Python `write()` → `flush()` 两步骤之间，另一个进程可能插入
   - 结果：混合的数据行、部分行被截断

3. **`load_benchmarks()` 与 `persist_benchmarks()` 的读写冲突：**
   - 若 `load_benchmarks()` 运行时 `persist_benchmarks()` 在写入 → 读到不完整的行 → `json.JSONDecodeError` → 跳过（§8.4 已处理断路）
   - 但写时的截断（100MB 自动截断最老 50%）与并发读必然冲突

**建议：**

| 方案 | 复杂度 | 适用性 |
|------|--------|--------|
| **文件锁**：每个 NDJSON 文件关联一个 `.lock` 文件，使用 `fcntl.flock()` 实现进程级互斥 | 中 | 推荐——简单可靠，Python 原生支持 |
| **独立文件**：每本书写入独立 NDJSON 文件，`load_benchmarks()` 遍历加载 | 低 | 简单但文件数爆炸（§8.3 已排除） |
| **SQLite**：用 SQLite 替代 NDJSON，自带 MVCC 并发控制 | 高 | 过渡设计，v0.7 可能不需要 |

**推荐实施：**

```python
import fcntl

def _append_benchmark(engine_name: str, event: dict):
    path = Path(benchmark_dir) / f"{engine_name}.ndjson"
    with open(path, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(event) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
```

#### 2.3.2 100MB 截断的竞态

§8.5 "文件超 100MB 时自动截断最老 50%"。截断操作的典型实现是：
1. 读取完整文件
2. 保留最新 N 行
3. 写回
4. 覆盖原文件

**在步骤 2–3 之间，并发写入的进程会丢失数据（写入被覆盖）。** 即使单进程也可能有问题——若自动截断由其他调度器触发，与 `persist_benchmarks` 并发。

**建议：** 截断操作需独占文件锁，且完成后立即通知其他进程。简单做法：在 `_append_benchmark` 的写入路径中判断文件大小，若超限则持有锁完成截断。

#### 2.3.3 进程隔离

当前系统有无可能多进程运行？从 `run.py:run_engine()` 委派模式看，CLI 是单进程调用。但若未来加入并行处理（如同时处理多本书），或用户手动在多个终端运行 KZOCR，竞态即为现实风险。

| NDJSON 竞态 | 需在 Phase 1 实现中添加文件锁。不修复可接受（单进程使用），但设计文档应注明非线程安全/非进程安全 |

---

### 2.4 其他新发现

#### 2.4.1 VLM 缓存的凭证残留

§7.6 D3 VLM 缓存写入 `_save_vlm_cache()` 缓存引擎返回文本。如果某个引擎将 API key 或敏感信息误吐回响应文本（如 `"Request submitted with API key: xxxx"`），该文本会被缓存到磁盘。**建议：** VLM 缓存不应缓存引擎返回的原始文本，而应缓存已通过 `GlyphVerifier` 的清洗后文本；或在缓存写入前做模式扫描。

#### 2.4.2 `_resolve_config()` 的 key 为空时行为

§3.2 中 `os.environ.get(key_env, "")` 在环境变量未设置时返回空字符串。后续云引擎调用时使用空 key 会返回 HTTP 401。这本身不构成安全风险（认证失败自然拒绝），但空 key 可能通过 `error` 字段写入 NDJSON 或日志（`"401 Invalid API key: "`），暴露了配置缺陷。**建议：** 在 `_resolve_config()` 中增加 `if not resolved["api_key"]: raise ConfigError(...)`，早失败而非隐式故障。

#### 2.4.3 `EngineRegistration.config` 中的 `base_url` 未校验

`engine.config.get("base_url", "")` 直接传递给 `validate_url()`。`validate_url()` 本身的 allowlist 防御是正确的，但 URL 格式的鲁棒性需确保。若 `base_url` 为 `""`（空字符串），`validate_url()` 应优雅拒绝而非处于不确定状态。**建议：** 在 egress 校验前加空 URL 检查。

---

## 3. 残留 & 新发现问题汇总

| # | 问题 | 严重性 | 影响面 | 提出轮次 | 处理时机 |
|---|------|--------|--------|---------|---------|
| R1 | `config` 字段裸 `dict`，缺乏结构性约束 | 中 | 开发者误用 | 本轮 | Phase 1 改用 typed dataclass |
| R2 | NDJSON `error` 字段未凭证过滤 | 中 | 磁盘凭证泄露 | 本轮 | Phase 1 `registry.record()` 增加 |
| R3 | NDJSON 追加无进程级文件锁 | 中 | 多进程竞态数据丢失 | 本轮 | Phase 1 `_append_benchmark` 加 `fcntl.flock` |
| R4 | Probe 阶段出站请求未接 B3 校验 | 中 | SSRF | Round1 4.2 | Phase 1 probe 实现时 |
| R5 | `requires_network` 默认 `False`，云端引擎可能遗漏标记 | 低 | S3 过滤失效 | 本轮 | Phase 1 注册表初始化加 `_validate_probe_consistency()` |
| R6 | 截断 100MB 操作非原子，与并发写入冲突 | 低 | 多进程下数据丢失 | 本轮 | Phase 2 截断函数加锁 |
| R7 | VLM 缓存可能缓存引擎返回的意外凭证文本 | 低 | 缓存文件泄露 | 本轮 | Phase 2 缓存写入前清洗 |
| R8 | `_resolve_config()` 空 key 应早 fail | 低 | 便于排障 | 本轮 | Phase 1 |

---

## 4. 结论

详细设计已正确落地第一轮 S1–S3 的安全要求。两个核心路径（API key 隔离、B3 egress 集成）设计正确且有伪代码佐证。本轮发现的主要问题集中于：

1. **NDJSON 持久化的竞态和凭证漂移（R2+R3）** — 是详细设计阶段新暴露的隐患。单进程使用无影响，但在任何并行场景下需要解决。
2. **数据类型约束（R1）** — 裸 `dict` 不符合防御性编程原则，应在 Phase 1 尽早改为带约束的类型。
3. **Probe 阶段 B3 集成（R4）** — 第一轮 4.2 提及但未在详细设计中修复。probe 阶段若发出非预期出站请求，可能暴露内网结构。

**建议 Phase 1 实施完成后，对以下 3 个文件做一轮安全 sink 扫描**（工具：`grep -n 'write\|requests\.post\|requests\.get\|os\.environ\|save\|api_key'`），以确保落地与设计一致：

- `kzocr/engine/registry.py`（EngineRegistration / persist_benchmarks）
- `kzocr/scheduler/scheduler.py`（select_candidates / probe）
- `kzocr/orchestrator/orchestrator.py`（orchestrate_book / egress / VLM cache）
