"""E1: EngineRegistry（引擎注册中心）—— v0.7 §3。

承载引擎注册、运行统计与候选选择。派生指标（通过率、平均单页延迟）在
访问时实时计算，避免存储顺序不一致；冷启动退化为保守先验（§3.5）。

`config` 仅存环境变量名引用，绝不存储 API key 明文（§3.3）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Optional

from kzocr.engine.types import (
    AdapterMeta,
    EngineConfig,
    EngineRunner,
    EngineStatus,
    GlyphStatus,
)
from kzocr.engines.errors import SchedulerError

# ── 冷启动与贝叶斯评分常量（v0.7 §3.5）──
GLYPH_PASS_RATE_DEFAULT = 0.5  # 中等置信度假设，非 0
AVG_LATENCY_DEFAULT_MS = 10000.0  # 10s 保守估计，非 0
BAYESIAN_C = 7  # 贝叶斯常数，控制先验权重
BAYESIAN_PRIOR = 0.7  # 全局先验通过率


@dataclass
class EngineStats:
    """单引擎历史运行统计。仅存原始累加值，派生值在访问时计算。"""

    total_calls: int = 0
    total_latency_ms: int = 0
    total_pages: int = 0
    glyph_pass_count: int = 0
    glyph_fail_count: int = 0
    glyph_unknown_count: int = 0  # 追踪 UNKNOWN 状态
    glyph_rare_count: int = 0  # 追踪 RARE（术语库命中，中医古籍属正确识别，非失败）
    glyph_uncertain_count: int = 0  # 追踪 UNCERTAIN（需人工复核）
    last_error: Optional[str] = None
    last_seen: float = 0.0  # time.time() 挂钟时间，支持跨进程持久化

    def __repr__(self) -> str:
        """掩码 last_error（可能含凭证/路径），满足 §3.3 敏感字段掩码要求。"""
        return (
            f"EngineStats(total_calls={self.total_calls}, total_pages={self.total_pages}, "
            f"glyph_pass={self.glyph_pass_count}, glyph_fail={self.glyph_fail_count}, "
            f"glyph_unknown={self.glyph_unknown_count}, glyph_rare={self.glyph_rare_count}, "
            f"glyph_uncertain={self.glyph_uncertain_count}, "
            f"last_error={'<redacted>' if self.last_error else None})"
        )


@dataclass
class EngineRegistration:
    """引擎注册项。"""

    meta: AdapterMeta  # 见 types.py 扩展后的 AdapterMeta
    config: EngineConfig  # 仅存环境变量名引用，无明文凭证（§3.3）
    status: EngineStatus = "HEALTHY"
    stats: EngineStats = field(default_factory=EngineStats)
    adapter: Optional[EngineRunner] = None  # EngineRunner 实例引用（v0.7 §2.2）

    def __repr__(self) -> str:
        """掩码 config 与 adapter 引用（避免泄露凭证名/对象），满足 §3.3 要求。"""
        return (
            f"EngineRegistration(meta={self.meta.name!r}, tier={self.meta.tier}, "
            f"status={self.status}, config=<EngineConfig>, "
            f"adapter={'<set>' if self.adapter else None})"
        )

    @property
    def glyph_pass_rate(self) -> float:
        """字形通过率 = (PASS + RARE) / (PASS + FAIL + UNKNOWN + RARE + UNCERTAIN)。

        RARE（术语库命中）在中医古籍中属正确识别，计入通过分子；UNKNOWN/UNCERTAIN
        计入分母但不计通过。冷启动（无样本）返回先验 GLYPH_PASS_RATE_DEFAULT（§3.5）。
        """
        s = self.stats
        total = (
            s.glyph_pass_count
            + s.glyph_fail_count
            + s.glyph_unknown_count
            + s.glyph_rare_count
            + s.glyph_uncertain_count
        )
        if total == 0:
            return GLYPH_PASS_RATE_DEFAULT
        return (s.glyph_pass_count + s.glyph_rare_count) / total

    @property
    def avg_latency_per_page_ms(self) -> float:
        """平均单页延迟。无延迟样本（冷启动或延迟未记录）返回保守默认值。

        修复：原实现仅凭 `total_pages == 0` 判空，会在「有页数、无延迟记录」时
        返回 0，触发 `_bayesian_score` 的 1/0 除零崩溃；改为以 `total_latency_ms` 判空。
        """
        if self.stats.total_latency_ms == 0:
            return AVG_LATENCY_DEFAULT_MS
        return self.stats.total_latency_ms / self.stats.total_pages


class EngineRegistry:
    """引擎注册中心：注册、查询、统计记录与候选选择。"""

    def __init__(self) -> None:
        self._regs: dict[str, EngineRegistration] = {}

    def register(self, reg: EngineRegistration) -> None:
        """注册一个引擎项（覆盖同名）。"""
        self._regs[reg.meta.name] = reg

    def register_adapter(
        self,
        meta: AdapterMeta,
        config: EngineConfig,
        adapter: Optional[EngineRunner] = None,
        status: EngineStatus = "HEALTHY",
    ) -> EngineRegistration:
        """便捷注册：由 AdapterMeta + config 构造并注册。

        `config` 只应含环境变量名引用（如 `{"api_key_env": "X"}`），不含明文凭证。
        """
        reg = EngineRegistration(meta=meta, config=config, status=status, adapter=adapter)
        self.register(reg)
        return reg

    def get(self, name: str) -> Optional[EngineRegistration]:
        return self._regs.get(name)

    def list(self) -> list[EngineRegistration]:
        return list(self._regs.values())

    def list_by_tier(self, tier: int) -> list[EngineRegistration]:
        return [r for r in self._regs.values() if r.meta.tier == tier]

    def record(
        self,
        name: str,
        success: bool,
        glyph: Optional[GlyphStatus] = None,
        latency_ms: Optional[float] = None,
        pages: int = 1,
        error: Optional[str] = None,
    ) -> None:
        """记录一次引擎调用的结果，更新统计。

        `glyph` 取字形验证状态（GlyphStatus）。RARE/UNCERTAIN 不再被静默丢弃，
        分别计入独立计数——领域评审指出中医古籍异体字/古方名占比高，漏计会系统性
        低估通过率。注：E3 落地 `GlyphVerdict` 后，此处应接受其 `.status` 字段。
        """
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


def _bayesian_score(reg: EngineRegistration) -> float:
    """贝叶斯平均评分（v0.7 §3.5）：

    score = (pass_rate × n + C × prior) / (n + C) × (1 / latency_avg)
    """
    n = reg.stats.total_pages
    pass_rate = reg.glyph_pass_rate
    latency = reg.avg_latency_per_page_ms
    return (pass_rate * n + BAYESIAN_C * BAYESIAN_PRIOR) / (n + BAYESIAN_C) * (1.0 / latency)


def select_candidates(
    registry: EngineRegistry,
    tier: int,
    prefer: Optional[Literal["speed", "accuracy"]] = None,
) -> list[EngineRegistration]:
    """按 tier 过滤候选并按评分排序（v0.7 §4.3 / §4.5 的聚焦版）。

    - `prefer="speed"`：按平均单页延迟升序（最快优先）
    - `prefer="accuracy"`：按字形通过率降序（最准优先）
    - 默认：贝叶斯评分降序（§3.5）
    同分时保持稳定排序（保留注册顺序）。
    """
    candidates = registry.list_by_tier(tier)
    if prefer == "speed":
        candidates = sorted(candidates, key=lambda e: e.avg_latency_per_page_ms)
    elif prefer == "accuracy":
        candidates = sorted(candidates, key=lambda e: e.glyph_pass_rate, reverse=True)
    else:
        candidates = sorted(candidates, key=_bayesian_score, reverse=True)
    return candidates
