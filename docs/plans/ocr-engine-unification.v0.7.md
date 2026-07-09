# v0.7 自适应 OCR 引擎编排层

> 目标：将所有 OCR 引擎纳入统一注册中心，由调度器按可用性、历史表现、资源预算智能分派，配合多级字形验证兜底。

---

## 现状 vs 目标

| 维度 | 当前（v0.6） | 目标（v0.7） |
|------|-------------|-------------|
| 引擎选择 | 硬编码 if-else (mock/VLM/real) | 调度器从注册中心动态选择 |
| 引擎配置 | 分散的环境变量 | 引擎单元自描述配置 |
| 引擎状态 | 无（if 成功/else 抛） | EngineProbe + 健康状态 |
| 历史数据 | 无 | 每个引擎有 benchmark（耗时/通过率） |
| 字形验证 | 数据模型有字段（glyph_status），无调用 | 字形验证作为编排层的一级判定节点 |
| 降级链 | 固定硬编码（VLM→SenseNova→PaddleOCR-VL） | 调度器动态路由 |
| 并行 | 无（逐页串行） | 多引擎并行（同一页） |

---

## 架构总览

```
                    ┌──────────────────────────────────┐
                    │         EngineRegistry            │
                    │  ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐  │
                    │  │E1 │ │E2 │ │E3 │ │E4 │ │E5 │  │  ← 每引擎含: 元信息/状态/stats/配置
                    │  └───┘ └───┘ └───┘ └───┘ └───┘  │
                    └────────────────┬─────────────────┘
                                     │
                    ┌────────────────▼─────────────────┐
                    │      EngineScheduler              │
                    │  选择候选集 → 分派 → 归集结果      │
                    └────────────────┬─────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
     ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
     │   OCR Tier 1    │  │   OCR Tier 2    │  │   OCR Tier 3    │
     │ (OCR 引擎)      │  │ (云端视觉 LLM)  │  │ (本地中医 LLM)  │
     └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
              │                    │                    │
              ▼                    ▼                    ▼
     ┌──────────────────────────────────────────────────────┐
     │              GlyphVerifier（字形验证器）               │
     │  PASS → 认可 | UNKNOWN/FAIL → 下一级 | 全失败→Human  │
     └──────────────────────────────────────────────────────┘
```

---

## 实施步骤

### E1: EngineRegistry（引擎注册中心）

**文件:** `kzocr/scheduler/registry.py`（新增）

每引擎一个注册条目：

```python
@dataclass
class EngineRegistration:
    meta: AdapterMeta                    # 已有的 AdapterMeta（name/label/kind/...）
    config: dict                         # 引擎专属配置（环境变量映射 + 默认值）
    status: EngineStatus                 # HEALTHY / DEGRADED / UNAVAILABLE
    stats: EngineStats                   # 历史运行统计 + benchmark
```

```python
@dataclass
class EngineStats:
    total_calls: int = 0
    total_latency_ms: int = 0
    total_pages: int = 0
    glyph_pass_count: int = 0
    glyph_fail_count: int = 0
    last_error: str | None = None
    last_seen: float = 0.0  # time.monotonic()
    avg_latency_per_page_ms: float = 0.0
    glyph_pass_rate: float = 0.0  # pass / (pass + fail + unknown)
```

引擎探测 `probe_engines()`：
- 复用现有的 `ProbeResult`（`kzocr/engine/types.py:132`）
- 扩展为逐引擎探测：检查端口、API key、GPU、模型文件
- 返回 `list[EngineRegistration]`

**现有可复用的引擎列表：**

| 引擎名 | 类型 | 视觉 | 现有适配器位置 |
|--------|------|------|-------------|
| mock | 本地桩 | N | `kzocr/engine/mock.py` |
| paddleocr | 本地 OCR | N | kimi BookPipeline |
| rapidocr | 本地 OCR | N | kimi BookPipeline |
| mineru | 本地 OCR | N | kimi BookPipeline |
| unirec | 本地 OCR | N | kimi BookPipeline |
| sensenova | 云端 VLM | Y | `sensenova_adapter.py` |
| paddleocr_vl16 | 本地 VLM | Y | `paddleocr_vl16_adapter.py` |
| shizhengpt | 本地 LLM | Y | `shizhengpt_adapter.py` |
| kimi_pipeline | 书级管线 | N | BookPipeline |

### E2: EngineScheduler（引擎调度器）

**文件:** `kzocr/scheduler/scheduler.py`（新增）

调度策略：

1. **候选排序** — 按 `glyph_pass_rate × (1/avg_latency)` 加权排序
2. **层级约束** — Tier 约束（OCR 引擎 / 云端视觉 LLM / 本地视觉中医 LLM）
3. **资源过滤** — 当前状态 != UNAVAILABLE，VRAM 足够，网络可达
4. **预算检查** — wall-clock 总预算（复用 `KZOCR_TOTAL_TIMEOUT`）、token 预算

```python
def select_candidates(registry, tier: str, page: PageInfo, budget: Budget) -> list[EngineRegistration]:
    """返回该 tier 最优的 N 个候选引擎（N 可配置，默认 2）。"""
```

**Tier 定义：**

| Tier | 类型 | 引擎 | 最大并发数 |
|------|------|------|---------|
| 1 | OCR 引擎 | paddleocr, rapidocr, mineru, unirec（通过 BookPipeline 或直连） | 默认 2 |
| 2 | 云端视觉 LLM | sensenova, siliconflow VLM | 默认 1 |
| 3 | 本地视觉中医 LLM | shizhengpt, paddleocr_vl16 | 默认 1 |

### E3: GlyphVerifier（字形验证器）

**文件:** `kzocr/scheduler/verifier.py`（新增）

复用 B1 设计的 `glyph_status` 枚举和金匮要略/本草纲目字形校验规则：

```python
@dataclass
class GlyphVerdict:
    status: GlyphStatus   # PASS / RARE / UNKNOWN / FAIL / UNCERTAIN
    confidence: float
    details: str | None   # 验证理由（命中哪条规则）
```

验证器集成：
- D4 字符数尖峰检测（`hierarchy.py`）→ 标记 `UNCERTAIN`
- C1 跨页泄漏检测 → 标记 `FAIL`
- 药材名/术语知识库匹配 → 标记 `PASS` 或 `RARE`
- 形似混淆集（B5 resources/confusion_set.json）→ 标记 `UNKNOWN`

### E4: Orchestrator（编排主循环）

**文件:** `kzocr/scheduler/orchestrator.py`（新增）

替换当前 `run_engine()` 中的硬编码 if-else：

```python
def orchestrate_book(pdf_path: str, book_code: str | None, config) -> BookResult:
    registry = probe_engines(config)        # E1
    budget = Budget(config)                 # 时间/页数预算
    pages_text: list[str] = []
    failed_pages: dict[int, str] = {}
    
    for page in render_pages(pdf_path, config):
        verdict = GlyphVerdict(status="FAIL", confidence=0)
        
        # Tier 1: OCR 引擎
        if verdict.status in ("FAIL", "UNKNOWN"):
            engines = scheduler.select_candidates(registry, tier=1, page, budget)
            for engine in engines:
                result = engine.run(page)
                verdict = verifier.check(result.text, page.context)
                if verdict.status in ("PASS", "RARE"):
                    registry.record(engine, success=True, glyph=verdict)
                    break
                registry.record(engine, success=False, glyph=verdict)
        
        # Tier 2: 云端视觉 LLM
        if verdict.status in ("FAIL", "UNKNOWN"):
            engines = scheduler.select_candidates(registry, tier=2, page, budget)
            for engine in engines:
                result = engine.run(page)
                verdict = verifier.check(result.text, page.context)
                if verdict.status in ("PASS", "RARE"):
                    registry.record(engine, success=True, glyph=verdict)
                    break
                registry.record(engine, success=False, glyph=verdict)
        
        # Tier 3: 本地视觉中医 LLM
        if verdict.status in ("FAIL", "UNKNOWN"):
            engines = scheduler.select_candidates(registry, tier=3, page, budget)
            for engine in engines:
                result = engine.run(page)
                verdict = verifier.check(result.text, page.context)
                if verdict.status in ("PASS", "RARE"):
                    registry.record(engine, success=True, glyph=verdict)
                    break
                registry.record(engine, success=False, glyph=verdict)
        
        # HumanGate
        if verdict.status in ("FAIL", "UNKNOWN"):
            failed_pages[page.num] = f"All engines/modes failed. Last: {verdict.details}"
            continue
        
        pages_text.append(result.text)
        # 记录 benchmark 数据
    
    return BookResult(...)
```

### E5: 现有关联文件修改

| 文件 | 变更 |
|------|------|
| `kzocr/engine/run.py` | `run_engine()` 改为调用 Orchestrator（`orchestrate_book()`）|
| `kzocr/engine/types.py` | 扩展 `ProbeResult` 逐引擎探测，扩展 `EngineResult` 含 benchmark |
| `kzocr/config.py` | 新增调度相关配置（`KZOCR_MAX_TIER1_ENGINES`, `KZOCR_BENCHMARK_DIR` 等）|
| `kzocr/engines/errors.py` | 新增调度层异常（`SchedulerError`, `AllEnginesFailedError`）|
| `kzocr/cli.py` | 新增 `kzocr benchmark` 子命令录入历史数据 |
| `tests/` | 新增 `test_registry.py`, `test_scheduler.py`, `test_verifier.py`, `test_orchestrator.py` |

---

## 与现有设计的关系

| 已有设计 | 在 v0.7 中的角色 |
|---------|---------------|
| B1 `glyph_status`/`glyph_verified` | 字形验证器的输出载体 |
| B2 `adapter_to_line_result()` | 引擎结果 → 归一化 `LineResult` |
| B3 egress allowlist | Tier 2 云引擎的安全约束 |
| C1 leakage detection | 作为 GlyphVerifier 的一项检测器 |
| C2 atomic write | 结果写入的保护 |
| C3 rate limiter | 云引擎调用限速 |
| D1 errors/retry | 每引擎调用的重试策略 |
| D3 VLM cache | Tier 1-3 的通用缓存层 |
| D4 hierarchy anomaly | 作为 GlyphVerifier 的一项检测器 |
| bookmark cache | 引擎级缓存 |

---

## 要修改/新增的文件清单

**新增：**
```
kzocr/scheduler/__init__.py
kzocr/scheduler/registry.py     (E1: 引擎注册 + 探测 + benchmark 持久化)
kzocr/scheduler/scheduler.py    (E2: 候选排序 + 分派)
kzocr/scheduler/verifier.py     (E3: 字形验证器)
kzocr/scheduler/orchestrator.py (E4: 编排主循环)
```

**修改：**
```
kzocr/engine/run.py             (run_engine → orchestrate_book)
kzocr/engine/types.py           (扩展 ProbeResult, EngineStats)
kzocr/config.py                 (新增调度配置)
kzocr/engines/errors.py         (调度层异常)
kzocr/cli.py                    (benchmark 子命令)
```

**测试新增：**
```
tests/test_registry.py
tests/test_scheduler.py
tests/test_verifier.py
tests/test_orchestrator.py
```
