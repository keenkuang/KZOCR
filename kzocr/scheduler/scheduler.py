"""E2: EngineScheduler（引擎调度器）—— v0.7 §4。

完整候选选择流程（§4.1 九步）：覆盖检查 → 层级约束 → 竖排跳过 Tier 1 →
allow_cloud_vision 过滤 → 资源过滤 → 预算检查 → 加权排序（贝叶斯评分 ×
衰减因子 × 领域权重）→ 取 Top-N → 5% 轮询采样。

`EngineStats.decay()`（§4.2）实现时效衰减；`domain_adjust()`（§4.3）实现
中医古籍场景的领域感知权重。
"""

from __future__ import annotations

import logging
import time
import random
from dataclasses import dataclass, field
from typing import Optional

from kzocr.engine.types import PageLayout
from kzocr.scheduler.registry import EngineRegistry, EngineRegistration
from kzocr.engines.errors import PinnedEngineUnavailableError

_logger = logging.getLogger(__name__)

# ── 调度常量（v0.7 §4.2 / §4.4）──
DECAY_HALF_LIFE_DAYS = 7.0
POLL_PROBABILITY = 0.05
DEFAULT_TIER_LIMITS: dict[int, int] = {1: 2, 2: 1, 3: 1}


# ── 调度支撑类型（v0.7 §1.5 / §1.6 / §4.3）──


@dataclass
class Budget:
    """资源预算（§1.5）。由编排主循环管理，调度器只读查询。"""

    max_pages: int  # KZOCR_MAX_PAGES
    max_wall_clock_ms: int  # KZOCR_TOTAL_TIMEOUT * 1000
    max_tokens: int = 0  # token 预算（可选）
    max_time_per_page_ms: int = 120000  # 单页最大耗时（默认 120s）
    allow_cloud_vision: bool = False  # 是否允许云端引擎
    _exhausted: bool = False  # 内部：是否已耗尽（由编排循环设置）

    def exhaust(self) -> None:
        """标记预算已耗尽。由编排循环在双闸触发时调用。"""
        self._exhausted = True

    @property
    def exhausted(self) -> bool:
        return self._exhausted

    def check_time_budget(self, elapsed_s: float) -> bool:
        return elapsed_s * 1000 < self.max_wall_clock_ms


@dataclass
class PageInfo:
    """页面信息（调度器输入，§4.3）。"""

    page_num: int
    book_type: str = ""  # "tcm_ancient" / "tcm_modern" / "formula" ...
    pub_era: str = ""  # "lead_print" / "transition" / "laser"
    is_vertical: bool = False
    has_table: bool = False


@dataclass
class EngineOverrides:
    """CLI 传入的调度器覆盖参数（§1.6 / §4.5）。"""

    pinned_engine: Optional[str] = None  # --engine <name>
    prefer: Optional[str] = None  # "speed" / "accuracy"
    tier_order: Optional[list[int]] = None  # --tier-order "1,3,2"
    tier_limit: Optional[int] = None  # --tier-limit N
    max_time_per_page: Optional[int] = None  # --max-time-per-page N
    resume: bool = False  # --resume：断点续跑
    retry_failed: bool = False  # --retry-failed：仅重跑失败页
    backoff_threshold_ms: int = 30000  # 即时延迟 > 此阈值时，对该引擎暂停调度（冷却）
    backoff_fail_rate: float = 0.5  # 即时失败率 > 此阈值时，对该引擎暂停调度
    rate_limited_until: dict[str, float] = field(default_factory=dict)
    enable_cross_check: bool = False  # 成功页跨引擎采样比对（默认关；需 Tier2 引擎可用）
    consensus_sample_rate: float = 0.0  # 共识一致页抽样率（0.0=关；0.1=10%；仅 enable_cross_check 时生效）


# ── 评分与权重（§4.2 / §4.3）──


def _compute_bayesian_score(reg: EngineRegistration) -> float:
    """完整权重评分（§4.2）：pass_rate × (1000 / max(latency, 1)) × decay。

    - glyph_pass_rate 已含贝叶斯平均（§3.5）
    - latency 下限 1ms 防除零
    - decay(last_seen) 时效衰减（§4.2）
    - F3 Part B: 混合近期滚动窗口（recent 30%）与长期全量（long 70%）
    """
    pass_rate = reg.glyph_pass_rate
    latency = max(reg.avg_latency_per_page_ms, 1.0)
    decay = reg.stats.decay(DECAY_HALF_LIFE_DAYS)
    long_score = pass_rate * (1000.0 / latency) * decay

    # 近期滚动指标
    recent_latency = max(reg.stats.recent_avg_latency_ms, 1.0)
    recent_fail = reg.stats.recent_fail_rate
    recent_pass = 1.0 - recent_fail
    recent_score = recent_pass * (1000.0 / recent_latency)

    # 混合：70% 长期 + 30% 近期（至少有滚动采样才混入近期）
    scales = reg.stats.rolling_latencies
    if len(scales) >= 5:
        return long_score * 0.7 + recent_score * 0.3
    return long_score


def domain_adjust(
    base_score: float,
    engine: EngineRegistration,
    page_info: PageInfo,
    page_layout: Optional[PageLayout] = None,
) -> float:
    """领域感知权重调整（§4.3）。

    - 竖排页：Tier 2/3 引擎 base_score × 1.5 + 0.2 混合偏移（T1 已被跳过，不再降权）
    - laser 出版时代 + 快速引擎（<5s）：+0.1
    - formula 方剂书 + 高召回引擎（pass_rate > 0.9）：+0.1
    采用加法 + 乘法混合模式，避免纯乘法使低分归零（加法提供保底）。
    """
    tier = engine.meta.tier
    if page_layout and page_layout.is_vertical and tier >= 2:
        return base_score * 1.5 + 0.2
    adjustments = 0.0
    if page_info.pub_era == "laser" and engine.avg_latency_per_page_ms < 5000:
        adjustments += 0.1
    if page_info.book_type == "formula" and engine.glyph_pass_rate > 0.9:
        adjustments += 0.1
    return base_score + adjustments


def _should_poll() -> bool:
    """以 5% 概率触发轮询采样（§4.1 第 9 步），避免冷启动陷阱。"""
    return random.random() < POLL_PROBABILITY


def _select_poll_candidate(
    candidates: list[EngineRegistration],
    top_n: list[EngineRegistration],
) -> Optional[EngineRegistration]:
    """从同 tier 未入选 Top-N 且非 UNAVAILABLE 的候选中随机挑一个做轮询。"""
    rest = [e for e in candidates if e not in top_n and e.status != "UNAVAILABLE"]
    if not rest:
        return None
    return random.choice(rest)


# ── 调度器（§4 / §8）──


class EngineScheduler:
    """引擎调度器：从注册中心动态选择候选（§4.1 / §8 步骤 2.1）。

    `tier_limits` 控制每个 tier 的候选上限（§4.4，默认 Tier1=2 / Tier2=1 / Tier3=1）。
    接入 `SchedulerConfig`（§7.3）留待 E5 集成阶段，此处以内置默认 + 构造覆盖实现。
    """

    def __init__(self, tier_limits: Optional[dict[int, int]] = None) -> None:
        self.tier_limits: dict[int, int] = dict(tier_limits or DEFAULT_TIER_LIMITS)

    def _max_engines(self, tier: int) -> int:
        return self.tier_limits.get(tier, 1)

    def select_candidates(
        self,
        registry: EngineRegistry,
        tier: int,
        page_info: PageInfo,
        budget: Budget,
        page_layout: Optional[PageLayout] = None,
        overrides: Optional[EngineOverrides] = None,
    ) -> list[EngineRegistration]:
        """选择指定 tier 的候选引擎列表（§4.1 九步）。

        执行顺序：覆盖检查 → 层级约束 → 竖排跳过 T1 → allow_cloud_vision 过滤
        → 资源过滤 → 预算检查 → 加权排序 → 取 Top-N → 5% 轮询采样。
        """
        # ── 第 1 步：覆盖检查（pinned_engine）──
        if overrides and overrides.pinned_engine:
            engine = registry.get(overrides.pinned_engine)
            if engine is None or engine.status == "UNAVAILABLE":
                raise PinnedEngineUnavailableError(
                    f"Pinned engine '{overrides.pinned_engine}' not available"
                )
            return [engine]

        # ── 第 2 步：层级约束（list_by_tier 已默认排除 UNAVAILABLE）──
        candidates = registry.list_by_tier(tier)
        if not candidates:
            _logger.info("[scheduler] tier=%d: no candidates registered", tier)
            return []

        # ── 第 3 步：竖排检测 → 跳过 Tier 1 ──
        if page_layout and page_layout.is_vertical and tier == 1:
            _logger.info("[scheduler] page vertical layout detected, skipping Tier 1")
            return []

        # ── 第 4 步：allow_cloud_vision 过滤（跳过云端引擎）──
        if not budget.allow_cloud_vision:
            before = len(candidates)
            candidates = [e for e in candidates if not e.meta.requires_network]
            if len(candidates) < before:
                _logger.info(
                    "[scheduler] filtered %d cloud engines (allow_cloud_vision=False)",
                    before - len(candidates),
                )

        # ── 第 4.5 步：F3 自适应调速（backoff）+ 429 限流──
        if overrides:
            before = len(candidates)
            now = time.time()
            filtered = []
            for e in candidates:
                # 429 退避检查
                backoff_until = overrides.rate_limited_until.get(e.meta.name, 0)
                if backoff_until > now:
                    _logger.info(
                        "[scheduler] rate_limited engine=%s (until t=%.0f)",
                        e.meta.name, backoff_until,
                    )
                    continue
                s = e.stats
                lat_ok = (
                    not s.rolling_latencies
                    or s.recent_avg_latency_ms <= overrides.backoff_threshold_ms
                )
                fail_ok = (
                    not s.rolling_failures
                    or s.recent_fail_rate <= overrides.backoff_fail_rate
                )
                if lat_ok and fail_ok:
                    filtered.append(e)
                else:
                    _logger.info(
                        "[scheduler] backoff engine=%s (lat=%.0f/%d, fail=%.2f/%.2f)",
                        e.meta.name, s.recent_avg_latency_ms, overrides.backoff_threshold_ms,
                        s.recent_fail_rate, overrides.backoff_fail_rate,
                    )
            candidates = filtered

        # ── 第 5 步：资源过滤（状态位缓存；list_by_tier 已排除，此处再保一层）──
        candidates = [e for e in candidates if e.status != "UNAVAILABLE"]
        if not candidates:
            return []

        # ── 第 6 步：预算检查（粗略；精细预算在编排循环逐引擎做）──
        if budget.exhausted:
            _logger.info("[scheduler] budget exhausted, no candidates")
            return []

        # ── 第 7 步：加权排序 ──
        def _score(e: EngineRegistration) -> float:
            base = _compute_bayesian_score(e)
            return domain_adjust(base, e, page_info, page_layout)

        if overrides and overrides.prefer == "speed":
            candidates.sort(key=lambda e: e.avg_latency_per_page_ms)
        elif overrides and overrides.prefer == "accuracy":
            candidates.sort(key=lambda e: e.glyph_pass_rate, reverse=True)
        else:
            candidates.sort(key=_score, reverse=True)

        # ── 第 8 步：取 Top-N ──
        top_n = candidates[: self._max_engines(tier)]

        # ── 第 9 步：5% 轮询采样 ──
        if _should_poll():
            poll = _select_poll_candidate(candidates, top_n)
            if poll:
                _logger.info("[scheduler] polling low-score engine: %s", poll.meta.name)
                top_n.append(poll)

        return top_n
