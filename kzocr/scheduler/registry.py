"""E1: EngineRegistry（引擎注册中心）—— v0.7 §3。

承载引擎注册、运行统计与候选选择。派生指标（通过率、平均单页延迟）在
访问时实时计算，避免存储顺序不一致；冷启动退化为保守先验（§3.5）。

`config` 仅存环境变量名引用，绝不存储 API key 明文（§3.3）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from kzocr.engine.types import AdapterMeta, EngineStatus

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
    last_error: Optional[str] = None
    last_seen: float = 0.0  # time.time() 挂钟时间，支持跨进程持久化


@dataclass
class EngineRegistration:
    """引擎注册项。"""

    meta: AdapterMeta  # 见 types.py 扩展后的 AdapterMeta
    config: dict  # 仅存环境变量名引用，无明文凭证
    status: EngineStatus = "HEALTHY"
    stats: EngineStats = field(default_factory=EngineStats)
    adapter: Optional[Callable] = None  # EngineRunner 实例引用（v0.7 §2.2）

    @property
    def glyph_pass_rate(self) -> float:
        """字形通过率 = pass / (pass + fail + unknown)，冷启动返回先验。"""
        total = (
            self.stats.glyph_pass_count
            + self.stats.glyph_fail_count
            + self.stats.glyph_unknown_count
        )
        if total == 0:
            return GLYPH_PASS_RATE_DEFAULT
        return self.stats.glyph_pass_count / total

    @property
    def avg_latency_per_page_ms(self) -> float:
        """平均单页延迟，冷启动返回保守默认值。"""
        if self.stats.total_pages == 0:
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
        config: dict,
        adapter: Optional[Callable] = None,
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
        glyph: Optional[str] = None,
        latency_ms: Optional[float] = None,
        pages: int = 1,
        error: Optional[str] = None,
    ) -> None:
        """记录一次引擎调用的结果，更新统计。"""
        reg = self._regs.get(name)
        if reg is None:
            raise KeyError(f"未注册的引擎: {name}")
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
    prefer: Optional[str] = None,
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
