# 安全评审 — Round 2 (v0.7 自适应 OCR 引擎编排层)

| 字段 | 值 |
|------|-----|
| 审查对象 | `docs/plans/ocr-engine-unification.v0.7.md`（修订版）+ `docs/plans/ocr-engine-unification.v0.7-DETAILED.md` |
| 审查角色 | 安全工程师 |
| 审查日期 | 2026-07-10 |
| 涉及现有模块 | `kzocr/engine/types.py`, `kzocr/config.py`, `kzocr/run.py`, `kzocr/engines/egress.py` (B3), `kzocr/engines/ratelimit.py` (C3) |

---

## 总体判断

**条件通过 (APPROVED WITH CONDITIONS)。** 修订版方案已将第一轮 3 项严重问题（S1 API key 明文、S2 B3 egress 旁路、S3 allow_cloud_vision 缺失）全部修复。详细设计阶段进一步增加了安全控制（`EngineConfig` 类型约束、`_sanitize_error()` 凭证过滤、NDJSON 文件锁、0o700 文件权限等）。以下 3 项阻塞条件需在 Phase 1 实施前确认，5 项中等风险建议在 Phase 2 前处理。

---

## 第一轮 3 项严重问题复查

### S1: EngineRegistration.config 明文 API key（严重）

| 问题 | 初版 | 修订版 + 详细设计 | 判定 |
|------|------|-------------------|------|
| `config` 字段明文存 API key | `config: dict` 无约束 | `EngineConfig` dataclass（§9.1），只存 `api_key_env` 环境变量名引用（§3.1） | ✅ **已修复** |
| `ProbeResult.keys` 存明文 | `dict[str, str]` | 改为 `dict[str, bool]`（§3.3） | ✅ **已修复** |
| `_compute_config_hash()` 包含 API key | hash 输入含 key | 明确移除 API key（§3.3） | ✅ **已修复** |
| `__repr__` 泄露 | 无掩码 | 自定义 `__repr__` 省略 config（§1.2） | ✅ **已修复** |
| Benchmark NDJSON `error` 字段凭证泄露 | 未涉及 | `_sanitize_error()` 实现凭证模式过滤（§8.5） | ✅ **已修复** |
| NDJSON 不含 config | 未涉及 | 格式表明确不含任何凭证字段（§8.2） | ✅ **已修复** |

**残留验证：**
- `_resolve_config()`（§3.2）在 `api_key_env` 对应环境变量为空时抛出 `ConfigError`，防止静默用空 key 调用云 API
- 运行时从 `os.environ` 读取的 `api_key` 仅存在于局部变量，不存入 `EngineRegistration.config`

**验证要求（Phase 1）：** grep 确认代码中不存在向 `EngineConfig` 添加明文 `api_key` 字段的路径。建议增加 lint 规则 `no-plaintext-api-key` 防止回归。

### S2: Tier 2 B3 egress 校验旁路（高）

| 问题 | 初版 | 修订版 + 详细设计 | 判定 |
|------|------|-------------------|------|
| Tier 2 云端引擎调用链未挂载 `validate_url()` | Adapter 直接 `requests.post()` | §7.1 伪代码中 Tier 2 循环内置 `validate_url(engine.config.get("base_url", ""))`（L1221） | ✅ **已修复** |
| 校验失败处理 | 未定义 | `EgressBlockedError` → `mark_unavailable()` + `continue`（§4.5） | ✅ **已修复** |
| 导入路径 | 未明确 | `kzocr.security.egress.validate_url`（§4.5，修复架构 N3） | ✅ **已明确** |

**残留风险（中等）：** Probe 阶段的出站网络请求（`probe_engines()`）是否经过 `validate_url()`？§4.5 只覆盖了 **orchestrator 层 Tier 2 调用前**。Probe 阶段的 `requests.get(engine.base_url)` 也应经过 B3 校验。建议 Phase 1 实现时确认。

**验证要求（Phase 1）：** 确认 `egress.py` 存在且 `validate_url()` 函数实现正确（allowlist 匹配逻辑）。确认 `KZOCR_EGRESS_ALLOWLIST` 环境变量配置路径。

### S3: `allow_cloud_vision` 检查缺失（高）

| 问题 | 初版 | 修订版 + 详细设计 | 判定 |
|------|------|-------------------|------|
| Tier 2 循环缺少 `allow_cloud_vision` 检查 | 无检查 | `Budget.allow_cloud_vision` 从 Config 传入（§7.1），`select_candidates()` 第 4 步过滤 `requires_network` 引擎（§4.1） | ✅ **已修复** |
| 数据出境路径无拦截 | 页面图像直接发送云端 | 双层保护：调度器过滤 + Budget 传递 | ✅ **已修复** |

**残留验证：**
- 每个云端引擎（sensenova、siliconflow 等）的 `AdapterMeta.requires_network` 必须设为 `True`（默认 `False`）
- 建议增加 `_validate_probe_consistency()`：当 `probe.method == "api"` 但 `requires_network == False` 时，在启动时 warn 或抛异常

**验证要求（Phase 1）：** 确认所有云端引擎 registration 的 `requires_network` 值正确。

---

## 详细设计阶段新增安全隐患评估

### N1: `EngineConfig` 裸 `dict` 隐患（Round1 R1 延伸→已修复）

**严重程度：中，已修复**

第一轮安全评审指出 `EngineRegistration.config: dict` 缺乏约束。详细设计已将其改为带类型约束的 `EngineConfig` dataclass（§9.1）：
```python
@dataclass
class EngineConfig:
    api_key_env: str = ""
    base_url: str = ""
    extra: dict = field(default_factory=dict)
```

**状态：✅ 已修复。** 注意 `extra: dict` 是"非敏感额外参数"，但作为裸 dict 仍然可以放入任意内容。建议在 setter 或 validator 中增加守卫，或在文档中明确禁止在 `extra` 中存放凭证。

---

### N2: Benchmark NDJSON 竞态控制（Round1 R3→已修复）

**严重程度：中，已修复**

详细设计 §8.5 已采纳文件锁方案：
```python
fcntl.flock(f.fileno(), fcntl.LOCK_EX)
```
并在写入后检查文件大小、需要时截断（持有独占锁期间完成）。

**状态：✅ 已修复。** 需确认 `load_benchmarks()` 在读取期间是否正确处理锁（共享锁或忽略——读取不完整行已通过 `json.JSONDecodeError` → skip 处理）。

---

### N3: `_sanitize_error()` 凭证过滤模式（Round1 R2→已修复）

**严重程度：中，已修复**

详细设计 §8.5 定义 `_CREDENTIAL_PATTERNS`：
```python
_CREDENTIAL_PATTERNS = [
    r'(api_key|token|secret|password)[=:]\s*\S+',
    r'(sk-[a-zA-Z0-9]{20,})',
]
```

**状态：✅ 已修复。** 追加验证：
- `sk-` 模式能覆盖 OpenAI/SenseNova 类 API key 格式
- 但 DeepSeek 的 API key 格式 `sk-xxxx` 已被覆盖 ✅
- `Authorization: Bearer xxx` 头部中的 token **未覆盖**——部分 HTTP 错误消息可能包含 `Authorization` 头文本。建议补充模式：`r'(Bearer\s+)[a-zA-Z0-9_-]{20,}'`
- 截断先于过滤执行（`result[:200]`再过滤），可能导致过滤模式跨截断边界失效。建议**先过滤再截断**。

---

### N4: Probe 阶段出站请求安全（Round1 4.2→未修复）

**严重程度：中**

Probe 阶段 `probe_engines()` 对每个已注册引擎执行网络检查（`probe.method == "api"` 时 `requests.get(engine.base_url)`）。详细设计 §1.1 定义了 `probe` 字典（`method`, `key`），但**未明确 probe 阶段的网络请求是否经过 `validate_url()`**。

**影响：** 如果攻击者能控制 `AdapterMeta.probe` 配置（通过恶意引擎注册或配置篡改），probe 阶段会向任意 URL 发起 HTTP 请求，暴露内网结构或触发非预期出站流量。

**建议：** 在 `probe_engines()` 中，对 `probe.method == "api"` 的探测也调用 `validate_url()`，与 Tier 2 调用前一致。可以在公共 `_validate_engine_url()` 函数中统一。

---

### N5: VLM 缓存可能缓存意外凭证文本（Round3 R7→未完全修复）

**严重程度：低**

§7.6 的 D3 VLM 缓存将引擎返回的文本直接缓存到磁盘。如果云引擎在异常或调试模式下返回了包含 API key/Token 的文本（如 `{"error": "Invalid API key: sk-xxx"}`），该文本会进入缓存文件。

详细设计 §7.6 的伪代码显示 `_save_vlm_cache(config, book_code, page_num, result.text)` 直接缓存引擎原始输出。

**建议：** 缓存写入前对 `result.text` 做凭证模式扫描（复用 `_sanitize_error()` 模式），或至少缓存通过 `GlyphVerifier` 验证后的清洗文本，而非引擎原始输出。

---

### N6: Trace 文件的凭证风险

**严重程度：低—中**

§7.1 中 `EngineCallRecord.error` 字段（§6.2）标记为"已 sanitize，无凭证"。详细设计未明确 `.error` 字段 sanitize 的具体实现——是复用 `_sanitize_error()` 还是另有实现？

此外，trace 文件默认输出到 `$KZOCR_OUTPUT_DIR/trace/`（§7.1 L1307），文件权限未在详细设计中指定。建议：
- 对所有写入 trace 的 `error` 字段调用 `_sanitize_error()`
- trace 目录默认权限 `0o700`
- trace 保留 7 天后自动清理（`trace_retention_days` 已定义于 §9.2）

---

### N7: `domain_adjust()` 公式安全性

**严重程度：低**

`domain_adjust()`（§4.3）的 `base_score * 1.5 + 0.2` 混合偏移用于竖排页 Tier 2/3。该公式本身不引入安全漏洞，但注意 `PageInfo.book_type` 和 `pub_era` 来自配置/外部输入。如果这些值未被校验就被用于 `domain_adjust()`，可能被用于探测内部配置信息（信息泄露）。

**建议：** 配置层约束 `book_type` 和 `pub_era` 的允许值集合，不在调度器层做二次校验（性能考虑），但需确认配置层有校验。

---

### N8: `_run_single_engine_with_timeout()` 僵尸线程

**严重程度：低**

§7.3 使用 `concurrent.futures.ThreadPoolExecutor` 实现引擎调用超时。设计文档已注明"挂死的线程会遗留为僵尸线程。v0.7 串行模式下数量可控（≤3），可接受。"

**安全视角：** 僵尸线程虽不直接构成安全风险，但泄露的文件描述符或网络连接可能耗尽资源（DoS 前提）。建议在 Phase 2/3 的并行模式实现时改用 `multiprocessing` 或 `asyncio.wait_for()`，或增加线程池大小监控和泄漏检测。

---

### N9: 引擎适配映射表中的 SQL 注入风险（安全边界外）

**严重程度：信息性**

§6.4 中的 `unirec` 引擎被归类为 `run_book`（BookPipeline 包装器）Tier 1。若 `unirec` 或 `kimi_pipeline` 内部构造 SQL 查询，且 `BookResult` 中的文本未经安全处理就进入数据库，可能存在 SQL 注入。

**不列入本次评审：** 这是下游引擎的内部安全问题，不在 v0.7 编排层的安全边界内。但建议在集成文档中注明**下游引擎的输出进入数据库前必须参数化**。

---

## 残留 & 新发现问题汇总

| # | 问题 | 严重性 | 影响面 | 状态 | 处理时机 |
|---|------|--------|--------|------|---------|
| S1 | API key 明文暴露 | **严重** | 全链路 | ✅ **已修复**（3.1–3.4, 9.1） | — |
| S2 | B3 egress 旁路 | **高** | SSRF | ✅ **已修复**（4.5, 7.1） | — |
| S3 | allow_cloud_vision 缺失 | **高** | 数据出境 | ✅ **已修复**（4.1, 7.1） | — |
| N4 | Probe 阶段出站未接 B3 | **中** | SSRF | ❌ 未修复 | Phase 1 实现时 |
| N6 | Trace 文件 error 字段 sanitize 未明确 | **低—中** | 凭证泄露 | ❌ 未完全明确 | Phase 1 |
| N5 | VLM 缓存可能缓存凭证文本 | **低** | 缓存文件泄露 | ❌ 未修复 | Phase 2 |
| N7 | `book_type`/`pub_era` 输入校验归属未明确 | **低** | 信息泄露 | ⚠️ 需确认配置层 | Phase 1 配置定义时 |
| N8 | 僵尸线程泄漏 FD/连接 | **低** | DoS 前提 | ⚠️ 已文档化 | Phase 3 并行模式 |

---

## 实施安全清单（Checklist for Phase 1–3）

### Phase 1 阻塞项

- [ ] **C1: `egress.py` 存在性确认。** 确认 `kzocr/security/egress.py` 实现了 `validate_url()` 函数。如不存在，立即创建骨架实现（allowlist 加载 + 域名匹配）。
- [ ] **C2: 云端引擎 `requires_network` 值确认。** 确认 sensenova、siliconflow 等云端引擎的 `AdapterMeta.requires_network = True`。
- [ ] **C3: Probe 阶段 B3 校验。** 在 `probe_engines()` 中对 `probe.method == "api"` 的探测 URL 调用 `validate_url()`。
- [ ] **C4: 先过滤再截断。** `_sanitize_error()` 应先做凭证模式过滤，再做长度截断（§8.5 当前顺序为截断→过滤，可能失效）。
- [ ] **C5: Trace `error` 字段 sanitize。** 确认 `EngineCallRecord.error` 写入 trace 前经过凭证过滤。

### Phase 2 建议项

- [ ] **R1: VLM 缓存写入前清洗。** `_save_vlm_cache()` 前对 `result.text` 做凭证模式扫描。
- [ ] **R2: `_sanitize_error()` 补充 `Bearer` token 模式。** 添加 `r'(Bearer\s+)[a-zA-Z0-9_-]{20,}'` 到 `_CREDENTIAL_PATTERNS`。
- [ ] **R3: benchmark 文件 `engine_name` 路径消毒。** 对 `engine_name` 做 `re.sub(r'[^a-zA-Z0-9_-]', '_', name)` 防止路径穿越。
- [ ] **R4: `EngineConfig.extra` 增加使用说明。** 文档约束只为"非敏感额外参数"，禁止存放凭证。

### Phase 3 建议项

- [ ] **R5: 僵尸线程监控。** 如果并行模式启用，需要 `ThreadPoolExecutor` 泄漏检测或改用 `asyncio`。

---

## 边界情况检查

| 场景 | 详细设计处理 | 评估 |
|------|-------------|------|
| 环境变量未设置（`api_key_env` 对应 var 不存在） | `_resolve_config` 提升 `ConfigError`（§3.2） | ✅ 正确 |
| `base_url` 为 `""`（空字符串）→ 传给 `validate_url()` | §4.5 伪代码 `engine.config.get("base_url", "")` → 空字符串传入 `validate_url()` | ⚠️ `validate_url("")` 应返回 `EgressBlockedError`（空 URL 不在 allowlist 中） |
| Benchmark NDJSON 文件不存在 | `mkdir(parents=True, exist_ok=True)` + `O_CREAT`（§8.5） | ✅ |
| Benchmark 加载时遇到坏行 | `json.JSONDecodeError` → warn → continue（§8.4） | ✅ |
| `allow_cloud_vision=False` + 只有云引擎可用 | 调度器过滤 → 返回空候选 → Tier 2/3 跳过 → HumanGate | ✅ 正确但建议记录 WARNING 日志 |
| Egress allowlist 为空（无白名单条目） | `validate_url()` 应拒绝所有 URL | ⚠️ 依赖 `egress.py` 实现 |
| 多个进程同时写入 benchmark | `fcntl.flock` 进程级互斥锁（§8.5） | ✅ 已修复 |
| Load 时遇到截断中的文件 | 不完整行 → `JSONDecodeError` → skip（§8.4） | ✅ |

---

## 实施前建议的安全扫描命令

Phase 1 代码落地后，建议运行以下 sink 扫描验证实现与设计一致：

```bash
# 1. 检查 EngineRegistration.config 是否真的用了 EngineConfig 而非裸 dict
grep -n 'config:' kzocr/engine/types.py | grep -v EngineConfig

# 2. 检查所有写 trace/NDJSON 的路径是否有凭证过滤
grep -n '\.error\|last_error' kzocr/scheduler/ --include='*.py'
grep -n 'write\|\.dump\|\.save' kzocr/scheduler/ --include='*.py'

# 3. 检查所有 HTTP 出站调用是否经过 validate_url
grep -rn 'requests\.\(get\|post\)' kzocr/ --include='*.py' | grep -v test | grep -v __pycache__

# 4. 检查 ProbeResult.keys 是否已改为 dict[str, bool]
grep -n 'class ProbeResult' kzocr/engine/types.py -A 10

# 5. 检查 _compute_config_hash 是否移除了 API key
grep -n 'api_key' kzocr/run.py | grep -i hash
```

---

## 最终裁决

| 维度 | 评分 | 说明 |
|------|------|------|
| S1 API key 隔离 | ✅ **已修复** | EngineConfig dataclass + 环境变量引用 + ProbeResult.keys bool + __repr__ 掩码 + _sanitize_error |
| S2 B3 egress | ✅ **已修复** | Orchestrator 层显式 validate_url()，EgressBlockedError 处理 |
| S3 allow_cloud_vision | ✅ **已修复** | 调度器过滤 + Budget 传递双层保护 |
| N4 Probe B3 | ❌ **待修复** | Phase 1 实施时需加入 |
| N5/N6 凭证过滤 | ⚠️ **部分** | NDJSON 有 sanitize，trace 和 VLM 缓存尚未明确 |
| N7/N8 其他 | ⚠️ **低风险** | 文档化跟踪 |
| Benchmark 竞态 | ✅ **已修复** | fcntl.flock + 截断独占锁 |
| EngineConfig 类型约束 | ✅ **已修复** | §9.1 定义的 EngineConfig dataclass |

**裁决：条件通过 (APPROVED WITH CONDITIONS)**

### 条件清单（Phase 1 实施前必须确认）

1. **C1:** `kzocr/security/egress.py` 文件存在且 `validate_url()` 已实现
2. **C2:** 所有云端引擎的 `AdapterMeta.requires_network = True`
3. **C3:** Probe 阶段出站请求接 B3 校验
4. **C4:** `_sanitize_error()` 先过滤再截断
5. **C5:** Trace 文件 error 字段凭证过滤实现

### 建议项（Phase 2 前处理）

6. VLM 缓存写入前凭证清洗
7. `_sanitize_error()` 补充 `Bearer` token 模式
8. benchmark 文件 `engine_name` 路径消毒
9. `EngineConfig.extra` 使用说明

**总体评估：** 详细设计在安全层面显著优于初版。Phase 1 重点实施数据模型 + 注册中心 + 资源桩文件，安全控制同步落地后，可以安全进入 Phase 2 的核心逻辑开发。
