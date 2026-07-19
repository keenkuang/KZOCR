# v0.7 自适应 OCR 引擎编排层 — 详细设计文档

> 本文档基于已批准的 `ocr-engine-unification.v0.7.md` 规划方案及两轮 8 角色评审报告编写。
> 为实施提供足够细度的指导：包含完整数据类定义、伪代码、API 签名、配置映射表和测试用例枚举。
> 评审报告归档：`docs/reviews/2026-07-10-round1-v0.7/`, `docs/reviews/2026-07-10-round2-v0.7/`
>
> **实现状态（2026-07-19）**：本设计稿已全部实现并合入 `main`（当前 **v0.20.0**）。`kzocr/scheduler/{registry,scheduler,verifier,orchestrator,review_manifest}.py` 均已落地，`run_engine` 已委派给编排层，§5.5 Box-Guided VL 仲裁与 §5.6 review_manifest 闭环均已接通。文中若仍出现「待实现 / TODO」字样均已过期，以 `kzocr/scheduler/` 实际代码为准。

---

## 目录

1. [数据类定义](#1-数据类定义)
2. [EngineRegistration 冷启动策略](#2-engineregistration-冷启动策略)
3. [API key 安全设计](#3-api-key-安全设计)
4. [调度器设计](#4-调度器设计)
5. [Detector 协议和 GlyphVerifier](#5-detector-协议和-glyphverifier)
6. [EngineRunner 协议](#6-enginerunner-协议)
7. [编排主循环伪代码](#7-编排主循环伪代码)
8. [Benchmark NDJSON 格式](#8-benchmark-ndjson-格式)
9. [Config 新增字段](#9-config-新增字段)
10. [CLI 扩展](#10-cli-扩展)
11. [测试策略](#11-测试策略)
12. [迁移策略](#12-迁移策略)

---

## 1. 数据类定义

### 1.1 AdapterMeta 扩展

**文件：** `kzocr/engine/types.py`

```python
from dataclasses import dataclass, field
from typing import Literal

AdapterKind = Literal["page", "book"]
EngineStatus = Literal["HEALTHY", "DEGRADED", "UNAVAILABLE"]
GlyphStatus = Literal["PASS", "RARE", "UNKNOWN", "FAIL", "UNCERTAIN"]


@dataclass
class AdapterMeta:
    """引擎适配器元信息。v0.7 扩展：tier, batch_capable, probe。"""
    name: str                                    # 引擎内部名（如 "sensenova"）
    label: str                                   # 用户可见名（如 "SenseNova VLM"）
    kind: AdapterKind = "page"                   # page / book
    tier: int = 1                                # Tier 归属 (1/2/3)
    batch_capable: bool = False                  # 是否支持书级输入（BookPipeline 模式）
    supports_confidence: bool = True
    supports_context: bool = False
    min_vram_gb: float = 0.0
    default_enabled: bool = True
    requires_gpu: bool = False
    requires_network: bool = False
    probe: dict = field(default_factory=lambda: {
        "method": "env",                         # env / port / file / api
        "key": "SENSENOVA_API_KEY",              # 探测目标
    })
```

**设计要点：**
- `tier` 不在 `EngineRegistration` 中而在 `AdapterMeta` 中，因为 tier 是引擎的固有属性而非运行时状态
- `probe` 字典描述了逐引擎的探测方式，供 `probe_engines()` 使用
- `batch_capable` 区分书级引擎（BookPipeline）和页级引擎（VLM/LLM），决定编排层的调用方式

### 1.2 EngineRegistration

**文件：** `kzocr/scheduler/registry.py`（新增）

```python
from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass
class EngineRegistration:
    """引擎注册项。每个引擎一个实例，由 EngineRegistry 管理。"""
    meta: AdapterMeta                            # 元信息（含 tier, probe 等）
    config: EngineConfig = field(default_factory=EngineConfig)  # 类型约束，防明文凭证
    status: EngineStatus = "HEALTHY"
    stats: "EngineStats" = field(default_factory=lambda: EngineStats())
    adapter: Callable | None = None              # EngineRunner 实例引用

    def __repr__(self) -> str:
        """掩码敏感字段，防止 API key 在日志中泄露。"""
        return (
            f"EngineRegistration(name={self.meta.name}, "
            f"tier={self.meta.tier}, status={self.status}, "
            f"stats=calls={self.stats.total_calls})"
        )
```

**设计要点：**
- `config` 用 `EngineConfig` 类型约束（安全 R1，防裸 dict 误放明文 key），只存环境变量名引用（如 `{"api_key_env": "SENSENOVA_API_KEY", "base_url": "https://..."}`），不存明文 —— 见第 3 节 + §9.1
- `adapter` 字段引用 `EngineRunner` 实例，供 Orchestrator 调用
- `__repr__` 掩码敏感字段，防止日志泄露

### 1.3 EngineStats

```python
import time
import math

# ── 全局常量 ──
GLYPH_PASS_RATE_DEFAULT = 0.5     # 冷启动默认 glyph 通过率（中等置信度假设，非 0）
AVG_LATENCY_DEFAULT_MS = 10000    # 冷启动默认延迟（10s 保守估计，非 0）
BAYESIAN_C = 7                     # 贝叶斯平均常数
BAYESIAN_PRIOR = 0.7               # 全局先验通过率
HALF_LIFE_DAYS = 7                 # 衰减半衰期（天）


@dataclass
class EngineStats:
    """引擎运行统计。只存原始累加值，派生值在访问时实时计算。"""
    # ── 原始累加值（只增不减） ──
    total_calls: int = 0            # 总调用次数
    total_latency_ms: int = 0       # 总延迟累加（毫秒）
    total_pages: int = 0            # 总处理页数
    glyph_pass_count: int = 0       # 字形验证通过次数
    glyph_fail_count: int = 0       # 字形验证失败次数
    glyph_unknown_count: int = 0    # 字形验证未知次数（新增，匹配 GlyphStatus.UNKNOWN）
    last_error: str | None = None   # 最近一次错误信息（简要）
    last_seen: float = 0.0          # time.time() — 挂钟时间，支持跨进程持久化

    # ── 派生属性（访问时实时计算） ──
    @property
    def avg_latency_per_page_ms(self) -> float:
        """平均每页延迟（毫秒）。无数据时返回默认值。"""
        if self.total_pages == 0:
            return AVG_LATENCY_DEFAULT_MS
        return self.total_latency_ms / self.total_pages

    @property
    def glyph_pass_rate(self) -> float:
        """字形通过率的贝叶斯平均（Bayesian Average）。
        公式：(pass_count + prior * C) / (total_glyph_decisions + C)
        其中 total_glyph_decisions = pass_count + fail_count + unknown_count
        C = 7, prior = 0.7
        """
        total = self.glyph_pass_count + self.glyph_fail_count + self.glyph_unknown_count
        if total == 0:
            return GLYPH_PASS_RATE_DEFAULT
        return (self.glyph_pass_count + BAYESIAN_PRIOR * BAYESIAN_C) / (total + BAYESIAN_C)

    @property
    def avg_latency_per_call_ms(self) -> float:
        """平均每次调用延迟（毫秒）。"""
        if self.total_calls == 0:
            return AVG_LATENCY_DEFAULT_MS
        return self.total_latency_ms / self.total_calls

    def decay(self, half_life_days: float = 7.0) -> float:
        """时效衰减因子（§4.2）。last_seen 越久衰减越强；未探测过返回 1.0。"""
        if self.last_seen == 0.0:
            return 1.0
        elapsed_days = (time.time() - self.last_seen) / 86400.0
        return 0.5 ** (elapsed_days / half_life_days)
```

**设计要点：**
- 只存原始累加值，派生值在访问时计算 —— 避免更新顺序导致的不一致（评审 SWENG S1）
- `last_seen` 使用 `time.time()` 而非 `time.monotonic()`，支持跨进程持久化（评审架构 B3）
- 贝叶斯平均公式：`(pass_count + prior * C) / (total + C)`，C=7, prior=0.7（评审架构 B4）
  - C=7 意味着引擎需要 7 页以上历史数据才能显著影响评分
  - 无数据时退化到 `prior`（0.7），确保新引擎有被选中机会
- `decay()` 使用标准半衰期衰减 `0.5 ** (elapsed_days / half_life_days)`，7 天后衰减到 0.5（实现见 §2 `EngineStats.decay`；原稿曾设想 `exp(-(now-last_seen)/HALF_LIFE)` 指数形式，实现时改为半衰期形式）
  - 轮询采样**参与**衰减：轮询选中的引擎经 `record()` 正常更新 `last_seen`，评分随之提升（探索预期效果；原"不参与衰减"声明已在 §2.3 修订）

### 1.4 EngineRegistry

```python
from collections import OrderedDict


class EngineRegistry:
    """引擎注册中心。管理所有引擎的注册、探测、统计记录和 benchmark 持久化。

    单线程设计：当前所有变更注册表状态的方法（register / mark_unavailable /
    mark_healthy / record）均只在 orchestrator 主线程顺序调用——页码顺序处理，
    并发 ThreadPoolExecutor 仅执行 engine.adapter.run_page，从不触碰本注册表；
    跨书并发走独立 Celery 进程，各自持有独立内存注册表，无共享内存竞争。
    因此当前实现不加线程锁。若将来启用并行页码处理（设计稿曾预留的 opt-in），
    则需在以上方法上引入锁以保证线程安全。
    """

    def __init__(self):
        self._engines: dict[str, EngineRegistration] = OrderedDict()

    def register(self, engine: EngineRegistration) -> None:
        """注册一个引擎。同名引擎后注册覆盖先注册（日志警告）。"""
        if engine.meta.name in self._engines:
            _logger.warning("[registry] engine=%s already registered, overwriting",
                            engine.meta.name)
        self._engines[engine.meta.name] = engine

    def get(self, name: str) -> EngineRegistration | None:
        """按名称获取注册项。"""
        return self._engines.get(name)

    def list_by_tier(self, tier: int, include_unavailable: bool = False) -> list[EngineRegistration]:
        """获取指定 tier 的所有引擎（默认排除 UNAVAILABLE）。"""
        return [e for e in self._engines.values()
                if e.meta.tier == tier and (include_unavailable or e.status != "UNAVAILABLE")]

    def list(self) -> list[EngineRegistration]:
        """获取所有注册引擎。"""
        return list(self._engines.values())

    def mark_unavailable(self, name: str, reason: str = "") -> None:
        """将某引擎标记为 UNAVAILABLE。调度器看到此状态后会跳过该引擎。"""
        engine = self._engines.get(name)
        if engine:
            engine.status = "UNAVAILABLE"
            engine.stats.last_error = reason
            _logger.warning("[registry] engine=%s marked UNAVAILABLE: %s", name, reason)

    def mark_healthy(self, name: str) -> None:
        """恢复引擎为 HEALTHY（如 probe 周期检测到恢复）。"""
        engine = self._engines.get(name)
        if engine:
            engine.status = "HEALTHY"

    def record(self, name: str, success: bool,
               glyph: Optional[GlyphStatus] = None, latency_ms: Optional[float] = None,
               pages: int = 1, error: Optional[str] = None) -> None:
        """记录一次引擎调用结果，更新统计（RARE/UNCERTAIN 不再被静默丢弃，
        分别计入独立计数——领域评审指出中医古籍异体字/古方名占比高，漏计会系统性
        低估通过率。注：E3 落地 `GlyphVerdict` 后，此处应接受其 `.status` 字段）。"""
        reg = self._regs.get(name)
        if reg is None:
            raise SchedulerError(f"未注册的引擎: {name}")
        s = reg.stats
        s.total_calls += 1
        s.total_pages += pages
        if latency_ms is not None:
            s.total_latency_ms += int(latency_ms)
        if glyph is not None:
            if glyph == "PASS":
                s.glyph_pass_count += 1
            elif glyph == "FAIL":
                s.glyph_fail_count += 1
            elif glyph == "UNKNOWN":
                s.glyph_unknown_count += 1
            elif glyph == "RARE":
                s.glyph_rare_count += 1
            elif glyph == "UNCERTAIN":
                s.glyph_uncertain_count += 1
        if not success:
            s.last_error = error
        s.last_seen = time.time()
        if self.benchmark_dir is not None:
            self._pending.append({...})  # NDJSON 增量事件

    # ── Benchmark 持久化（见第 8 节） ──
    def persist_benchmarks(self) -> None:
        """将内存中的增量事件以 NDJSON 逐行追加写入 benchmark 目录。
        每本书完成后调用一次（批量 flush）。无 benchmark_dir 时为空操作。"""
        ...

    def load_benchmarks(self) -> None:
        """进程启动时从 benchmark 目录加载 NDJSON，重建已注册引擎的 EngineStats。
        实例方法，直接作用于当前 registry 的已注册引擎。目录不存在时静默跳过。"""
        ...
```

### 1.5 Budget

```python
@dataclass
class Budget:
    """资源预算。由编排主循环管理，调度器只读查询。"""
    max_pages: int                              # KZOCR_MAX_PAGES
    max_wall_clock_ms: int                      # KZOCR_TOTAL_TIMEOUT * 1000
    max_tokens: int = 0                         # token 预算（可选）
    max_time_per_page_ms: int = 120000          # 单页最大耗时（默认 120s）
    allow_cloud_vision: bool = False            # 是否允许云端引擎
    _exhausted: bool = False                    # 内部：是否已耗尽（由编排循环设置）

    def exhaust(self) -> None:
        """标记预算已耗尽。由编排循环在双闸触发时调用。"""
        self._exhausted = True

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    def check_time_budget(self, elapsed_s: float) -> bool:
        return elapsed_s * 1000 < self.max_wall_clock_ms
```

**设计要点：**
- `_exhausted` 由编排循环中的双闸设置（`budget.exhaust()`），使 Tier 2/3 内部的 `if budget.exhausted` 检查真正生效
- 修复评审性能 N1/架构 N4 指出的 `exhausted` 恒为 False 问题

### 1.6 其他辅助类型

```python
from dataclasses import dataclass, field
from typing import Generator


@dataclass
class PageInput:
    """引擎输入：渲染后的单页数据。"""
    page_num: int
    img: "np.ndarray"                     # 渲染后的页图像（numpy 数组）
    layout: "PageLayout | None" = None   # 版式分析结果
    context: str | None = None            # 上页的底部 15% 文本（跨页上下文）


@dataclass
class PageLayout:
    """页面版式信息。由渲染阶段的光学布局分析得出。"""
    orientation: str = "horizontal"       # horizontal / vertical
    is_vertical: bool = False              # 是否为竖排版
    has_table: bool = False                # 是否含表格


@dataclass
class EngineOverrides:
    """CLI 传入的调度器覆盖参数。"""
    pinned_engine: str | None = None      # --engine <name>
    prefer: str | None = None             # "speed" / "accuracy"
    tier_order: list[int] | None = None   # --tier-order "1,3,2"
    tier_limit: int | None = None         # --tier-limit N
    max_time_per_page: int | None = None  # --max-time-per-page N


def render_pages(pdf_path: str, config) -> Generator[PageInput, None, None]:
    """流式生成器，逐页 yield PageInput。禁止全量物化。
    从 _run_vlm() 中提取 PDF → PageInput 转换逻辑。
    """
    ...
```

---

## 2. EngineRegistration 冷启动策略

### 2.1 初始值

```python
GLYPH_PASS_RATE_DEFAULT = 0.5     # 中等置信度假设，非 0
AVG_LATENCY_DEFAULT_MS = 10000    # 10s 保守估计，非 0
```

`EngineStats` 首次运行（`total_calls == 0`）时，派生属性 `glyph_pass_rate` 和 `avg_latency_per_page_ms` 返回以上默认值。

### 2.2 前 3 次调用按预设优先级排序

在引擎历史数据积累之前（`total_calls < 3`），调度器按 Tier 内预设优先级排序：

**全局 preset priority 顺序（Tier 无关，在全集内定序）：**

```
sensenova > paddleocr > rapidocr > mineru > unirec > paddleocr_vl16 > shizhengpt
```

即：sensenova 优先级最高，shizhengpt 最低。sensenova 能力上限最高放在首位合理；paddleocr_vl16 因实测通过率 89.2%、延迟 18.7s/页且有 temp=0 死循环风险，冷启动期不应排在前列，故调至 unirec 之后。

```python
PRESET_PRIORITY = [
    "sensenova", "paddleocr", "rapidocr",
    "mineru", "unirec", "paddleocr_vl16", "shizhengpt",
]

def _preset_sort_key(engine: EngineRegistration) -> int:
    try:
        return PRESET_PRIORITY.index(engine.meta.name)
    except ValueError:
        return len(PRESET_PRIORITY)  # 未在预设列表中的引擎排在最后
```

### 2.3 轮询采样（探索 vs 利用）

在 `select_candidates()` 中，以 5% 概率从注册表中随机选择一个低分引擎作为候选（即使其排名不在 Top-N 内）：

```python
import random

def _should_poll() -> bool:
    """5% 概率触发轮询采样。"""
    return random.random() < 0.05

def _select_poll_candidate(candidates: list[EngineRegistration],
                            top_n: list[EngineRegistration]) -> EngineRegistration | None:
    """从同 tier、未入选 Top-N 且可用的候选中随机挑一个做探索性采样。"""
    rest = [e for e in candidates if e not in top_n and e.status != "UNAVAILABLE"]
    if not rest:
        return None
    return random.choice(rest)
```

**轮询采样参与衰减（更新 `last_seen`）：** 轮询选中的引擎经 `registry.record()` 正常更新 `last_seen`，其时效衰减因子随之变新鲜、评分被适度抬升——这恰是探索的预期效果：让久未入选的引擎重新获得被选中机会。原设计曾设想"轮询数据不参与衰减"，评审（round3，architect 报告 C1）确认改为**承认轮询参与衰减**，以强化探索、避免冷启动陷阱。

**轮询可突破 `tier_limits` 上限（有意设计）：** 轮询候选在 Top-N 截断（§4.1 第 8 步）之后追加，故单页候选数可能短暂超过 `tier_limits`。该上限约束的是"利用"阶段的主选集，"突破"的是"探索"阶段的额外采样，属有意设计。此策略（探索强度 vs 成本，如每页是否允许多跑一个引擎）后续可能按运行数据修订。

---

## 3. API key 安全设计

### 3.1 核心原则

**`EngineRegistration.config` 绝不存储 API key 明文。** 使用 `EngineConfig` 类型约束，存环境变量名引用：

```python
# ❌ 禁止：
config = EngineConfig(api_key_env="sk-xxxxxx", base_url="https://...")
# 或更坏：config = {"api_key": "sk-xxxxxx", ...}

# ✅ 允许：
config = EngineConfig(
    api_key_env="SENSENOVA_API_KEY",      # ❌ 不，这仍是错误的——api_key_env 是环境变量名
    base_url="https://api.sensenova.com/v1",
)
# 正确的理解：api_key_env 存放 *环境变量名*（如 "SENSENOVA_API_KEY"），
# 运行时从 os.environ["SENSENOVA_API_KEY"] 读取实际值
```

### 3.2 运行时读取

引擎调用时从 `os.environ` 读取：

```python
def _resolve_config(registration: EngineRegistration) -> dict:
    """将 EngineConfig 中的环境变量引用解析为实际值。
    返回包含实际 api_key 的 dict（仅运行时使用，不存回 config）。"""
    cfg = registration.config  # type: EngineConfig
    api_key = os.environ.get(cfg.api_key_env, "")
    if not api_key:
        raise ConfigError(
            f"API key environment variable '{cfg.api_key_env}' not set "
            f"for engine '{registration.meta.name}'"
        )
    return {
        "api_key": api_key,
        "base_url": cfg.base_url,
        **cfg.extra,
    }
```

### 3.3 配套改动

- **`ProbeResult.keys`** 由 `dict[str, str]` 改为 `dict[str, bool]`（仅存 key 是否存在，不存值）
  ```python
  @dataclass
  class ProbeResult:
      gpu: bool = False
      vram_gb: float = 0.0
      ports: dict[str, bool] = field(default_factory=dict)
      keys: dict[str, bool] = field(default_factory=dict)   # ← 改为 bool
      allow_cloud_vision: bool = False
  ```
- **`_compute_config_hash()`** 移除 API key 作为 hash 输入（换 key 不换 model 不应导致缓存失效）
- **`EngineStats` 和 `EngineRegistration`** 的 `__str__`/`__repr__` 掩码敏感字段（已在 1.2 节实现）
- **Benchmark NDJSON** 不写入任何凭证信息（`config` 不参与序列化）

### 3.4 完整性保护

benchmark NDJSON 文件权限默认 `700`（仅 owner 可读写）。可选：写入时附加 HMAC 签名，调度器读取时验证。

---

## 4. 调度器设计

### 4.1 `select_candidates()` 完整签名与执行顺序

```python
from dataclasses import dataclass, field


class EngineScheduler:
    """调度器主类。"""

    def select_candidates(
        self,
        registry: EngineRegistry,
        tier: int,
        page_info: PageInfo,
        budget: Budget,
        page_layout: PageLayout | None = None,
        overrides: EngineOverrides | None = None,
    ) -> list[EngineRegistration]:
        """选择指定 tier 的候选引擎列表。

    执行顺序：
    1. 引擎覆盖检查（overrides.pinned_engine）
    2. 层级约束
    3. 竖排检测（跳过 Tier 1）
    4. allow_cloud_vision 过滤（跳过云端引擎）
    5. 资源过滤（状态位缓存）
    6. 预算检查
    7. 加权排序（贝叶斯评分 × 衰减因子 × domain_adjust）
    8. 取 Top-N
    9. 5% 概率轮询采样
    """
    # ── 第 1 步：覆盖检查 ──
    if overrides and overrides.pinned_engine:
        engine = registry.get(overrides.pinned_engine)
        if engine is None:
            raise PinnedEngineUnavailableError(
                f"Pinned engine '{overrides.pinned_engine}' not found in registry"
            )
        if engine.status == "UNAVAILABLE":
            raise PinnedEngineUnavailableError(
                f"Pinned engine '{overrides.pinned_engine}' is UNAVAILABLE"
            )
        return [engine]

    # ── 第 2 步：层级约束 ──
    candidates = registry.list_by_tier(tier)
    if not candidates:
        _logger.info("[scheduler] tier=%d: no candidates registered", tier)
        return []

    # ── 第 3 步：竖排检测 → 跳过 Tier 1 ──
    if page_layout and page_layout.is_vertical and tier == 1:
        _logger.info("[scheduler] page vertical layout detected, skipping Tier 1")
        return []

    # ── 第 4 步：allow_cloud_vision 过滤 ──
    if not budget.allow_cloud_vision:
        before = len(candidates)
        candidates = [e for e in candidates if not e.meta.requires_network]
        if len(candidates) < before:
            _logger.info("[scheduler] filtered %d cloud engines (allow_cloud_vision=False)",
                         before - len(candidates))

    # ── 第 5 步：资源过滤（只读状态缓存，不做实时探测） ──
    candidates = [e for e in candidates if e.status != "UNAVAILABLE"]

    # ── 第 6 步：预算检查（粗略；精细预算在编排循环逐引擎做） ──
    if budget.exhausted:
        return []

    # ── 第 7 步：加权排序 ──
    def _score(engine: EngineRegistration) -> float:
        now = time.time()
        pass_rate = engine.stats.glyph_pass_rate
        latency = engine.stats.avg_latency_per_page_ms
        decay = engine.stats.decay(HALF_LIFE_DAYS)
        base_score = pass_rate * (1000.0 / max(latency, 1.0)) * decay
        return domain_adjust(base_score, engine, page_info, page_layout)

    # -- prefer 覆盖 --
    if overrides and overrides.prefer == "speed":
        candidates.sort(key=lambda e: e.stats.avg_latency_per_page_ms)
    elif overrides and overrides.prefer == "accuracy":
        candidates.sort(key=lambda e: e.stats.glyph_pass_rate, reverse=True)
    else:
        candidates.sort(key=_score, reverse=True)

    # ── 第 8 步：取 Top-N ──
    max_engines = _get_max_engines_for_tier(tier)  # 从配置读取
    top_n = candidates[:max_engines]

    # ── 第 9 步：5% 轮询采样（可突破 tier_limits 上限，探索性追加） ──
    if _should_poll():
        poll_candidate = _select_poll_candidate(candidates, top_n)
        if poll_candidate:
            _logger.info("[scheduler] polling low-score engine: %s", poll_candidate.meta.name)
            top_n.append(poll_candidate)

    return top_n
```

### 4.2 权重公式

```python
def _compute_bayesian_score(engine: EngineRegistration,
                            half_life_days: float = HALF_LIFE_DAYS) -> float:
    """完整权重公式。

    score = bayesian_pass_rate * (1000 / max(avg_latency_ms, 1)) * decay

    其中：
    - bayesian_pass_rate: (pass_count + prior * C) / (total + C)  [C=7, prior=0.7]
    - avg_latency_ms: 平均延迟，下限 1ms（防除零）
    - decay(): 0.5 ** (elapsed_days / half_life_days)  [half_life_days=7天，7天后衰减到0.5]
    """
    pass_rate = engine.stats.glyph_pass_rate   # 已包含贝叶斯平均
    latency = max(engine.stats.avg_latency_per_page_ms, 1.0)
    decay = engine.stats.decay(half_life_days)
    return pass_rate * (1000.0 / latency) * decay
```

### 4.3 `domain_adjust()` 领域感知调整

```python
@dataclass
class PageInfo:
    """页面信息（调度器输入）。"""
    page_num: int
    book_type: str = ""           # "tcm_ancient" / "tcm_modern" / ...
    pub_era: str = ""             # "lead_print" / "transition" / "laser"
    is_vertical: bool = False
    has_table: bool = False


def domain_adjust(
    base_score: float,
    engine: EngineRegistration,
    page_info: PageInfo,
    page_layout: PageLayout | None = None,
) -> float:
    """领域感知权重调整。

    调整规则：
    - 竖排页：Tier 2/3 引擎 +0.2 偏移（不使用乘法降权 T1，因为 T1 已被跳过）
    - pub_era=laser（激光照排）：快速引擎 +0.1
    - book_type=formula（方剂书）：高召回引擎 +0.1
    """
    adjustments = 0.0
    tier = engine.meta.tier

    # 竖排页：Tier 2/3 混合模式偏移（base_score * 1.5 + 0.2）
    # 乘法因子 1.5 确保高精度引擎在竖排古籍场景不因高延迟被低精度低延迟引擎排挤
    # 加法偏移 0.2 作为保底，覆盖无历史数据的新引擎
    if page_layout and page_layout.is_vertical and tier >= 2:
        return base_score * 1.5 + 0.2 + adjustments

    # 激光照排：快速引擎（延迟低于 5000ms）获得正向偏移
    if page_info.pub_era == "laser" and engine.stats.avg_latency_per_page_ms < 5000:
        adjustments += 0.1

    # 方剂书：高召回引擎（glyph_pass_rate > 0.9）获得正向偏移
    if page_info.book_type == "formula" and engine.stats.glyph_pass_rate > 0.9:
        adjustments += 0.1

    return base_score + adjustments
```

**设计要点（基于领域评审反馈）：**
- 竖排页跳过 T1（在 `select_candidates()` 中直接 return []），所以竖排规则给 T2/T3 `base_score * 1.5 + 0.2` 混合模式偏移而非 T1 降权
- 出版时代感知（pub_era=laser→快速引擎提权）回应领域评审第 4 点
- 方剂书感知（book_type=formula→高召回提权）回应领域评审遗漏 C
- 不使用 `base_score * adjustments` 的纯乘法模式，改用加法+乘法混合模式（避免乘法使低分归零，加法提供保底）
- 竖排混合模式中乘法因子 1.5 确保高延迟高精度引擎（sensenova 12s/页）不因低延迟低精度引擎（2s/页）的 base_score 优势而被排挤；加法偏移 0.2 为新引擎提供保底
- 二级领域评审建议的"竖排/雕版场景优先级偏移"通过 `tier >= 2` 混合偏移实现

### 4.4 `max_tier_N_engines` 配置映射

```python
def _get_max_engines_for_tier(tier: int) -> int:
    """根据 tier 返回最大候选数。"""
    from kzocr.config import get_scheduler_config
    cfg = get_scheduler_config()
    if tier == 1:
        return cfg.max_tier1_engines
    elif tier == 2:
        return cfg.max_tier2_engines
    elif tier == 3:
        return cfg.max_tier3_engines
    return 1
```

默认值：Tier 1 = 2, Tier 2 = 1, Tier 3 = 1。

### 4.5 egress 校验

在 Orchestrator 层，Tier 2 引擎调用前执行 egress 校验：

```python
from kzocr.security.egress import validate_url

# Tier 2 循环内
try:
    validate_url(engine.config.get("base_url", ""))
except EgressBlockedError as exc:
    _logger.warning("[orchestrator] egress blocked for engine=%s: %s",
                    engine.meta.name, exc)
    registry.mark_unavailable(engine.meta.name, str(exc))
    continue  # 跳过该引擎，继续下一候选
```

**注意：导入路径为 `kzocr.security.egress`，非 `kzocr.engines.egress`（修复架构 N3）。**

---

## 5. Detector 协议和 GlyphVerifier

### 5.1 DetectorContext

```python
@dataclass
class DetectorContext:
    """检测器上下文。每页每引擎检测时传入。"""
    page_num: int
    book_type: str                  # 书籍类型（用于领域感知检测）
    pub_era: str                    # 出版时代
    engine_label: str               # 当前引擎标签（用于溯源）
    resources: dict = field(default_factory=dict)  # 资源字典（confusion_set, term_kb 等）
```

### 5.2 Detector Protocol

```python
class Detector(Protocol):
    """验证检测器协议。返回 None 表示"无意见"。"""
    name: str
    enabled: bool = True
    priority: int = 50              # 优先级（数字越小越优先检测）

    def check(self, text: str, context: DetectorContext) -> Optional[GlyphVerdict]:
        """执行检测。
        返回 GlyphVerdict 表示有意见，None 表示无意见（跳过）。
        """
        ...


@dataclass
class GlyphVerdict:
    """字形验证裁决。"""
    status: GlyphStatus
    confidence: float
    details: str | None = None      # 结构化格式：`key=val;key=val`
    detector_name: str = ""
```

### 5.3 7 个预注册检测器

| 优先级 | 检测器 | 来源 | 输出 | 说明 |
|-------|--------|------|------|------|
| 10 | `ToxinDoseDetector` | toxic_herbs.json | FAIL/CRITICAL | 药名+剂量超安全上限，触及安全立即终止 |
| 20 | `LeakageDetector` | C1 leakage.py | FAIL | 跨页泄漏（引擎后处理后残余检测） |
| 30 | `CharCountSpikeDetector` | D4 hierarchy.py | UNCERTAIN | 字符数 > 邻页中位数 × 3 |
| 40 | `ConfusionSetDetector` | B5 confusion_set.json | RARE + force_review | 命中形似混淆（non-blocking，采纳文本送复核） |
| 41 | `PhraseErrorDetector` | confusion_phrase.json | RARE + force_review | Layer2 词组错扫描（同音/语义错，标记 M6 待语义校验） |
| 45 | `ConfusionKeyPresenceDetector` | confusion_set.json | RARE + force_review | Layer1 前置静态筛查（一级高危字强制 M4，分侧强弱标） |
| 50 | `TermKBMatcher` | variant_map + term_kb | PASS/RARE | 知识库术语匹配 |

**设计要点：**
- 优先级数字越小越优先检测
- 优先级顺序已优化：安全（ToxinDose）→ 泄漏（Leakage）→ 异常（CharCountSpike）→ 混淆（ConfusionSet）→ 词组（PhraseError）→ 基准字前置筛查（ConfusionKeyPresence）→ 知识库（TermKBMatcher）
- 每个检测器独立 enable/disable，资源不存在时自动 disable 并 warn
- 形近字相关检测器（ConfusionSetDetector、ConfusionKeyPresenceDetector、PhraseErrorDetector）统一为 **non-blocking（RARE + force_review）**：文本照常采纳，同时打标送人工复核队列，不阻断主流程
- 对比原方案（v0.7 设计）：新增 ConfusionKeyPresenceDetector（Layer1 字符级前置筛查）和 PhraseErrorDetector（Layer2 词组级筛查），两者均在 `kzocr/resources/confusion_*.json` 上运行时加载

#### ToxinDoseDetector

```python
class ToxinDoseDetector:
    """检测 OCR 结果中的药名+剂量组合是否超出安全上限。"""
    name = "ToxinDoseDetector"
    priority = 10

    def __init__(self, toxic_db: dict[str, HerbEntry] | None = None):
        self.toxic_db = toxic_db or {}
        self._enabled = bool(toxic_db)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, text: str, context: DetectorContext) -> GlyphVerdict | None:
        """匹配 pattern: (药名) + (数字)(g/克/钱/两)"""
        import re
        for herb, info in self.toxic_db.items():
            # 使用 re.escape 防止药名含正则特殊字符（如 + / ( 等）
            pattern = re.compile(rf"{re.escape(herb)}\s*(\d+(?:\.\d+)?)\s*(g|克|钱|两)")
            for match in pattern.finditer(text):
                dosage = float(match.group(1))
                unit = match.group(2)
                if unit == "钱":
                    dosage *= 3.0  # 1钱 ≈ 3g
                elif unit == "两":
                    dosage *= 30.0  # 1两 ≈ 30g（汉制，后世沿用）
                if dosage > info["max_dosage_g"]:
                    return GlyphVerdict(
                        status="FAIL",
                        confidence=1.0,
                        details=(
                            f"toxin_dose;herb={herb};dosage={match.group(1)}{unit};"
                            f"max={info['max_dosage_g']}g;severity=critical"
                        ),
                        detector_name=self.name,
                    )
        return None
```

**设计要点（基于领域评审）：**
- 使用 `re.escape(herb)` 防止药名含正则特殊字符（修复测试评审 4.6）
- 支持 `g`、`克`、`钱`、`两` 四种单位，钱/两自动转换为克（领域第二轮建议，第三轮补充"两"）
- 浮点剂量支持（`\d+(?:\.\d+)?`)
- 正则 `附子汤` 不会匹配 `附子`（re.escape 只转义特殊字符，但 `附子汤` ≠ `附子` 需要药名后跟空格或边界，可加 `\b`）

#### LeakageDetector

```python
class LeakageDetector:
    """检测引擎后处理后的残余跨页泄漏。"""
    name = "LeakageDetector"
    priority = 20

    def __init__(self):
        self._enabled = True

    @property
    def enabled(self) -> bool:
        # 在引擎后处理阶段 C1 已执行截断后，此检测器只检测残余泄漏
        return self._enabled

    def check(self, text: str, context: DetectorContext) -> GlyphVerdict | None:
        # 复用现有 apply_leakage_defense() 的泄漏检测逻辑
        # 返回 FAIL 如果检测到残余泄漏
        # 具体实现参考 kzocr/engines/leakage.py
        ...
```

#### CharCountSpikeDetector

```python
class CharCountSpikeDetector:
    """检测字符数尖峰（D4 层级异常）。"""
    name = "CharCountSpikeDetector"
    priority = 30

    def __init__(self, multiplier: float = 3.0):
        self.multiplier = multiplier
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, text: str, context: DetectorContext) -> GlyphVerdict | None:
        # 需要邻居页文本用于比较
        # 具体复用 check_hierarchy_anomaly() 逻辑
        ...
```

#### ConfusionSetDetector

```python
class ConfusionSetDetector:
    """检测命中 confusion_set.json 的形似混淆字。"""
    name = "ConfusionSetDetector"
    priority = 40

    def __init__(self, confusion_set: dict | None = None):
        self.confusion_set = confusion_set or {}
        self._enabled = bool(confusion_set)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, text: str, context: DetectorContext) -> GlyphVerdict | None:
        # 扫描文本中是否包含 confusion_set 中的错误字形
        ...
```

#### TermKBMatcher

```python
class TermKBMatcher:
    """匹配知识库术语（variant_map + term_kb），命中 PASS/RARE。"""
    name = "TermKBMatcher"
    priority = 50

    def __init__(self, rare_allowlist: set | None = None,
                 variant_map: dict | None = None):
        self.rare_allowlist = rare_allowlist or set()
        self.variant_map = variant_map or {}
        self._enabled = bool(rare_allowlist) or bool(variant_map)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def check(self, text: str, context: DetectorContext) -> GlyphVerdict | None:
        # 匹配 rare_allowlist 中的术语→PASS
        # 匹配 variant_map 中的变体→RARE
        ...
```

### 5.4 GlyphVerifier

```python
class GlyphVerifier:
    """字形验证器。管理检测器链，按优先级执行，支持短路模式。"""

    def __init__(self, detectors: list[Detector] | None = None):
        self.detectors = sorted(
            [d for d in (detectors or []) if d.enabled],
            key=lambda d: d.priority,
        )

    def verify(self, text: str, context: DetectorContext) -> GlyphVerdict:
        """强规则短路模式：遇到 PASS/FAIL 立即返回。

        短路规则：
        - ToxinDoseDetector 的 FAIL/CRITICAL → 立即返回
        - LeakageDetector 的 FAIL → 立即返回
        - TermKBMatcher 的 PASS → 立即返回
        - CharCountSpikeDetector 的 UNCERTAIN → 不短路，继续后续检测
        - ConfusionSetDetector 的 UNKNOWN → 不短路，继续后续检测

        所有检测器执行完毕后的聚合逻辑：
        - 全 PASS → glyph_status=PASS
        - 有 RARE → glyph_status=RARE
        - 有 UNKNOWN → 继续下一级（不在 verify 中处理）
        - 有 FAIL → 继续下一级（不在 verify 中处理）
        """
        has_rare = False
        has_unknown = False
        has_fail = False

        for detector in self.detectors:
            verdict = detector.check(text, context)
            if verdict is None:
                continue

            # 短路：PASS 或 FAIL 直接返回
            if verdict.status == "PASS":
                return verdict
            if verdict.status == "FAIL":
                # FAIL 不立即返回——继续检测以收集完整信息
                # 但 ToxinDoseDetector 的 FAIL（critical）立即返回
                if verdict.details and "severity=critical" in verdict.details:
                    return verdict
                has_fail = True

            # 非短路标记
            if verdict.status == "RARE":
                has_rare = True
            elif verdict.status == "UNKNOWN":
                has_unknown = True

        # 聚合逻辑
        if not has_fail and not has_unknown and not has_rare:
            return GlyphVerdict(status="PASS", confidence=1.0,
                                details="all_detectors_passed",
                                detector_name="GlyphVerifier")
        if has_rare and not has_fail and not has_unknown:
            return GlyphVerdict(status="RARE", confidence=0.8,
                                details="rare_terms_detected",
                                detector_name="GlyphVerifier")
        # FAIL/UNKNOWN 不在此处做最终裁决，由编排循环判定是否降级
        return GlyphVerdict(status="UNKNOWN", confidence=0.5,
                            details=f"has_fail={has_fail},has_unknown={has_unknown}",
                            detector_name="GlyphVerifier")
```

**设计要点：**
- 全 PASS → `glyph_status=PASS`
- 有 RARE（无 FAIL/UNKNOWN）→ `glyph_status=RARE`
- 有 UNKNOWN → 编排循环继续下一 Tier
- 有 FAIL → 编排循环继续下一 Tier
- ToxinDoseDetector 的 critical FAIL 立即短路

### 5.5 性能预算

- 单次 `verify()` 调用预算：**< 50ms**
- 知识库在 GlyphVerifier 初始化时一次性加载到内存（`@lru_cache` 或 `__init__` 中加载）
- 术语 KB 用哈希集 / Trie 实现，禁止逐条正则匹配（当前 < 5000 条规模）
- `details` 使用结构化格式：`key=val;key=val`

### 5.6 review_manifest

```python
@dataclass
class ReviewManifest:
    """人工校对清单。每本书一个。"""
    book_code: str
    pages: list["ReviewPageItem"]

@dataclass
class ReviewPageItem:
    page_num: int
    priority: Literal["P0", "P1", "P2"]     # P0=FAIL, P1=UNKNOWN, P2=RARE
    engine_results: dict[str, str]           # 每级引擎的产出
    crop_img_path: str | None = None
    issues: list["ReviewIssue"] = field(default_factory=list)

@dataclass
class ReviewIssue:
    position: int
    ocr_char: str
    expected: str | None = None
    issue_type: Literal["glyph", "dosage", "herb", "layout"] = "glyph"
    severity: Literal["critical", "warning", "info"] = "info"
```

---

## 6. EngineRunner 协议

### 6.1 协议定义

```python
from typing import Protocol


class EngineRunner(Protocol):
    """引擎统一执行接口。"""

    def run_page(self, page: PageInput) -> "AdapterPageResult":
        """页级执行：输入单页图像，返回归一化结果。
        所有 page-level 引擎（VLM/LLM 适配器）实现此方法。"""
        ...

    def run_book(self, pdf_path: str) -> "BookResult":
        """书级执行：输入 PDF 路径，返回全书结果。
        仅在 kind='book' 时支持，如 BookPipeline。"""
        ...


@dataclass
class PageInput:
    """引擎输入：渲染后的单页数据。"""
    page_num: int
    img: "np.ndarray"                     # 渲染后的页图像
    layout: "PageLayout | None" = None
    context: str | None = None             # 上页的底部 15% 文本（跨页上下文）
```

### 6.2 EngineCallRecord

```python
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class EngineCallRecord:
    """单次引擎调用的完整记录。用于 trace 和运维排障。"""
    page: int                                    # 页号
    tier: int                                    # 所在 tier (1/2/3)
    engine: str                                  # 引擎名
    latency_ms: int                              # 延迟（毫秒）
    glyph_status: str | None = None              # 字形验证状态
    error: str | None = None                     # 错误信息（已 sanitize，无凭证）
    status: str = "HEALTHY"                      # 调用前引擎状态
    detector_chain: list[str] = field(default_factory=list)  # 触发的 detector 列表
    ts: float = 0.0                              # 调用时间戳（time.time()）
    cache_hit: bool = False                      # 是否来自 VLM 缓存
    breakdown: dict[str, float] = field(default_factory=dict)  # 子阶段耗时分解（ms）
```

**设计要点：**
- `glyph_status` 为 None 表示未做验证（如 Tier 1 全书引擎的中间页仅计入统计，但不代表验证结果）
- `error` 字段在写入前需经过凭证过滤（sanitize），防止 API key 等敏感信息泄露到 trace 文件
- `breakdown` 示例：`{"render": 120, "engine": 3400, "verify": 15}`，用于运维排障性能瓶颈
- `detector_chain` 记录本次 verify 按序触发了哪些 detector（如 `["ToxinDoseDetector", "LeakageDetector", "TermKBMatcher"]`）

### 6.3 PageLayout

```python
@dataclass
class PageLayout:
    """页面版式信息。由渲染阶段的光学布局分析得出。"""
    orientation: str = "horizontal"         # horizontal / vertical
    is_vertical: bool = False               # 是否为竖排版（调度器竖排感知使用）
    has_table: bool = False                 # 是否含表格
```

### 6.4 引擎适配映射表

| 引擎名 | 协议实现 | 方式 | tier |
|--------|---------|------|------|
| mock | `run_book` | 直接返回 mock BookResult | 0 (special) |
| paddleocr | `run_book` | BookPipeline 包装器 | 1 |
| rapidocr | `run_book` | BookPipeline 包装器 | 1 |
| mineru | `run_book` | BookPipeline 包装器 | 1 |
| unirec | `run_book` | BookPipeline 包装器 | 1 |
| kimi_pipeline | `run_book` | BookPipeline 包装器 | 1 |
| sensenova | `run_page` | 现有 SenseNovaAdapter | 2 |
| paddleocr_vl16 | `run_page` | 现有 PaddleOCRVl16Adapter | 3 |
| shizhengpt | `run_page` | 现有 ShizhengptAdapter | 3 |

### 6.5 BookPipelineAdapter

```python
class BookPipelineAdapter:
    """BookPipeline 包装器，实现 EngineRunner 协议。"""

    def __init__(self, engine_name: str, pipeline):
        self.engine_name = engine_name
        self.pipeline = pipeline

    def run_book(self, pdf_path: str) -> "BookResult":
        """委托 BookPipeline 全书处理，返回 BookResult。"""
        result = self.pipeline.process_book(pdf_path, self.engine_name)
        return result

    def run_page(self, page: PageInput) -> "AdapterPageResult":
        """书级引擎不支持逐页调用。"""
        raise NotImplementedError(
            f"{self.engine_name} is a book-level engine, use run_book() instead"
        )
```

---

## 7. 编排主循环伪代码

### 7.1 `orchestrate_book()` 完整伪代码

```python
import time
import logging

_logger = logging.getLogger(__name__)


def orchestrate_book(
    pdf_path: str,
    book_code: str | None,
    config,
    overrides: EngineOverrides | None = None,
) -> BookResult:
    """全书编排主循环。

    流程：
    1. 初始化：probe_engines → Budget → GlyphVerifier → EngineScheduler
    2. 运行所有 book-level 引擎（当前只有 BookPipeline），取全书结果
    3. 逐页验证 Tier 1 结果
    4. 失败页 / 字形验证不通过的页，进入逐页 Tier 2 → Tier 3 降级
    5. 全部失败 → HumanGate
    """
    # ── 引擎并行有效性检查 ──
    if config.engine_parallel and not probe_result.has_gpu:
        _logger.warning("engine_parallel ignored: no GPU detected")
        config.engine_parallel = False

    # ── 初始化 ──
    registry = probe_engines(config)                # E1
    budget = Budget(
        max_pages=config.max_pages,
        max_wall_clock_ms=config.total_timeout_s * 1000,
        max_time_per_page_ms=config.max_time_per_page_ms,
        allow_cloud_vision=config.allow_cloud_vision,
    )
    verifier = GlyphVerifier(_init_detectors(config))  # E3
    scheduler = EngineScheduler(config)                # E2

    trace: list[EngineCallRecord] = []
    start_time = time.monotonic()

    pages_text: list[str] = []
    failed_pages: dict[int, str] = {}
    uncertain_pages: dict[int, GlyphVerdict] = {}
    engine_usage_counter: dict[str, int] = {}

    # ── 第 1 步：Tier 1 全书处理（只执行一次） ──
    tier1_candidates = _safe_select_candidates(
        scheduler, registry, tier=1, ...  # 获取 Tier 1 候选
    )
    tier1_result: BookResult | None = None
    if tier1_candidates:
        t0 = time.monotonic()
        try:
            # Tier 1 只取第一个候选引擎执行全书处理
            tier1_result = _run_book_engine(tier1_candidates[0], pdf_path)
        except Exception as exc:
            _logger.error("[orchestrator] Tier 1 book engine failed: %s", exc)
        finally:
            t1_elapsed = int((time.monotonic() - t0) * 1000)
            # ★全书延迟均摊到各页：避免每页 trace 记录全书耗时导致 avg_latency 失真
            t1_elapsed_per_page = (
                t1_elapsed // len(tier1_result.pages)
                if tier1_result and tier1_result.pages
                else t1_elapsed
            )
    else:
        t1_elapsed = 0

    # ── 第 2 步：逐页处理（render_pages 必须为流式生成器） ──
    for page_num, page_input in enumerate(render_pages(pdf_path, config)):
        # ── 进度日志（每 5 页输出，响应 PM Round 1 要求） ──
        if page_num % 5 == 0:
            elapsed_m = (time.monotonic() - start_time) / 60
            _logger.info(
                "[progress] book=%s page=%d/%d | elapsed=%dm | tier1=%s",
                book_code or "unknown", page_num + 1, budget.max_pages,
                int(elapsed_m),
                tier1_candidates[0].meta.name if tier1_candidates else "none",
            )
        # ── B6 双闸：页数闸 ──
        if page_num >= budget.max_pages:
            _logger.warning("[orchestrator] page_limit=%d reached, truncating", budget.max_pages)
            budget.exhaust()
            break

        # ── B6 双闸：时间闸（每页检查，统一使用 budget.check_time_budget） ──
        elapsed = time.monotonic() - start_time
        if not budget.check_time_budget(elapsed):
            _logger.warning("[orchestrator] total_timeout=%ds reached at page=%d",
                            config.total_timeout_s, page_num)
            budget.exhaust()
            break

        page_trace_records: list[EngineCallRecord] = []
        verdict = GlyphVerdict(status="FAIL", confidence=0.0)
        page_layout = page_input.layout or PageLayout()
        final_text = ""

        # ── Tier 1 结果验证（使用 book-level 引擎的预计算结果） ──
        if tier1_result and page_num < len(tier1_result.pages):
            page_text = tier1_result.pages[page_num].text
            context = DetectorContext(
                page_num=page_num,
                engine_label=tier1_candidates[0].meta.name if tier1_candidates else "",
                book_type=config.book_type or "",
                pub_era=config.pub_era or "",
            )
            verdict = verifier.verify(page_text, context)
            page_trace_records.append(EngineCallRecord(
                page=page_num, tier=1,
                engine=tier1_candidates[0].meta.name if tier1_candidates else "unknown",
                latency_ms=t1_elapsed_per_page,  # ★使用均摊后的每页延迟
                glyph_status=verdict.status,
            ))

            if verdict.status in ("PASS", "RARE"):
                final_text = page_text
                pages_text.append(final_text)
                _record_engine_usage(registry, tier1_candidates[0], verdict, t1_elapsed,
                                     engine_usage_counter)
                continue

        # ── Tier 2：云端视觉 LLM（逐页降级） ──
        if verdict.status in ("FAIL", "UNKNOWN", "UNCERTAIN") and not budget.exhausted:
            tier2_candidates = _safe_select_candidates(
                scheduler, registry, tier=2, page_info=_make_page_info(page_num, page_layout),
                budget=budget, page_layout=page_layout, overrides=overrides,
            )
            for engine in tier2_candidates:
                if budget.exhausted:
                    break
                t0 = time.monotonic()
                try:
                    # 云引擎调用前 B3 校验
                    validate_url(engine.config.get("base_url", ""))
                    # Tier 2 云端引擎也加超时保护（默认 300s，Tier 3 的 2 倍，云端较慢但不应挂死）
                    result = _run_single_engine_with_timeout(
                        engine, page_input,
                        timeout_s=config.max_time_per_page_ms // 1000 * 2,
                    )
                except EgressBlockedError as exc:
                    _logger.warning("egress blocked for %s: %s", engine.meta.name, exc)
                    registry.mark_unavailable(engine.meta.name, str(exc))
                    continue
                except TimeoutError as exc:
                    _logger.warning("Tier 2 engine=%s timed out: %s", engine.meta.name, exc)
                    continue
                except Exception as exc:
                    _logger.error("Tier 2 engine=%s failed: %s", engine.meta.name, exc)
                    registry.record(engine, success=False, error=str(exc))
                    continue
                t_elapsed = int((time.monotonic() - t0) * 1000)
                context = DetectorContext(page_num=page_num, engine_label=engine.meta.name, ...)
                verdict = verifier.verify(result.text, context)
                _record_engine_usage(registry, engine, verdict, t_elapsed,
                                     engine_usage_counter)
                if verdict.status in ("PASS", "RARE"):
                    final_text = result.text
                    pages_text.append(final_text)
                    break

        # ── Tier 3：本地中医 LLM ──
        if verdict.status in ("FAIL", "UNKNOWN", "UNCERTAIN") and not budget.exhausted:
            tier3_candidates = _safe_select_candidates(
                scheduler, registry, tier=3, ...)
            for engine in tier3_candidates:
                if budget.exhausted:
                    break
                t0 = time.monotonic()
                try:
                    # Tier 3 引擎超时保护（默认 120s）
                    result = _run_single_engine_with_timeout(
                        engine, page_input,
                        timeout_s=config.max_time_per_page_ms // 1000,
                    )
                except TimeoutError:
                    _logger.warning("Tier 3 engine=%s timed out", engine.meta.name)
                    continue
                except Exception as exc:
                    _logger.error("Tier 3 engine=%s failed: %s", engine.meta.name, exc)
                    registry.record(engine, success=False, error=str(exc))
                    continue
                t_elapsed = int((time.monotonic() - t0) * 1000)
                context = DetectorContext(page_num=page_num, engine_label=engine.meta.name, ...)
                verdict = verifier.verify(result.text, context)
                _record_engine_usage(registry, engine, verdict, t_elapsed,
                                     engine_usage_counter)
                if verdict.status in ("PASS", "RARE"):
                    final_text = result.text
                    pages_text.append(final_text)
                    break

        # ── HumanGate ──
        if verdict.status in ("FAIL", "UNKNOWN"):
            failed_pages[page_num] = f"All tiers failed. Last: {verdict.details}"
            _logger.warning("[orchestrator] page=%d all tiers failed: %s",
                            page_num, verdict.details)
        elif verdict.status == "UNCERTAIN":
            uncertain_pages[page_num] = verdict
            if final_text:
                pages_text.append(final_text)
        else:
            if final_text:
                pages_text.append(final_text)

        # 记录调度日志（默认输出，不依赖 trace_dir）
        _logger.info(
            "[scheduler] book=%s page=%d tier=1..3 verdict=%s engines=%s latency_ms=%d",
            book_code or "unknown", page_num, verdict.status,
            [r.engine for r in page_trace_records],
            sum(r.latency_ms for r in page_trace_records),
        )
        trace.extend(page_trace_records)

    # ── 书完成后处理 ──
    # 批量持久化 benchmark
    registry.persist_benchmarks()

    # 输出 trace 文件（默认启用，路径 $KZOCR_OUTPUT_DIR/trace/）
    # 可通过 KZOCR_TRACE_DIR 覆盖，设为空字符串禁用
    trace_dir = config.scheduler.trace_dir or os.path.join(config.output_dir, "trace")
    os.makedirs(trace_dir, exist_ok=True)
    _write_trace(trace_dir, book_code or f"book_{int(time.time())}", trace)

    # 输出引擎报告日志
    _log_engine_report(book_code, pages_text, failed_pages, uncertain_pages,
                       engine_usage_counter, time.monotonic() - start_time)

    # 失败率告警
    total = len(pages_text) + len(failed_pages) + len(uncertain_pages)
    failed_ratio = len(failed_pages) / max(total, 1)
    if failed_ratio > 0.3:
        _logger.error("[orchestrator] book=%s failed_ratio=%.2f exceeds CRITICAL threshold (30%%)",
                      book_code, failed_ratio)
    elif failed_ratio > 0.1:
        _logger.warning("[orchestrator] book=%s failed_ratio=%.2f exceeds threshold (10%%)",
                        book_code, failed_ratio)

    return BookResult(
        pages=_build_pages_result(pages_text, ...),   # 见 7.2 节
        failed_pages=failed_pages,
        uncertain_pages=uncertain_pages,
        engine_trace=trace,
    )
```

### 7.2 `pages_text → list[PageResult]` 转换

```python
def _build_pages_result(
    pages_text: list[str],
    tier1_result: BookResult | None,
    page_count: int,
) -> list[PageResult]:
    """将 pages_text 转换为 BookResult 所需的 list[PageResult]。
    如果 Tier 1 有结果且页被保留，尽量复用 Tier 1 的结构化数据。
    """
    results: list[PageResult] = []
    for i, text in enumerate(pages_text):
        if tier1_result and i < len(tier1_result.pages):
            # 复用 Tier 1 的 PageResult（含 LineResult 等），仅更新 text
            pr = tier1_result.pages[i]
            pr.text = text
            results.append(pr)
        else:
            # 从字符串构建基本 PageResult（无行级元信息）
            results.append(PageResult(text=text, lines=[]))
    return results
```

### 7.3 引擎调用超时包裹（Tier 2 + Tier 3）

```python
import concurrent.futures


def _run_single_engine_with_timeout(
    engine: EngineRegistration,
    page_input: PageInput,
    timeout_s: int = 120,
) -> "AdapterPageResult":
    """带超时的引擎调用。防止云端 VLM（Tier 2）或本地 LLM（Tier 3）挂死。
    Tier 2 调用时 timeout_s 传入 max_time_per_page_ms 的两倍（默认 240s）。
    
    注意：concurrent.futures 超时不会终止后台线程，
    挂死的线程会遗留为僵尸线程。v0.7 串行模式下数量可控（≤3），可接受。
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(engine.adapter.run_page, page_input)
        try:
            return future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"engine={engine.meta.name} timed out after {timeout_s}s"
            )
```

### 7.4 引擎报告日志格式

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

### 7.5 B6 双闸实现细则

| 保护 | 检查时机 | 位置 | 默认值 |
|------|---------|------|--------|
| 页数上限 (`MAX_PAGES`) | `for page` 循环入口 | 循环顶部，渲染前截断 | 50 页 |
| 总时间预算 (`TOTAL_TIMEOUT`) | `for page` 循环入口，每页 | 每页 start 后立即检查 | 7200s |
| 单页超时 (`MAX_TIME_PER_PAGE`) | Tier 2/3 引擎调用 | `_run_single_engine_with_timeout` | 120s (T3) / 240s (T2) |

### 7.6 D3 VLM 缓存集成

编排循环中，Tier 2/3 分派前检查 VLM 缓存（缓存命中不计 benchmark）：

```python
# Tier 2/3 循环内
cached_text = _load_vlm_cache(config, book_code or "", page_num)
if cached_text is not None:
    result = AdapterPageResult(text=cached_text)
    _logger.info("[orchestrator] page=%d cached VLM hit, skipping engine call", page_num)
else:
    result = _run_page_engine(engine, page_input)
    # 引擎执行后写入 VLM 缓存
    _save_vlm_cache(config, book_code or "", page_num, result.text)
```

---

## 8. Benchmark NDJSON 格式

### 8.1 文件路径

```
$KZOCR_OUTPUT_DIR/benchmarks/{engine_name}.ndjson
```

### 8.2 每行格式

每行一个 JSON 事件：

```json
{"ts": 1712345678.123, "engine": "sensenova", "page": 5, "book": "TCM-001",
 "tier": 2, "latency_ms": 12345, "glyph_status": "PASS", "error": null}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `ts` | float | 是 | `time.time()` 时间戳 |
| `engine` | str | 是 | 引擎名 |
| `page` | int | 是 | 页号 |
| `book` | str | 是 | 书籍代码 |
| `tier` | int | 是 | 所在 tier (1/2/3) |
| `latency_ms` | int | 是 | 延迟（毫秒） |
| `glyph_status` | str | 否 | 字形验证状态，null 表示未做验证 |
| `error` | str\|null | 否 | 错误信息，null 表示无错误 |

### 8.3 写入策略

- 追加式写入（行级追加，O(1) 写，禁止 JSON 全文覆写）
- 进程内 `EngineStats` 实时更新内存，每本书完成后批量 flush
- 追加写入使用 `fcntl.flock` 进程级文件锁保证并发安全（见 §8.5 实现）
- 不使用 `kzocr/engines/atomic.py` 的整文件原子写入（`atomic_write` 为覆写模式，与追加模式不兼容），改用带锁的逐行追加
- 每本书写入独立文件？不——按引擎名分区写入同一文件，避免文件数爆炸

### 8.4 启动加载

```python
def load_benchmarks(benchmark_dir: str, max_age_days: int = 90,
                    max_load_lines: int = 50000) -> dict[str, EngineStats]:
    """从 benchmark 目录加载重建 EngineStats。

    策略：
    - 只加载最近 max_age_days 内的数据
    - 每个引擎最多读取 max_load_lines 条（从文件尾部向前取最新 N 条）
    - 跳过损坏的行（日志警告，不崩溃）
    """
    cutoff = time.time() - max_age_days * 86400
    stats_map: dict[str, EngineStats] = {}

    for ndjson_path in Path(benchmark_dir).glob("*.ndjson"):
        engine_name = ndjson_path.stem
        # ★从文件尾部读取最新 N 行（NDJSON 追加写入，新数据在尾部）
        lines = _tail_lines(ndjson_path, max_load_lines)
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                _logger.warning("corrupt benchmark line in %s: %s", ndjson_path.name, exc)
                continue
            if event.get("ts", 0) < cutoff:
                continue
            # 累加统计
            if engine_name not in stats_map:
                stats_map[engine_name] = EngineStats()
            stats = stats_map[engine_name]
            stats.total_calls += 1
            if event.get("latency_ms"):
                stats.total_latency_ms += event["latency_ms"]
            glyph_status = event.get("glyph_status")
            if glyph_status == "PASS":
                stats.glyph_pass_count += 1
                stats.total_pages += 1        # ★修复：PASS 时累加 total_pages
            elif glyph_status == "FAIL":
                stats.glyph_fail_count += 1
            elif glyph_status in ("UNKNOWN", "UNCERTAIN"):
                stats.glyph_unknown_count += 1
            # last_seen 取最新事件的时间戳
            stats.last_seen = max(stats.last_seen, event["ts"])
            if event.get("error"):
                stats.last_error = _sanitize_error(event["error"])  # ★凭证过滤
    return stats_map


def _tail_lines(path: Path, n: int) -> list[str]:
    """从文件尾部读取最多 n 行，类似 tail -n 的语义。
    使用块读取 + 反向扫描避免全量加载。"""
    lines: list[str] = []
    block_size = 8192
    total_size = path.stat().st_size
    with open(path, "rb") as f:
        # 从文件末尾向前读块
        position = total_size
        while position > 0 and len(lines) < n:
            read_size = min(block_size, position)
            position -= read_size
            f.seek(position)
            chunk = f.read(read_size)
            # 按行分割，取后面的行
            chunk_lines = chunk.split(b"\n")
            if lines:
                # 不是第一块：块的最后一行与已有结果的第一行拼接
                chunk_lines[-1] = chunk_lines[-1] + lines[0].encode()
            for bline in reversed(chunk_lines):
                if bline:
                    decoded = bline.decode("utf-8", errors="replace")
                    lines.insert(0, decoded)
                    if len(lines) >= n:
                        break
    return lines[-n:]
```

### 8.5 容量管理

#### 截断策略

- 文件超 `benchmark_max_mb`（默认 100MB）时自动截断最老 50%
- 截断实现：
  1. 对文件加独占锁（`fcntl.flock`）
  2. 读取全部行到内存（100MB ≈ 50-67 万条）
  3. 保留最新 50% 行
  4. 写入临时文件（峰值磁盘：原文件 100MB + 临时文件 ~50MB → 1.5x 峰值）
  5. `os.replace(tmp_path, original_path)` 原子替换
  6. 释放锁
- 截断检查时机：每次 `persist_benchmarks()` 批量 flush 时检查单引擎文件大小
- 原子性保障：通过写临时文件 + `os.replace()` 实现，即使进程在步骤 4 崩溃，原文件仍完整

#### 并发安全

- **追加写入使用进程级文件锁**（`fcntl.flock`），防止多进程并行写入行交错：

```python
import fcntl
import os
import json

def _append_benchmark(engine_name: str, event: dict, benchmark_dir: str):
    """带进程级锁的 NDJSON 追加写入。"""
    path = Path(benchmark_dir) / f"{engine_name}.ndjson"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
            # 写入后检查文件大小，超限则截断
            if path.stat().st_size > _get_max_bytes():
                _truncate_benchmark(path)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
```

- `load_benchmarks()` 与 `persist_benchmarks()` 的读写冲突：load 时遇到不完整行 → `json.JSONDecodeError` → 跳过（已处理）；截断操作的独占锁保证读写不会同时发生在临界区
- **凭证过滤**：写入 NDJSON 前对所有 `error` 字段执行 `_sanitize_error()`，使用正则移除可能的凭证模式：
  ```python
  import re
  
  _CREDENTIAL_PATTERNS = [
      r'(api_key|token|secret|password)[=:]\s*\S+',
      r'(sk-[a-zA-Z0-9]{20,})',
  ]
  
  def _sanitize_error(msg: str) -> str:
      """移除错误消息中的凭证信息。"""
      result = msg[:200]  # 先截断
      for pattern in _CREDENTIAL_PATTERNS:
          result = re.sub(pattern, r'\1=***', result, flags=re.IGNORECASE)
      return result
  ```

#### 其他容量约束

- 启动时只读最近 90 天数据（`benchmark_retention_days`，仅加载时过滤，非磁盘清理策略）
- 每个引擎最多加载 50000 行
- 注意：`benchmark_retention_days` 仅为启动加载时的时间窗口过滤，磁盘数据需配合大小触发的截断机制清理。运维人员不应期望 90 天前的数据被自动删除。
- 文件权限默认 `0o700`（安全 R5），容器/备份场景可改为 `0o750`

### 8.6 数据格式演进

`load_benchmarks()` 使用 `dict.get(field, default)` 容错，支持向后兼容旧格式（缺少新字段时使用默认值）。

---

## 9. Config 新增字段

### 9.1 EngineConfig（新增类型）

```python
from dataclasses import dataclass, field


@dataclass
class EngineConfig:
    """引擎配置类型。用于 EngineRegistration.config，替代裸 dict。
    只存环境变量名引用，不存明文凭证。"""
    api_key_env: str = ""       # 环境变量名（如 "SENSENOVA_API_KEY"），运行时从 os.environ 读取
    base_url: str = ""          # 服务端点 URL
    extra: dict = field(default_factory=dict)  # 非敏感额外参数
```

**设计要点：**
- 使用 `@dataclass` 而非 `TypedDict`，因为 `dataclass` 支持默认值和 `field()` 选项
- 禁止添加 `api_key` 明文字段——仅在运行时从 `os.environ` 通过 `api_key_env` 读取
- 安全 R1 要求：通过类型约束防止开发者误放明文 key

### 9.2 SchedulerConfig

```python
@dataclass
class SchedulerConfig:
    """调度器配置。作为 Config 的嵌套 dataclass。"""
    # ── 引擎选择 ──
    max_tier1_engines: int = 2          # KZOCR_MAX_TIER1_ENGINES（默认 2，最大 3）
    max_tier2_engines: int = 1          # KZOCR_MAX_TIER2_ENGINES
    max_tier3_engines: int = 1          # KZOCR_MAX_TIER3_ENGINES
    engine_parallel: bool = False       # KZOCR_ENGINE_PARALLEL（仅 GPU 生效，v0.7 串行占位）
    disabled_tiers: list[int] = field(default_factory=list)  # KZOCR_DISABLED_TIERS（如 [1] 禁用 T1）

    # ── 预算 ──
    max_pages: int = 50                 # KZOCR_MAX_PAGES
    total_timeout_s: int = 7200         # KZOCR_TOTAL_TIMEOUT
    max_time_per_page_ms: int = 120000  # KZOCR_MAX_TIME_PER_PAGE_MS

    # ── 数据目录 ──
    benchmark_dir: str = ""             # KZOCR_BENCHMARK_DIR（默认 $KZOCR_OUTPUT_DIR/benchmarks/）
    trace_dir: str = ""                 # KZOCR_TRACE_DIR（默认 $KZOCR_OUTPUT_DIR/trace/，设为 "" 禁用）
    trace_retention_days: int = 7       # KZOCR_TRACE_RETENTION_DAYS

    # ── 安全 ──
    allow_cloud_vision: bool = False    # KZOCR_ALLOW_CLOUD_VISION

    # ── 兜底 ──
    tier_limit: int = 3                 # KZOCR_TIER_LIMIT
    fail_on_no_pass: bool = False       # KZOCR_FAIL_ON_NO_PASS（=1 时无人肉可读通过页抛异常）

    # ── Benchmark 容量 ──
    benchmark_retention_days: int = 90  # KZOCR_BENCHMARK_RETENTION_DAYS
    benchmark_max_mb: int = 100         # KZOCR_BENCHMARK_MAX_MB
```

### 9.3 环境变量 → Python 字段映射

| 环境变量 | Python 字段 | 类型 | 默认值 |
|---------|------------|------|--------|
| `KZOCR_OUTPUT_DIR` | `output_dir` | str | —（复用） |
| `KZOCR_MAX_TIER1_ENGINES` | `scheduler.max_tier1_engines` | int | 2 |
| `KZOCR_MAX_TIER2_ENGINES` | `scheduler.max_tier2_engines` | int | 1 |
| `KZOCR_MAX_TIER3_ENGINES` | `scheduler.max_tier3_engines` | int | 1 |
| `KZOCR_ENGINE_PARALLEL` | `scheduler.engine_parallel` | bool | False |
| `KZOCR_DISABLED_TIERS` | `scheduler.disabled_tiers` | list[int] | [] |
| `KZOCR_TRACE_DIR` | `scheduler.trace_dir` | str | `$KZOCR_OUTPUT_DIR/trace/` |
| `KZOCR_TRACE_RETENTION_DAYS` | `scheduler.trace_retention_days` | int | 7 |
| `KZOCR_BENCHMARK_DIR` | `scheduler.benchmark_dir` | str | `$KZOCR_OUTPUT_DIR/benchmarks/` |
| `KZOCR_BENCHMARK_RETENTION_DAYS` | `scheduler.benchmark_retention_days` | int | 90 |
| `KZOCR_BENCHMARK_MAX_MB` | `scheduler.benchmark_max_mb` | int | 100 |
| `KZOCR_ALLOW_CLOUD_VISION` | `scheduler.allow_cloud_vision` | bool | False |
| `KZOCR_TIER_LIMIT` | `scheduler.tier_limit` | int | 3 |
| `KZOCR_FAIL_ON_NO_PASS` | `scheduler.fail_on_no_pass` | bool | False |
| `KZOCR_MAX_TIME_PER_PAGE_MS` | `scheduler.max_time_per_page_ms` | int | 120000 |

### 9.4 Config 集成

```python
@dataclass
class Config:
    """现有 Config 扩展 scheduler 嵌套字段。"""
    # ... 原有字段 ...
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
```

---

## 10. CLI 扩展

### 10.1 pipeline 子命令新增参数

```bash
# 强制指定引擎（跳过调度器）
kzocr pipeline --engine sensenova

# 调度器优化偏好
kzocr pipeline --prefer speed         # 在 PASS 引擎中选延迟最低的
kzocr pipeline --prefer accuracy      # 基于 glyph_pass_rate 排序

# 自定义降级顺序
kzocr pipeline --tier-order "1,3,2"   # 跳过 Tier 2，直接 Tier 3

# 最大兜底级数
kzocr pipeline --tier-limit 2         # 最多尝试 2 级

# 单页最大时间（秒）
kzocr pipeline --max-time-per-page 120
```

### 10.2 benchmark 子命令

```bash
# 列出所有引擎历史摘要
kzocr benchmark list

# 查看指定引擎的详细历史
kzocr benchmark show sensenova
```

`kzocr benchmark list` 输出示例：

```
引擎             状态        glyph 通过率    平均延迟   最后调用
─────────────────────────────────────────────────────
paddleocr        HEALTHY    96.2%          4.2s/页    10s 前
rapidocr         HEALTHY    94.8%          3.1s/页    10s 前
mineru           HEALTHY    97.1%          5.5s/页    10s 前
sensenova        HEALTHY    98.5%          12.3s/页   2m 前
paddleocr_vl16   DEGRADED   89.2%          18.7s/页   5m 前
shizhengpt       UNAVAILABLE -             -          -
─────────────────────────────────────────────────────
```

### 10.3 EngineOverrides 构造

```python
def _parse_cli_overrides(args) -> EngineOverrides:
    overrides = EngineOverrides()
    if args.engine:
        overrides.pinned_engine = args.engine
    if args.prefer:
        overrides.prefer = args.prefer
    if args.tier_order:
        overrides.tier_order = [int(t.strip()) for t in args.tier_order.split(",")]
    if args.tier_limit is not None:
        overrides.tier_limit = args.tier_limit
    if args.max_time_per_page is not None:
        overrides.max_time_per_page = args.max_time_per_page
    return overrides
```

---

## 11. 测试策略

### 11.1 新增测试文件

| 测试文件 | 覆盖范围 | 最低用例数 |
|----------|---------|-----------|
| `tests/test_registry.py` | 数据类构造、probe 探测、benchmark save/load/append、状态转换、去重 | ≥ 10 |
| `tests/test_scheduler.py` | 确定性排序、冷启动默认值、竖排跳过 T1、衰减因子、tier 约束、allow_cloud_vision 过滤 | ≥ 12 |
| `tests/test_verifier.py` | 每条 Detector 独立测试、短路逻辑、优先级顺序、空知识库 | ≥ 14 |
| `tests/test_orchestrator.py` | 8 种兜底路径参数化、B6 双闸边界测试、egress 校验失败路径 | ≥ 10 |
| `tests/test_regression.py` | `run_engine()` 委派后旧行为不变验证 | ≥ 5 |

### 11.2 test_registry.py — 用例枚举

| # | 测试用例 | 验证点 |
|---|---------|-------|
| 1 | `test_engine_registration_construction` | `EngineRegistration` / `EngineStats` 数据类字段完整性 |
| 2 | `test_engine_stats_derived_fields` | `avg_latency_per_page_ms` 计算（含 epsilon 断言）|
| 3 | `test_engine_stats_cold_start_defaults` | 无数据时返回 `GLYPH_PASS_RATE_DEFAULT` 和 `AVG_LATENCY_DEFAULT_MS` |
| 4 | `test_engine_stats_bayesian_pass_rate` | 贝叶斯平均公式校验（已知数据→预期值） |
| 5 | `test_engine_stats_decay` | `decay()` 函数在不同时间差的输出（patch time.time）|
| 6 | `test_probe_empty_env` | 无引擎可用时 probe 返回空注册表 |
| 7 | `test_probe_all_engines_available` | 全部引擎 mock 可用 |
| 8 | `test_probe_partial_available` | 部分引擎可用 |
| 9 | `test_benchmark_save_load_roundtrip` | 空→写入→读出→追加完整循环 |
| 10 | `test_benchmark_survives_process_restart` | 模拟进程重启后 EngineStats 重建 |
| 11 | `test_benchmark_load_time_window` | 只加载最近 90 天数据 |
| 12 | `test_benchmark_load_corrupt_line` | 损坏行优雅跳过 |
| 13 | `test_status_transitions` | HEALTHY→UNAVAILABLE→HEALTHY |
| 14 | `test_dedup_registration` | 同名引擎二次注册覆盖 |

### 11.3 test_scheduler.py — 用例枚举

| # | 测试用例 | 验证点 |
|---|---------|-------|
| 1 | `test_select_empty_registry` | 空注册表返回空列表 |
| 2 | `test_cold_start_preset_priority` | 前 3 次：预设优先级排序 |
| 3 | `test_cold_start_bayesian_default` | 无历史数据时使用默认值评分 |
| 4 | `test_warm_start_sorts_by_score` | 给定 EngineStats fixture，排序可预测 |
| 5 | `test_tier_constraint` | Tier 过滤正确 |
| 6 | `test_vertical_layout_skips_tier1` | 竖排页跳过 Tier 1 |
| 7 | `test_allow_cloud_vision_filter` | 云端引擎过滤 |
| 8 | `test_excludes_unavailable_engines` | UNAVAILABLE 引擎被排除 |
| 9 | `test_top_n_limit` | 返回数不超过 max_engines |
| 10 | `test_domain_adjust_vertical_t2_boost` | 竖排页 Tier 2/3 +0.2 偏移 |
| 11 | `test_domain_adjust_laser_speed` | 激光照排+快速引擎 +0.1 |
| 12 | `test_domain_adjust_formula_recall` | 方剂书+高召回引擎 +0.1 |
| 13 | `test_polling_sampling` | 5% 轮询采样概率（确定性 mock） |
| 14 | `test_pinned_engine_override` | `pinned_engine` 覆盖调度逻辑 |
| 15 | `test_prefer_speed_sort` | `prefer=speed` 按延迟排序 |
| 16 | `test_prefer_accuracy_sort` | `prefer=accuracy` 按通过率排序 |
| 17 | `test_pinned_engine_unavailable_error` | pinned 引擎不可用时抛 `PinnedEngineUnavailableError` |

### 11.4 test_verifier.py — 用例枚举

| # | 测试用例 | 验证点 |
|---|---------|-------|
| 1 | `test_toxin_dose_detector_fail` | 剂量超限→FAIL |
| 2 | `test_toxin_dose_detector_safe` | 剂量在安全范围内→None |
| 3 | `test_toxin_dose_detector_boundary` | 剂量等于 max_dosage_g→None |
| 4 | `test_toxin_dose_detector_multiple_units` | g/克/钱 单位转换 |
| 5 | `test_toxin_dose_detector_substring_safe` | `附子汤` 不匹配 `附子` |
| 6 | `test_leakage_detector_fail` | 残余泄漏→FAIL |
| 7 | `test_char_count_spike_uncertain` | 字符数尖峰→UNCERTAIN |
| 8 | `test_confusion_set_unknown` | 命中混淆集→UNKNOWN |
| 9 | `test_term_kb_matcher_pass` | 稀有术语命中→PASS |
| 10 | `test_term_kb_matcher_rare` | variant_map 变体→RARE |
| 11 | `test_verify_short_circuit_toxin` | ToxinDose FAIL→立即返回（不执行后续检测器）|
| 12 | `test_verify_all_pass` | 全 PASS→PASS |
| 13 | `test_verify_rare_without_fail` | 有 RARE 无 FAIL→RARE |
| 14 | `test_verify_empty_knowledge_base` | 空知识库→UNKNOWN |
| 15 | `test_verify_all_detectors_disabled` | 全部 disable→UNKNOWN |
| 16 | `test_detector_priority_order` | 检测器按优先级执行 |
| 17 | `test_detector_enable_disable` | 独立 enable/disable |

### 11.5 test_orchestrator.py — 8 种兜底路径参数化

| # | 路径 | Tier 1 | Tier 2 | Tier 3 | 预期结果 |
|---|------|--------|--------|--------|---------|
| 1 | T1 PASS | PASS | — | — | 跳过 T2/T3 |
| 2 | T1 FAIL→T2 PASS | FAIL | PASS | — | T2 被调用 |
| 3 | T1 FAIL→T2 FAIL→T3 PASS | FAIL | FAIL | PASS | T3 被调用 |
| 4 | 全部 FAIL | FAIL | FAIL | FAIL | HumanGate |
| 5 | RARE | RARE | — | — | 同 PASS，放行 |
| 6 | UNKNOWN→T2 UNKNOWN→T3 UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | HumanGate |
| 7 | 预算耗尽 | — | — | — | 提前返回，failed_pages 含剩余页 |
| 8 | 空注册表 | — | — | — | 所有页入 failed_pages |
| 9 | egress 校验失败 | FAIL | egress FAIL | — | 跳过 Tier 2，继续 Tier 3 |

### 11.6 test_orchestrator.py — 额外用例

| # | 测试用例 | 验证点 |
|---|---------|-------|
| 10 | `test_b6_page_limit` | 页数闸边界（`page_num >= max_pages`） |
| 11 | `test_b6_timeout` | 时间闸边界（`elapsed > total_timeout`） |
| 12 | `test_engine_crash_continues` | 单引擎崩溃→继续下一引擎 |
| 13 | `test_vlm_cache_hit` | D3 缓存命中→跳过引擎调用 |
| 14 | `test_registry_record_called` | `registry.record()` 被正确调用 |
| 15 | `test_orchestrate_book_minimal_mock` | 最小 mock 集成测试（只 mock 引擎调用和探测条件，不 mock 注册/调度/验证内部逻辑）|

### 11.7 test_regression.py — 用例枚举

| # | 测试用例 | 验证点 |
|---|---------|-------|
| 1 | `test_run_engine_mock_shortcut` | `use_mock=True` → 不调用 `orchestrate_book()` |
| 2 | `test_run_engine_vlm_maps_to_disabled_tier1` | `use_vlm=True` → `disabled_tiers=[1]` |
| 3 | `test_run_engine_default_dispatches` | 默认路径→`orchestrate_book()` 被调用 |
| 4 | `test_run_engine_old_config_compat` | 未设 `SchedulerConfig` 时仍能工作 |
| 5 | `test_run_engine_require_real_preserved` | `require_real=True` 行为不变 |

### 11.8 conftest.py 共享 fixture

```python
# tests/conftest.py

@pytest.fixture
def mock_all_engines_available():
    """patch 所有 9 引擎的探测条件，返回全部可用的 EngineRegistration 列表"""

@pytest.fixture
def mock_only_tier1_engines():
    """只有本地 OCR 可用，VLM/LLM 全部不可用"""

@pytest.fixture
def sample_engine_stats():
    """构造已知排序的 EngineStats fixture（不走 record 方法）。
    EngineA: pass_rate=0.9, latency=100ms
    EngineB: pass_rate=0.8, latency=200ms
    """

@pytest.fixture
def frozen_time():
    """patch time.time() 固定时间，用于衰减测试"""
    frozen = 1000000.0
    patcher = patch("time.time", return_value=frozen)
    patcher.start()
    yield frozen
    patcher.stop()

@pytest.fixture
def tmp_benchmark_dir(tmp_path):
    """tmp_path 子目录，用于 benchmark 持久化 roundtrip 测试"""
    bm_dir = tmp_path / "benchmarks"
    bm_dir.mkdir()
    return str(bm_dir)
```

### 11.9 集成测试检查清单

| 集成场景 | 测试文件 | 覆盖状态 |
|---------|---------|---------|
| probe→registry 真实流程 | `test_orchestrator.py` 最小 mock | 需补充 |
| scheduler 排序→orchestrator 选择 | `test_orchestrator.py` 参数化 8 路径 | 已覆盖 |
| verifier 真实判断→orchestrator 降级 | `test_orchestrator.py` 参数化 + `test_verifier.py` | 已覆盖 |
| persist_benchmarks→下次加载评分 | `test_registry.py` roundtrip | 已覆盖 |
| 引擎真实异常→scheduler 标记 UNAVAILABLE | `test_orchestrator.py` engine_crash | 已覆盖 |

---

## 12. 迁移策略

### 12.1 `run_engine()` 委派模式

```python
def run_engine(pdf_path: str, book_code: str | None, config) -> BookResult:
    """run_engine 入口，内部委派到 orchestrate_book。

    兼容旧配置：
    - KZOCR_USE_MOCK=1 → 直接返回 mock，不走调度器
    - KZOCR_USE_VLM=1 → 映射为禁 Tier 1 的调度器配置
    - KZOCR_REQUIRE_REAL=1 → 交给调度器选 T1 引擎，T2/T3 全部跳过
    """
    # Mock 模式保持短路（PM 建议 E）
    if config.use_mock:
        return build_mock_book(book_code or "mock", ...)

    # VLM 模式映射为"禁用 Tier 1"
    if config.use_vlm:
        config_overrides = SchedulerConfig(disabled_tiers=[1])
        return orchestrate_book(pdf_path, book_code, config, config_overrides)

    # REQUIRE_REAL 映射为"只走 T1，跳过 T2/T3"
    if config.require_real:
        config_overrides = SchedulerConfig(tier_limit=1)
        return orchestrate_book(pdf_path, book_code, config, config_overrides)

    # 默认走调度器
    return orchestrate_book(pdf_path, book_code, config)
```

### 12.2 配置兼容层

| 旧环境变量 | v0.7 行为 | 废弃计划 |
|-----------|----------|---------|
| `KZOCR_USE_MOCK=1` | 直接返回 mock，不经过调度器 | **保留**（特殊模式）|
| `KZOCR_USE_VLM=1` | 映射为 `disabled_tiers=[1]` | 建议 v0.8 废弃 |
| `KZOCR_REQUIRE_REAL=1` | 映射为 `tier_limit=1` | 建议 v0.8 废弃 |
| `KZOCR_VLM_ENGINE=auto` | 由调度器管理 | **v0.7 废弃**（统一由调度器管理）|

### 12.3 配置项废弃时间线

| 配置项 | v0.7 状态 | v0.8 预期 |
|--------|----------|----------|
| `KZOCR_USE_VLM` | 兼容（映射为 disable T1） | 移除，警告 |
| `KZOCR_REQUIRE_REAL` | 兼容（映射为 tier_limit=1） | 移除，警告 |
| `KZOCR_VLM_ENGINE` | 废弃（调度器接管） | 移除 |

### 12.4 实施阶段依赖图

```
Phase 1（基础准备）：
  types.py: AdapterMeta 扩展 + EngineRunner 协议 + 辅助类型
  config.py: SchedulerConfig 嵌套 dataclass
  errors.py: 新增 SchedulerError, AllEnginesFailedError, PinnedEngineUnavailableError
  registry.py: EngineRegistration / EngineStats / EngineRegistry
  render_pages() 提取（从 _run_vlm 中提取 PDF→PageInput 逻辑）
  benchmark NDJSON 持久化（save/load/flush）
  conftest.py: 共享 fixture
  test_registry.py

Phase 2（核心逻辑）：
  scheduler.py: EngineScheduler.select_candidates() + domain_adjust() + decay() + Budget / EngineOverrides
  verifier.py: Detector 协议 + GlyphVerifier + 7 个预注册检测器（含运行时新增 ConfusionKeyPresenceDetector、PhraseErrorDetector）+ VisionRecheckAdapter（含 recheck + arbitrate_divergence）
  concurrency.py: AdaptiveController + run_engines_concurrent（ThreadPoolExecutor 全局单例）
  cross_align.py: align_engines + run_cross_align + Divergence + 形近字黑名单自学习（设计评审后新增，不在 v0.7 原始范围）
  PageLayout 定义
  test_scheduler.py + test_verifier.py + test_cross_divergence.py

Phase 3（编排 + 集成）：
  orchestrator.py: orchestrate_book() 主循环
  run.py: run_engine() 委派模式改造
  CLI 扩展（--engine, --prefer, kzocr benchmark 等）
  trace + 引擎报告日志
  review_manifest + feedback_apply
  test_orchestrator.py + test_regression.py + 集成测试
```
