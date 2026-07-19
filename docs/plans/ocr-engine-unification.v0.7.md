# v0.7 自适应 OCR 引擎编排层（修订版）

> 目标：将所有 OCR 引擎纳入统一注册中心，由调度器按可用性、历史表现、资源预算智能分派，配合多级字形验证兜底。本版修复了第一轮 8 份评审报告（架构/软件工程/领域/安全/运维/性能/测试/PM）中指出的全部 10 项阻塞问题和 6 项强烈建议。

---

## 1. 现状 vs 目标

| 维度 | 当前（v0.6） | 目标（v0.7） |
|------|-------------|-------------|
| 引擎选择 | 硬编码 if-else（mock/VLM/real） | 调度器从注册中心动态选择 |
| 引擎配置 | 分散的环境变量 | 引擎单元自描述配置（API key 仅存引用，无明文） |
| 引擎状态 | 无（if 成功/else 抛） | EngineProbe + 健康状态 + 时效衰减 |
| 历史数据 | 无 | 每个引擎有 benchmark（NDJSON 追加式持久化） |
| 字形验证 | 数据模型有字段（glyph_status），无调用 | 字形验证作为编排层的一级判定节点；Detector 插件架构 |
| 降级链 | 固定硬编码（VLM→SenseNova→PaddleOCR-VL） | 调度器动态路由，支持竖排跳过 T1 |
| 并行 | 无（逐页串行） | **默认串行**；GPU 环境可通过 `KZOCR_ENGINE_PARALLEL=1` opt-in |
| 冷启动 | 不适用（硬编码无选择） | 贝叶斯平均平滑 + 预设优先级 + 轮询调度 |
| 可观测性 | 基本无 | 调度决策结构化日志 + trace JSON + 引擎报告 |
| API key 安全 | 明文配置 / hash 输入 | 环境变量引用，config 不存明文 |
| 人工校对 | 无反馈闭环 | review_manifest 结构化输出 + 反馈回写 |

---

## 2. 架构总览

### 2.1 整体架构

```
                    ┌──────────────────────────────────┐
                    │         EngineRegistry            │
                    │  ┌───┐ ┌───┐ ┌───┐ ┌───┐ ┌───┐  │
                    │  │E1 │ │E2 │ │E3 │ │E4 │ │E5 │  │  ← 每引擎含: 元信息/状态/stats/配置引用
                    │  └───┘ └───┘ └───┘ └───┘ └───┘  │
                    └────────────────┬─────────────────┘
                                     │
                    ┌────────────────▼─────────────────┐
                    │      EngineScheduler              │
                    │  候选排序 → 层级约束 → 资源过滤    │
                    │  → 预算检查 → 取 Top-N             │
                    │  (贝叶斯平滑 + 衰减因子)           │
                    └────────────────┬─────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
     ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
     │   OCR Tier 1    │  │   OCR Tier 2    │  │   OCR Tier 3    │
     │ (OCR 引擎)      │  │ (云端视觉 LLM)  │  │ (本地中医 LLM)  │
     │ 书级: BookPipe  │  │ 页级: run_page  │  │ 页级: run_page  │
     └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
              │                    │                    │
              ▼                    ▼                    ▼
     ┌──────────────────────────────────────────────────────┐
     │              GlyphVerifier（字形验证器）               │
     │  Detector 流水线:                                      │
     │  1. CharCountSpikeDetector (D4) → UNCERTAIN           │
     │  2. ToxinDoseDetector → FAIL/CRITICAL                 │
     │  3. ConfusionSetDetector → UNKNOWN                    │
     │  4. TermKBMatcher → PASS/RARE                         │
     │  5. LeakageDetector (C1) → FAIL                       │
     │  (竖排检测在 Scheduler 层, 非 Verifier)                │
     └──────────────────────────────────────────────────────┘
```

### 2.2 EngineRunner 协议（新增）

解决「评审 B1/B2：`engine.run(page)` 不存在」的核心设计缺口。

**文件：** `kzocr/engine/types.py`（新增协议层）

```python
class EngineRunner(Protocol):
    """引擎统一执行接口。"""

    def run_page(self, page: PageInput) -> AdapterPageResult:
        """页级执行：输入单页图像，返回归一化结果。
        所有 page-level 引擎（VLM/LLM 适配器）实现此方法。"""
        ...

    def run_book(self, pdf_path: str) -> BookResult:
        """书级执行：输入 PDF 路径，返回全书结果。
        仅在 kind='book' 时支持，如 BookPipeline。"""
        ...
```

**新编排模型（两级流水线）：**

```
  Tier 1 (书级引擎):
    BookPipeline 一次处理全书 → BookResult
    ↓ 逐页取 results[i].text 过 GlyphVerifier
    ↓ 失败页 → 进入页级降级

  Tier 2/3 (页级引擎):
    逐页调用 run_page() 做补充 OCR
```

这替代了原方案中「逐页调用所有引擎」的单级循环，避免书级引擎被碎片化调用。

**EngineRegistration 引擎实例引用：**

```python
@dataclass
class EngineRegistration:
    meta: AdapterMeta
    config: dict                     # 仅存配置引用（环境变量名），不存明文凭证
    status: EngineStatus
    stats: EngineStats
    adapter: Callable | None = None  # 引擎可调用实例（EngineRunner），供 Orchestrator 使用
```

---

## 3. E1: EngineRegistry（引擎注册中心）

**文件：** `kzocr/scheduler/registry.py`（新增）

### 3.1 数据模型

```python
@dataclass
class EngineRegistration:
    meta: AdapterMeta                    # 见 3.2 扩展后的 AdapterMeta
    config: dict                         # 仅存环境变量名引用，无明文凭证
    status: EngineStatus                 # HEALTHY / DEGRADED / UNAVAILABLE
    stats: EngineStats                   # 历史运行统计
    adapter: Callable | None = None      # EngineRunner 实例引用

@dataclass
class EngineStats:
    total_calls: int = 0
    total_latency_ms: int = 0
    total_pages: int = 0
    glyph_pass_count: int = 0
    glyph_fail_count: int = 0
    glyph_unknown_count: int = 0         # 新增：追踪 UNKNOWN 状态
    last_error: str | None = None
    last_seen: float = 0.0              # time.time() — 挂钟时间，支持跨进程持久化
    # 派生字段在访问时实时计算，不存储（避免更新顺序不一致）
```

**关键设计决策：**
- 只存储 **原始累加值**（`total_calls`、`total_latency_ms` 等），派生值（`avg_latency_per_page_ms`、`glyph_pass_rate`）在访问时计算。
- `last_seen` 使用 `time.time()` 而非 `time.monotonic()`，支持跨进程持久化。
- 增加 `glyph_unknown_count` 字段，匹配 `GlyphStatus.UNKNOWN`。

### 3.2 AdapterMeta 扩展（解决 B3/B14）

**文件：** `kzocr/engine/types.py`

```python
@dataclass
class AdapterMeta:
    name: str
    label: str
    kind: AdapterKind = "page"          # page / book
    tier: int = 1                       # 新增：Tier 归属 (1/2/3)
    batch_capable: bool = False         # 新增：是否支持书级输入（BookPipeline 模式）
    supports_confidence: bool = True
    supports_context: bool = False
    min_vram_gb: float = 0.0
    default_enabled: bool = True
    requires_gpu: bool = False
    requires_network: bool = False
    probe: dict = field(default_factory=lambda: {
        "method": "env",                # env / port / file / api
        "key": "SENSENOVA_API_KEY",     # 探测目标
    })                                  # 新增：逐引擎探测描述
```

### 3.3 API key 安全（解决「评审安全 S1」）

**`EngineRegistration.config` 绝不存储 API key 明文。** 改为存环境变量名引用：

```python
# ❌ 禁止：
config = {"api_key": "sk-xxxxxx", "base_url": "https://..."}

# ✅ 允许：
config = {
    "api_key_env": "SENSENOVA_API_KEY",   # 运行时从 os.environ 读取
    "base_url": "https://api.sensenova.com/v1",
}
```

**配套改动：**
- `ProbeResult.keys` 由 `dict[str, str]` 改为 `dict[str, bool]`（仅存是否存在，不存值）。
- `_compute_config_hash()` 移除 API key 作为 hash 输入。
- `EngineStats` 添加 `__str__`/`__repr__` 掩码敏感字段。

### 3.4 引擎探测 `probe_engines()`

复用现有的 `ProbeResult`（`kzocr/engine/types.py:132`）作为缓存/输入源，扩展为逐引擎探测：

1. 先查 `ProbeResult` 全局信息（GPU/VRAM/端口）
2. 对每个引擎按 `AdapterMeta.probe` 定义执行实时验证：
   - `method="env"`：检查 `os.environ.get(key)` 是否存在
   - `method="port"`：TCP connect 目标端口
   - `method="file"`：检查模型文件路径是否存在
   - `method="api"`：执行一次轻量 API 健康检查（需 B3 校验）
3. 惰性探测：仅在 `select_candidates()` 选为候选时才执行网络/API 类探测

**现有引擎注册表：**

| 引擎名 | tier | kind | probe |
|--------|------|------|-------|
| mock | 0 (special) | book | 始终可用 |
| paddleocr | 1 | book | 引擎目录存在 |
| rapidocr | 1 | book | 引擎目录存在 |
| mineru | 1 | book | 引擎目录存在 |
| unirec | 1 | book | 引擎目录存在 |
| sensenova | 2 | page | api_key 环境变量 + 网络可达 |
| paddleocr_vl16 | 3 | page | 端口 18080 可达 |
| shizhengpt | 3 | page | 模型文件存在 |
| kimi_pipeline | 1 | book | 引擎目录存在 |

### 3.5 冷启动默认值（解决「阻塞 B4/冷启动NaN」）

`EngineStats` 首次运行（`total_calls == 0`）时的默认值：

```python
GLYPH_PASS_RATE_DEFAULT = 0.5    # 中等置信度假设，非 0
AVG_LATENCY_DEFAULT_MS = 10000   # 10s 保守估计，非 0
```

调度器评分公式使用贝叶斯平均（Bayesian Average）——替代原方案中 `0 × (1/0) = NaN` 的退化公式：

```
score = (pass_rate_avg × n + C × prior) / (n + C) × (1 / latency_avg)

其中：
  n = total_pages（该引擎历史处理页数）
  C = 7（贝叶斯常数，控制先验权重）
  prior = 0.7（全局先验通过率）
  latency_avg = avg_latency_per_page_ms（有数据）/ AVG_LATENCY_DEFAULT_MS（无数据）
```

首次运行的引擎退化到 `prior × (1 / AVG_LATENCY_DEFAULT_MS)`，确保被选中机会。

**冷启动期的辅助策略：**
- 首次运行按预设优先级（Tier 内定序：paddleocr > rapidocr > mineru > unirec）
- 以 5% 概率执行**轮询调度**，即使引擎排名低也定期采样，避免冷启动陷阱
- 轮询采样**参与**衰减（见 E2；轮询选中的引擎经 `record()` 更新 `last_seen`，评分随之提升）

---

## 4. E2: EngineScheduler（引擎调度器）

**文件：** `kzocr/scheduler/scheduler.py`（新增）

### 4.1 调度策略流程（明确顺序 — 解决 S5）

```
1. 层级约束 → 2. allow_cloud_vision 检查 → 3. 资源过滤（状态位缓存） → 4. 预算检查 → 5. 贝叶斯加权排序 → 6. 取 Top-N
```

**每个步骤的详细规则：**

1. **层级约束** — 只保留指定 Tier 的引擎（`registration.meta.tier == target_tier`）
2. **allow_cloud_vision 检查**（解决 S3）— 若 `cfg.allow_cloud_vision == False`，过滤掉所有 `requires_network=True` 的引擎
3. **资源过滤** — 仅检查状态位缓存（`EngineStatus != UNAVAILABLE`），不做实时 TCP/CUDA 调用。状态缓存由 `EngineRegistry.mark_unavailable()` 或周期 probe 刷新
4. **预算检查** — `Budget.check()` 调用预算：wall-clock、页数配额、token 预算
5. **排序** — 贝叶斯加权评分 + 衰减因子
6. **取 Top-N** — N 由 `max_tier{N}_engines` 配置（默认 Tier 1: 2, Tier 2: 1, Tier 3: 1）

### 4.2 衰减因子（解决「评审 B6 时效性」）

引入时间衰减，防止 30 天前的历史高分引擎被持续选中：

```python
def decay(self, half_life_days: float = 7.0) -> float:
    """半衰期衰减。last_seen 越久，衰减越强；7 天后衰减到 0.5。"""
    elapsed_days = (time.time() - self.last_seen) / 86400
    return 0.5 ** (elapsed_days / half_life_days)

# 有效评分 = 贝叶斯评分 × decay()
effective_score = bayesian_score * decay()
```

- 默认半衰期 7 天
- 轮询采样**参与**衰减：轮询选中的引擎经 `record()` 正常更新 `last_seen`，评分随之提升（探索预期效果；原"不参与衰减"声明已在 round3 修订）

### 4.3 竖排感知调度（解决「领域 P0 竖排盲区」）

```python
def select_candidates(registry, tier: int, page: PageInfo, budget: Budget,
                      page_layout: PageLayout | None = None) -> list[EngineRegistration]:
    candidates = _filter_by_tier(registry, tier)
    
    # 竖排检测：若页面为竖排布局，跳过 Tier 1
    if page_layout and page_layout.is_vertical and tier == 1:
        logger.info("[scheduler] page=%d vertical layout detected, skipping Tier 1", page.num)
        return []
    
    # allow_cloud_vision 过滤
    if not budget.allow_cloud_vision:
        candidates = [e for e in candidates if not e.meta.requires_network]
    
    # ... 继续排序逻辑
```

`PageLayout.is_vertical` 由页面渲染阶段的光学布局分析得出（已有 `hierarchy.py` 中的基线页面特征）。

### 4.4 领域感知排序权重（解决「领域 4」）

```python
def domain_adjust(base_score: float, engine: EngineRegistration,
                  page_info: PageInfo, book_info: BookInfo) -> float:
    adjustments = 1.0
    # 竖排对 Tier 1 降权
    if page_info.is_vertical and engine.meta.tier == 1:
        adjustments *= 0.3
    # 雕版印刷对 VLM 提权
    if book_info.pub_era == "lead_print" and engine.meta.tier >= 2:
        adjustments *= 1.5
    # 表格页对 OCR 降权
    if page_info.has_table and engine.meta.tier == 1:
        adjustments *= 0.5
    # 冷启动降权（样本量不足）
    if engine.stats.total_pages < 10:
        adjustments *= 0.8
    return base_score * adjustments
```

### 4.5 B3 egress 校验 + 手动引擎覆盖

**egress 校验**（解决「评审安全 S2」）：

`select_candidates()` 返回后，Orchestrator 在执行引擎调用前对 Tier 2 引擎执行：

```python
from kzocr.security.egress import validate_url
validate_url(engine.config.get("base_url", ""))  # 若失败抛 EgressBlockedError
```

**手动引擎覆盖**（解决 B15/PM 3.2）：调度器支持跳过自动逻辑的覆写：

```bash
kzocr pipeline --engine sensenova                 # 强制使用指定引擎
kzocr pipeline --prefer speed                      # 偏好速度（在 PASS 引擎中选延迟最低）
kzocr pipeline --prefer accuracy                   # 偏好准确率（基于 glyph_pass_rate 排序）
kzocr pipeline --tier-order "1,3,2"               # 自定义 tier 顺序
kzocr pipeline --tier-limit 2                      # 限制最大兜底层数
kzocr pipeline --max-time-per-page 120            # 单页最大耗时上限
```

CLI 层解析后覆盖调度器行为：

```python
def select_candidates(registry, tier, page, budget, overrides=None):
    if overrides and overrides.pinned_engine:
        return [registry.get(overrides.pinned_engine)]
    if overrides and overrides.prefer == "speed":
        candidates.sort(key=lambda e: e.stats.avg_latency_per_page_ms)
    elif overrides and overrides.prefer == "accuracy":
        candidates.sort(key=lambda e: e.stats.glyph_pass_rate, reverse=True)
    # ... 默认贝叶斯排序
```

---

## 5. E3: GlyphVerifier（字形验证器）

**文件：** `kzocr/scheduler/verifier.py`（新增）

### 5.1 Detector 协议（解决「阻塞 B2/评审架构3.1」）

采用插件式检测器架构，每项检测实现为独立 Detector，可独立 enable/disable 和测试：

```python
@dataclass
class GlyphVerdict:
    status: GlyphStatus    # PASS / RARE / UNKNOWN / FAIL / UNCERTAIN
    confidence: float
    details: str | None    # 结构化格式：`key=val;key=val`

@dataclass
class VerifierContext:
    page_num: int
    neighbor_texts: list[str]    # 前后邻居页文本（供 D4 使用）
    book_code: str
    # 可选字段
    page_layout: PageLayout | None = None

class Detector(Protocol):
    """验证检测器协议。返回 None 表示"无意见"。"""
    name: str
    enabled: bool = True
    
    def check(self, text: str, context: VerifierContext) -> GlyphVerdict | None: ...

class GlyphVerifier:
    def __init__(self, detectors: list[Detector]):
        self.detectors = [d for d in detectors if d.enabled]
    
    def verify(self, text: str, context: VerifierContext) -> GlyphVerdict:
        """强规则短路模式：遇到 PASS/FAIL 立即返回。"""
        for detector in self.detectors:
            verdict = detector.check(text, context)
            if verdict is not None and verdict.status in (GlyphStatus.PASS, GlyphStatus.FAIL):
                return verdict
        return GlyphVerdict(status=GlyphStatus.UNKNOWN, confidence=0.5, details="no_detector_matched")
    
    def estimate(self, text: str, context: VerifierContext) -> GlyphVerdict:
        """非短路模式：所有检测器投票，取加权结果。"""
        ...
```

### 5.2 预注册检测器列表

| 检测器 | 来源 | 标记状态 | 短路权 | 说明 |
|--------|------|---------|--------|------|
| `CharCountSpikeDetector` | D4 | UNCERTAIN | 无 | 字符数 > 邻页中位数 × 3 |
| `LeakageDetector` | C1 | FAIL | 高 | 跨页泄漏（引擎后处理后残余检测） |
| `ConfusionSetDetector` | B5 | UNKNOWN | 无 | 命中 `confusion_set.json` 形似混淆 |
| `TermKBMatcher` | rare_allowlist | PASS/RARE | 高 | 知识库术语匹配 → PASS（避 RARE 过保守） |
| `ToxinDoseDetector` | toxic_herbs.json | FAIL/CRITICAL | 最高 | 药名+剂量超安全上限 |

**检测器优先级表：**
1. `ToxinDoseDetector`（FAIL/CRITICAL 最高优先级，触及安全立即终止）
2. `LeakageDetector`（FAIL 高优先）
3. `TermKBMatcher`（PASS 高优先，确认正确）
4. `CharCountSpikeDetector`（UNCERTAIN 中优先）
5. `ConfusionSetDetector`（UNKNOWN 低优先）

### 5.3 ToxinDoseDetector（解决「领域 P1 toxic 剂量」）

```python
class ToxinDoseDetector:
    """检测 OCR 结果中的药名+剂量组合是否超出安全上限。"""
    
    def __init__(self, toxic_db: dict[str, HerbEntry]):
        # toxic_herbs.json 加载为 {herb_name: {max_dosage_g: ..., ...}}
        self.toxic_db = toxic_db
    
    def check(self, text: str, context: VerifierContext) -> GlyphVerdict | None:
        """匹配 pattern: (药名) + (数字)g"""
        for herb, info in self.toxic_db.items():
            pattern = re.compile(rf"{herb}\s*(\d+)\s*g")
            for match in pattern.finditer(text):
                dosage = int(match.group(1))
                if dosage > info["max_dosage_g"]:
                    return GlyphVerdict(
                        status=GlyphStatus.FAIL,
                        confidence=1.0,
                        details=f"toxin_dose;herb={herb};dosage={dosage}g;max={info['max_dosage_g']}g;severity=critical",
                    )
        return None  # 未命中
```

### 5.4 竖排检测（在 Scheduler 层，不在 Verifier — 解决「领域 P0 竖排盲区」）

竖排布局检测由页面渲染阶段的布局分析执行（已有基线特征），在 Scheduler 层作为「跳过 Tier 1」的依据（见 4.3），不作为 GlyphVerifier 的检测器。理由：
- 竖排是调度决策，不是字形验证
- 在调度层面跳过 T1 避免了无效调用，优于在验证阶段事后标记

### 5.5 验证器性能预算

- 单次 `verify()` 调用预算：**< 50ms**
- 知识库在 GlyphVerifier 初始化时一次性加载到内存（`@lru_cache` 或 `__init__` 中加载）
- 术语 KB 用哈希集 / Trie 实现，禁止逐条正则匹配（适配当前 < 5000 条的规模）
- `details` 使用结构化格式：`rule=char_count_spike,value=5000;rule=herb_match,name=黄芪`

### 5.6 人工校对反馈闭环（解决「领域 P1 反馈闭环」）

GlyphVerifier 记录所有 UNKNOWN/FAIL 的原文和上下文，输出为 `review_manifest`，为后续知识库积累提供结构化数据：

```python
@dataclass
class ReviewManifest:
    """人工校对清单。每本书一个。"""
    book_code: str
    pages: list[ReviewPageItem]

@dataclass
class ReviewPageItem:
    page_num: int
    priority: Literal["P0", "P1", "P2"]  # P0=FAIL, P1=UNKNOWN, P2=RARE
    engine_results: dict[str, str]        # 每级引擎的产出
    crop_img_path: str | None
    issues: list[ReviewIssue]

@dataclass
class ReviewIssue:
    position: int
    ocr_char: str
    expected: str | None
    issue_type: Literal["glyph", "dosage", "herb", "layout"]
    severity: Literal["critical", "warning", "info"]
```

定义 `feedback_apply()` 函数将人工修正记录中的新知识反向同步到 `variant_map` 和 `confusion_set`：
- 确认的 glyph 纠错 → 追加到 `confusion_set.json`
- 确认的稀有术语 → 追加到 `rare_allowlist.json`
- 修正后的剂量值 → 验证 `toxic_herbs.json` 的 `max_dosage_g` 是否需要更新

---

## 6. E4: Orchestrator（编排主循环）

**文件：** `kzocr/scheduler/orchestrator.py`（新增）

### 6.1 EngineRunner 分派（解决「阻塞 B1」）

```python
def _run_book_engine(engine: EngineRegistration, pdf_path: str) -> BookResult:
    """执行书级引擎，返回全书结果。"""
    return engine.adapter.run_book(pdf_path)

def _run_page_engine(engine: EngineRegistration, page_input: PageInput) -> AdapterPageResult:
    """执行页级引擎。"""
    return engine.adapter.run_page(page_input)
```

### 6.2 主循环（含双闸 + 串行默认 + trace）

```python
def orchestrate_book(
    pdf_path: str, book_code: str | None, config,
    overrides: EngineOverrides | None = None,
) -> BookResult:
    registry = probe_engines(config)        # E1
    budget = Budget(config)                 # 时间/页数预算
    verifier = GlyphVerifier(config)        # E3 初始化所有 Detector
    scheduler = EngineScheduler(config)     # E2
    trace = []                              # 全书 trace
    
    # B6 双闸：页面总数截断
    max_pages = budget.max_pages
    total_timeout = budget.total_timeout_ms / 1000.0
    start_time = time.monotonic()
    
    pages_text: list[str] = []
    failed_pages: dict[int, str] = {}
    uncertain_pages: dict[int, GlyphVerdict] = {}
    engine_usage_counter: dict[str, int] = {}
    
    # render_pages 必须为生成器（流式），禁止全量物化
    for page_num, page_input in enumerate(render_pages(pdf_path, config)):
        if page_num >= max_pages:           # ← 页数闸
            logger.warning("[orchestrator] page_limit=%d reached, truncating", max_pages)
            break
        elapsed = time.monotonic() - start_time
        if elapsed > total_timeout:         # ← 时间闸（每页检查）
            logger.warning("[orchestrator] total_timeout=%.0fs reached at page=%d", total_timeout, page_num)
            break
        
        page_trace = PageTrace(page=page_num)
        verdict = GlyphVerdict(status=GlyphStatus.FAIL, confidence=0)
        page_layout = page_input.layout  # 含 is_vertical/has_table
        
        # ── Tier 1: 书级引擎（全书只执行一次） ──
        if page_num == 0:
            tier1_engines = scheduler.select_candidates(
                registry, tier=1, page=page_input, budget=budget,
                page_layout=page_layout, overrides=overrides,
            )
            if tier1_engines:
                t0 = time.monotonic()
                try:
                    book_result = _run_book_engine(tier1_engines[0], pdf_path)
                except Exception as exc:
                    logger.error("[orchestrator] Tier 1 book engine failed: %s", exc)
                    book_result = None
                t1_elapsed = time.monotonic() - t0
                if book_result:
                    # 将书级结果按页拆入 pages_text
                    tier1_pages = list(book_result.pages)
                else:
                    tier1_pages = [None] * max_pages
        
        # Tier 1 逐页验证
        if tier1_pages and page_num < len(tier1_pages) and tier1_pages[page_num] is not None:
            page_text = tier1_pages[page_num].text
            context = VerifierContext(page_num=page_num, neighbor_texts=_get_neighbors(page_num, tier1_pages))
            verdict = verifier.verify(page_text, context)
            page_trace.add_event(tier=1, engine=tier1_engines[0].meta.name,
                                 latency_ms=t1_elapsed, verdict=verdict.status)
            if verdict.status in (GlyphStatus.PASS, GlyphStatus.RARE):
                pages_text.append(page_text)
                registry.record(tier1_engines[0], success=True, glyph=verdict, latency_ms=t1_elapsed)
                engine_usage_counter[tier1_engines[0].meta.name] = \
                    engine_usage_counter.get(tier1_engines[0].meta.name, 0) + 1
                trace.append(page_trace)
                continue
        
        # ── Tier 2: 云端视觉 LLM（逐页降级） ──
        if verdict.status in (GlyphStatus.FAIL, GlyphStatus.UNKNOWN):
            if budget.check_time_budget(elapsed) and not budget.exhausted:
                engines = scheduler.select_candidates(
                    registry, tier=2, page=page_input, budget=budget,
                    page_layout=page_layout, overrides=overrides,
                )
                for engine in engines:
                    if budget.exhausted:
                        break
                    t0 = time.monotonic()
                    try:
                        # 云引擎调用前 B3 校验
                        validate_url(engine.config.get("base_url", ""))
                        result = _run_page_engine(engine, page_input.img)
                    except Exception as exc:
                        logger.error("[orchestrator] Tier2 engine=%s failed: %s", engine.meta.name, exc)
                        registry.record(engine, success=False, error=str(exc))
                        continue
                    t_elapsed = time.monotonic() - t0
                    page_trace.add_event(tier=2, engine=engine.meta.name,
                                         latency_ms=int(t_elapsed * 1000), verdict="")
                    context = VerifierContext(page_num=page_num, neighbor_texts=[])
                    verdict = verifier.verify(result.text, context)
                    registry.record(engine, success=(verdict.status in (GlyphStatus.PASS, GlyphStatus.RARE)),
                                    glyph=verdict, latency_ms=int(t_elapsed * 1000))
                    if verdict.status in (GlyphStatus.PASS, GlyphStatus.RARE):
                        pages_text.append(result.text)
                        break
                else:
                    # Tier 2 全部失败
                    pass
        
        # ── Tier 3: 本地中医 LLM ──
        if verdict.status in (GlyphStatus.FAIL, GlyphStatus.UNKNOWN):
            if budget.check_time_budget(elapsed):
                engines = scheduler.select_candidates(
                    registry, tier=3, page=page_input, budget=budget,
                    page_layout=page_layout, overrides=overrides,
                )
                for engine in engines:
                    if budget.exhausted:
                        break
                    t0 = time.monotonic()
                    try:
                        result = _run_page_engine(engine, page_input.img)
                    except Exception as exc:
                        registry.record(engine, success=False, error=str(exc))
                        continue
                    t_elapsed = time.monotonic() - t0
                    page_trace.add_event(tier=3, engine=engine.meta.name,
                                         latency_ms=int(t_elapsed * 1000), verdict="")
                    context = VerifierContext(page_num=page_num, neighbor_texts=[])
                    verdict = verifier.verify(result.text, context)
                    registry.record(engine, success=(verdict.status in (GlyphStatus.PASS, GlyphStatus.RARE)),
                                    glyph=verdict, latency_ms=int(t_elapsed * 1000))
                    if verdict.status in (GlyphStatus.PASS, GlyphStatus.RARE):
                        pages_text.append(result.text)
                        break
        
        # ── HumanGate ──
        if verdict.status in (GlyphStatus.FAIL, GlyphStatus.UNKNOWN):
            failed_pages[page_num] = f"All tiers failed. Last: {verdict.details}"
        elif verdict.status == GlyphStatus.UNCERTAIN:
            uncertain_pages[page_num] = verdict
            pages_text.append(page_text)  # 放行但标记
        else:
            pages_text.append(result.text)
        
        page_trace.verdict = verdict.status
        trace.append(page_trace)
    
    # 书完成后：批量持久化 benchmark
    registry.persist_benchmarks()
    
    # 输出 trace（可选）
    if config.trace_dir:
        _write_trace(config.trace_dir, book_code, trace)
    
    # 输出引擎报告日志
    _log_engine_report(book_code, pages_text, failed_pages, uncertain_pages,
                       engine_usage_counter, time.monotonic() - start_time)
    
    return BookResult(
        pages=[...],   # pages_text → list[PageResult] 转换
        failed_pages=failed_pages,
        uncertain_pages=uncertain_pages,
        engine_trace=trace,
    )
```

### 6.3 B6 双闸实现

编排主循环继承当前 `_run_vlm`（`run.py:585`）的两道保护：

| 保护 | 检查时机 | 位置 | 默认值 |
|------|---------|------|--------|
| **页数上限** (`MAX_PAGES`) | `for page` 循环入口 | 循环顶部，渲染前截断 | 50 页 |
| **总时间预算** (`TOTAL_TIMEOUT`) | `for page` 循环入口，每页 | 每页 start 后立即检查 | 7200s |

`Budget` 对象提供：

```python
@dataclass
class Budget:
    max_pages: int                           # KZOCR_MAX_PAGES
    max_wall_clock_ms: int                   # KZOCR_TOTAL_TIMEOUT * 1000
    max_tokens: int = 0                      # token 预算（可选）
    max_time_per_page_ms: int = 120000       # 单页最大耗时（默认 120s）
    allow_cloud_vision: bool = False         # allow_cloud_vision
    
    def check_page_limit(self, page_num: int) -> bool:
        return page_num < self.max_pages
    
    def check_time_budget(self, elapsed_s: float) -> bool:
        return elapsed_s * 1000 < self.max_wall_clock_ms
    
    _exhausted: bool = False          # 由编排循环双闸设置

    def exhaust(self) -> None:
        self._exhausted = True

    @property
    def exhausted(self) -> bool:
        return self._exhausted
```

### 6.4 并行/串行策略（解决「R1 / 并行串行矛盾」）

| 场景 | 默认 | opt-in |
|------|------|--------|
| Tier 1 (CPU 密集 OCR) | **串行**（max_concurrency=1） | GPU 环境 + `KZOCR_ENGINE_PARALLEL=1` |
| Tier 2 (云端 API) | 串行（max_concurrency=1） | 天然 I/O 密集，无需并行 |
| Tier 3 (本地 LLM) | **串行** | 不推荐并行（CPU 推理极慢） |

`KZOCR_ENGINE_PARALLEL=1` 仅在 `ProbeResult.gpu=True` 时生效，且需显式配置。并行仅限不同 GPU 设备（`CUDA_VISIBLE_DEVICES=0,1`）。

### 6.5 trace + 引擎报告（解决「运维 A2/B2」）

**`BookResult` 扩展：**

```python
@dataclass
class EngineCallRecord:
    page: int
    tier: int
    engine: str
    latency_ms: int
    glyph_status: str   # "PASS" / "FAIL" / "UNKNOWN" / ...

@dataclass
class BookResult:
    pages: list[PageResult]
    failed_pages: dict[int, str]
    uncertain_pages: dict[int, GlyphVerdict]
    engine_trace: list[EngineCallRecord]   # 新增：全书引擎调用序列
```

**`KZOCR_TRACE_DIR`（可选）：** 每本书完成后将 trace JSON 写入 `{trace_dir}/{book_code}_{timestamp}.json`。

**引擎报告日志（书完成时输出）：**

```
[orchestrator] === 引擎报告 ===
[orchestrator] 书籍: mifangqiuzhen-970
[orchestrator] 总页数: 48 | 失败: 0 | UNCERTAIN: 2
[orchestrator] 引擎使用分布:
[orchestrator]   Tier1 mineru      → 32 页 (PASS)
[orchestrator]   Tier1 paddleocr  → 12 页 (PASS)
[orchestrator]   Tier2 sensenova  → 4 页 (PASS)
[orchestrator] 字形通过率: 46/48 (95.8%)
[orchestrator] 总耗时: 4m32s
[orchestrator] ============
```

---

## 7. E5: 现有关联文件修改

| 文件 | 变更 |
|------|------|
| `kzocr/engine/types.py` | `AdapterMeta` 扩展（tier, batch_capable, probe）；定义 `EngineRunner` 协议；`ProbeResult.keys` 改为 `dict[str, bool]`；`BookResult` 增加 `uncertain_pages`、`engine_trace` |
| `kzocr/engine/run.py` | `run_engine()` 内部委派 `orchestrate_book()`；保留旧配置兼容（`use_mock`/`use_vlm` → 映射为调度器配置）；提取共享 PDF 渲染逻辑 |
| `kzocr/config.py` | 新增 `SchedulerConfig` 嵌套 dataclass；`KZOCR_BENCHMARK_DIR`, `KZOCR_TRACE_DIR`, `KZOCR_ENGINE_PARALLEL`, `KZOCR_MAX_TIER1_ENGINES` 等 |
| `kzocr/engines/errors.py` | 新增 `SchedulerError(OcrError)`、`AllEnginesFailedError(RuntimeError)`、`EgressBlockedError` |
| `kzocr/cli.py` | 新增 `kzocr benchmark` 子命令（status/history/run/reset）；`kzocr pipeline` 增加 `--engine`、`--prefer`、`--tier-order`、`--tier-limit` 参数 |

### 7.1 Benchmark 持久化（NDJSON 追加式 — 解决「阻塞 B6」）

**目录：** `$KZOCR_OUTPUT_DIR/benchmarks/`（非 `docs/reviews/`）

**格式：** NDJSON（每行一个 JSON 事件，追加式写入）

```ndjson
{"ts": 1690000000.123, "engine": "paddleocr", "page": 1, "latency_ms": 4500, "glyph_status": "PASS", "tier": 1}
{"ts": 1690000005.456, "engine": "paddleocr", "page": 2, "latency_ms": 4200, "glyph_status": "PASS", "tier": 1}
{"ts": 1690000010.789, "engine": "sensenova", "page": 3, "latency_ms": 12300, "glyph_status": "FAIL", "tier": 2}
```

**关键设计：**
- 每行独立 JSON，行级追加（O(1) 写），禁止 JSON 全文覆写（防 O(n²) I/O 退化）
- 进程内 `EngineStats` 实时更新内存，每本书完成后批量 flush
- 进程启动时从 benchmark 目录加载重建 `EngineStats`
- 写入时复用 `kzocr/engines/atomic.py` 的原子写入

### 7.2 `run_engine()` 迁移策略（委派模式 — 解决「测试缺口 2」）

```python
def run_engine(pdf_path, book_code, config) -> BookResult:
    # mock 模式保持短路（PM 建议 E）
    if config.use_mock:
        return build_mock_book(...)
    # vlm 模式映射为禁用 Tier 1
    if config.use_vlm:
        config_overrides = SchedulerConfig(disabled_tiers=[1])
        return orchestrate_book(pdf_path, book_code, config, config_overrides)
    # 默认走调度器
    return orchestrate_book(pdf_path, book_code, config)
```

### 7.3 Config 新增字段

```python
@dataclass
class SchedulerConfig:
    max_tier1_engines: int = 2          # KZOCR_MAX_TIER1_ENGINES
    max_tier2_engines: int = 1
    max_tier3_engines: int = 1
    max_pages: int = 50                 # KZOCR_MAX_PAGES
    total_timeout_s: int = 7200         # KZOCR_TOTAL_TIMEOUT
    max_time_per_page_ms: int = 120000  # KZOCR_MAX_TIME_PER_PAGE_MS
    benchmark_dir: str = ""             # KZOCR_BENCHMARK_DIR → 默认 $KZOCR_OUTPUT_DIR/benchmarks/
    trace_dir: str = ""                 # KZOCR_TRACE_DIR
    engine_parallel: bool = False       # KZOCR_ENGINE_PARALLEL（仅 GPU 生效）
    allow_cloud_vision: bool = False    # KZOCR_ALLOW_CLOUD_VISION
    tier_limit: int = 3                 # KZOCR_TIER_LIMIT
```

---

## 8. 实施顺序

### Phase 1: 注册中心 + 数据模型（基础准备）

| 步骤 | 内容 | 预计耗时 |
|------|------|---------|
| 1.1 | `AdapterMeta` 扩展（tier, batch_capable, probe）；`EngineRunner` 协议定义 | 1 天 |
| 1.2 | `EngineRegistration` / `EngineStats` 数据类实现（含冷启动默认值 + 贝叶斯平滑） | 1 天 |
| 1.3 | `probe_engines()` 基础实现（复用 ProbeResult + 惰性探测） | 1 天 |
| 1.4 | `Config` 新增 `SchedulerConfig` + 环境变量映射 | 0.5 天 |
| 1.5 | benchmark NDJSON 持久化（save/load/flush） | 1 天 |
| 1.6 | 新增异常类（`SchedulerError`, `AllEnginesFailedError`） | 0.5 天 |
| 1.7 | 测试：`test_registry.py`（数据类构造 + 探测 + 持久化 roundtrip） | 1 天 |
| 1.8 | `conftest.py` 共享 fixture（9 引擎 mock 环境） | 0.5 天 |

**交付标准：** `test_registry.py` 全部通过；benchmark 能够从空状态→写入→读出→追加。

### Phase 2: 调度器 + 验证器（核心逻辑）

| 步骤 | 内容 | 预计耗时 |
|------|------|---------|
| 2.1 | `EngineScheduler.select_candidates()` 实现（层级约束 → allow_cloud_vision → 资源过滤 → 预算检查 → 贝叶斯排序 → 衰减 → Top-N） | 1.5 天 |
| 2.2 | 领域感知权重（竖排/出版时代/表格/冷启动降权） | 0.5 天 |
| 2.3 | 测试：`test_scheduler.py`（确定性排序 + 冷启动 + 竖排过滤 + 衰减 + tier 约束） | 1 天 |
| 2.4 | `Detector` 协议 + `GlyphVerifier` 实现 | 1 天 |
| 2.5 | 预注册检测器：`CharCountSpikeDetector`、`LeakageDetector`、`ConfusionSetDetector`、`TermKBMatcher`、`ToxinDoseDetector`（各独立类） | 2 天 |
| 2.6 | 测试：`test_verifier.py`（每条检测器独立测试 + 短路逻辑 + 优先级） | 1.5 天 |

**交付标准：** 调度器在给定 EngineStats fixture 下排序结果可预测；所有检测器独立可测；`test_scheduler.py` + `test_verifier.py` 全部通过。

### Phase 3: 编排主循环 + 集成

| 步骤 | 内容 | 预计耗时 |
|------|------|---------|
| 3.1 | `orchestrate_book()` 主循环实现（两级流水线 + B6 双闸 + try/except + HumanGate） | 2 天 |
| 3.2 | D3 VLM 缓存集成（缓存优先于调度器，不计 benchmark） | 0.5 天 |
| 3.3 | `run_engine()` 委派模式改造 + 旧配置兼容层 | 0.5 天 |
| 3.4 | CLI 扩展（`--engine`, `--prefer`, `--tier-order`, `kzocr benchmark`） | 1 天 |
| 3.5 | trace + 引擎报告日志输出 | 0.5 天 |
| 3.6 | 集成测试：`test_orchestrator_integration.py`（全链路 8 种兜底路径参数化）+ `test_regression.py`（确保 260+ 现有测试通过） | 2 天 |
| 3.7 | B3 egress 校验接入 + allow_cloud_vision 调度器检查 | 0.5 天 |
| 3.8 | 人工校对反馈闭环（review_manifest + feedback_apply） | 1 天 |

**交付标准：** `test_orchestrator_integration.py` 全 8 条路径通过；现有 260+ 测试零失败；`kzocr pipeline` 所有模式正常工作。

---

## 9. 与现有设计的关系

| 已有设计 | 在 v0.7 中的角色 | 变更 |
|---------|---------------|------|
| B1 `glyph_status`/`glyph_verified` | 字形验证器的输出载体 | 无变更 |
| B2 `adapter_to_line_result()` | 引擎结果 → 归一化 `LineResult` | 无变更 |
| B3 egress allowlist | Tier 2 云引擎的安全约束 | **新增**：Orchestrator 层显式调用 `validate_url()` |
| B5 `confusion_set.json` | `ConfusionSetDetector` 的数据源 | 无变更 |
| C1 leakage detection | `LeakageDetector`（GlyphVerifier 的一项检测器，对引擎后处理的残余泄漏做验证） | **重定位**：从独立后处理变为 Verifier 检测器 |
| C2 atomic write | 结果写入的保护 | 复用，增加 benchmark NDJSON 原子写入 |
| C3 rate limiter | 云引擎调用限速 | **增强**：每服务独立 `AdaptiveRateLimiter` |
| D1 errors/retry | 每引擎调用的重试策略 | 注意嵌套重试叠加时间预算 |
| D3 VLM cache | 编排层缓存优先（缓存命中不计 benchmark） | **集成点**：编排循环在调度器前检查缓存 |
| D4 hierarchy anomaly | `CharCountSpikeDetector`（GlyphVerifier 的一项检测器） | **重定位**：从独立后处理变为 Verifier 检测器 |
| `run_engine()` | 委派给 `orchestrate_book()` | **委派模式**：保留入口签名，内部路由 |
| `_run_vlm()` | PDF 渲染/裁剪/后处理/跨页合并 → 提取到编排层共享函数 | **提取**：共享逻辑移入编排层，`_run_vlm` 最终废弃 |
| `_init_vlm_adapter()` | 分散到各引擎注册初始化 | **移除**：硬编码降级链移入调度器 |
| `_build_engine_config()` | 分解为逐引擎配置构建函数 | **重构**：逐引擎 config 独立构造 |
| bookmark cache | 引擎级缓存，编排层黑盒不管理 | 无变更 |
| `human_final` / `ProofreadRecord` | 人工校对反馈闭环的输入 | **新增**：`review_manifest` + `feedback_apply()` |
| `toxic_herbs.json` | `ToxinDoseDetector` 的数据源 | **新增**：首次接入 GlyphVerifier |
| `rare_allowlist.json` | `TermKBMatcher` 将命中项标记为 PASS（改进前为 RARE） | **增强**：RARE → PASS 减少不必要 T2/T3 调用 |
| `variant_map.json` | `ConfusionSetDetector` 辅助 | 无变更 |

---

## 10. 测试策略

### 10.1 新增测试文件

| 测试文件 | 覆盖范围 | 最低用例数 |
|----------|---------|-----------|
| `tests/test_registry.py` | 数据类构造、probe 探测（全部/部分/零可用）、benchmark save/load/append、状态转换、去重 | ≥ 8 |
| `tests/test_scheduler.py` | 确定性排序、冷启动默认值、竖排跳过 T1、衰减因子、tier 约束、allow_cloud_vision 过滤、空注册表、预算耗尽 | ≥ 8 |
| `tests/test_verifier.py` | 每条 Detector 独立测试、短路逻辑、优先级顺序、空知识库、全部 Detector disable、`details` 结构化 | ≥ 10 |
| `tests/test_orchestrator.py` | 单元测试：单引擎调用、错误处理、`registry.record()` 调用验证 | ≥ 6 |
| `tests/test_orchestrator_integration.py` | 集成测试：8 种兜底路径参数化（T1 PASS → T1 FAIL+T2 PASS → 全部 FAIL → HumanGate → RARE → UNKNOWN → 预算耗尽 → 空注册表） | ≥ 8 |
| `tests/test_regression.py` | `run_engine()` 委派后旧行为不变验证：`use_mock`、`use_vlm`、`require_real` 各模式下路由 | ≥ 5 |

### 10.2 回归策略

1. `run_engine()` 使用**委派模式**（见 7.2），保留入口签名不变
2. 现有 mock `_run_real`/`_run_vlm` 的测试在 Phase 1-2 期间不受影响
3. Phase 3 完成后，在 `test_regression.py` 中新增路由兼容测试
4. CI 验证：`pytest tests/` 在每阶段的 commit 后全量通过

### 10.3 共享 fixture（`tests/conftest.py`）

```python
@pytest.fixture
def mock_all_engines_available():
    """patch 所有 9 引擎的探测条件，返回全部可用的 EngineRegistration 列表"""

@pytest.fixture
def mock_only_tier1_engines():
    """只有本地 OCR 可用，VLM/LLM 全部不可用"""

@pytest.fixture
def sample_engine_stats():
    """构造已知排序的 EngineStats fixture（不走 record 方法）"""
```
