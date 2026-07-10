"""E1: EngineRegistry（引擎注册中心）—— v0.7 §3。

承载引擎注册、运行统计与候选选择。派生指标（通过率、平均单页延迟）在
访问时实时计算，避免存储顺序不一致；冷启动退化为保守先验（§3.5）。

`config` 仅存环境变量名引用，绝不存储 API key 明文（§3.3）。
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from kzocr.engine.types import (
    AdapterMeta,
    EngineConfig,
    EngineRunner,
    EngineStatus,
    GlyphStatus,
    ProbeResult,
)
from kzocr.engines.errors import SchedulerError
from kzocr.engines.atomic import _check_base
from kzocr.security.egress import validate_url

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

    def decay(self, half_life_days: float = 7.0) -> float:
        """时效衰减因子（§4.2）。last_seen 越久衰减越强；未探测过返回 1.0。"""
        if self.last_seen == 0.0:
            return 1.0
        elapsed_days = (time.time() - self.last_seen) / 86400.0
        return 0.5 ** (elapsed_days / half_life_days)


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
    """引擎注册中心：注册、查询、统计记录与候选选择。

    benchmark 持久化（§7.1）：进程内 EngineStats 实时更新内存，书完成后通过
    `persist_benchmarks()` 将增量事件以 NDJSON 逐行追加（O(1)）写入
    `$benchmark_dir/{engine}.ndjson`；启动时 `load_benchmarks()` 重建已注册引擎统计。
    """

    def __init__(self, benchmark_dir: Optional[str] = None) -> None:
        self._regs: dict[str, EngineRegistration] = {}
        self.benchmark_dir: Optional[str] = benchmark_dir
        self._pending: list[dict] = []

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

    def list_by_tier(
        self, tier: int, include_unavailable: bool = False
    ) -> list[EngineRegistration]:
        """返回指定 tier 的已注册引擎；默认排除 UNAVAILABLE（§4.1 资源过滤）。"""
        return [
            r
            for r in self._regs.values()
            if r.meta.tier == tier
            and (include_unavailable or r.status != "UNAVAILABLE")
        ]

    def mark_unavailable(self, name: str) -> None:
        """将引擎标记为 UNAVAILABLE，资源过滤时从候选排除（§4.1 状态位缓存）。"""
        self._set_status(name, "UNAVAILABLE")

    def mark_degraded(self, name: str) -> None:
        """将引擎标记为 DEGRADED（仍可选，但评分应低于 HEALTHY）。"""
        self._set_status(name, "DEGRADED")

    def mark_healthy(self, name: str) -> None:
        """将引擎恢复为 HEALTHY。"""
        self._set_status(name, "HEALTHY")

    def _set_status(self, name: str, status: EngineStatus) -> None:
        reg = self._regs.get(name)
        if reg is None:
            raise SchedulerError(f"未注册的引擎: {name}")
        reg.status = status

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
        if self.benchmark_dir is not None:
            self._pending.append(
                {
                    "ts": s.last_seen,
                    "engine": name,
                    "page": pages,
                    "latency_ms": int(latency_ms) if latency_ms is not None else 0,
                    "glyph_status": glyph,
                    "tier": reg.meta.tier,
                    "success": success,
                }
            )

    def persist_benchmarks(self) -> None:
        """将累计的增量事件以 NDJSON 逐行追加（O(1)）写入 benchmark 目录（§7.1）。

        进程内 EngineStats 实时更新内存，书完成后调用本方法批量 flush 增量；
        行级追加而非全文覆写，避免 O(n²) I/O 退化。无 benchmark_dir 时为空操作。
        """
        if self.benchmark_dir is None:
            self._pending.clear()
            return
        if not self._pending:
            return
        base = Path(self.benchmark_dir)
        base.mkdir(parents=True, exist_ok=True)
        by_engine: dict[str, list[dict]] = {}
        for ev in self._pending:
            by_engine.setdefault(ev["engine"], []).append(ev)
        for engine, events in by_engine.items():
            if "/" in engine or "\\" in engine:
                raise ValueError(f"非法引擎名（路径穿越风险）: {engine}")
            path = _check_base(base / f"{engine}.ndjson", allowed_base=None)
            with path.open("a", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self._pending.clear()

    def load_benchmarks(self) -> None:
        """进程启动时从 benchmark 目录加载 NDJSON，重建已注册引擎的 EngineStats（§7.1）。

        目录不存在或文件损坏时静默跳过（冷启动 / 容错），不阻断启动。
        """
        if self.benchmark_dir is None:
            return
        base = Path(self.benchmark_dir)
        if not base.is_dir():
            return
        for ndjson in sorted(base.glob("*.ndjson")):
            engine = ndjson.stem
            try:
                with ndjson.open(encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        _apply_event(self.get(engine), json.loads(line))
            except (json.JSONDecodeError, OSError):
                continue


def _apply_event(reg: Optional[EngineRegistration], ev: dict) -> None:
    """将一条 benchmark 事件累加到引擎统计（load_benchmarks 用）。"""
    if reg is None:
        return  # 历史引擎不再注册，跳过
    s = reg.stats
    s.total_calls += 1
    s.total_pages += int(ev.get("page", 1))
    s.total_latency_ms += int(ev.get("latency_ms") or 0)
    g = ev.get("glyph_status")
    if g == "PASS":
        s.glyph_pass_count += 1
    elif g == "FAIL":
        s.glyph_fail_count += 1
    elif g == "UNKNOWN":
        s.glyph_unknown_count += 1
    elif g == "RARE":
        s.glyph_rare_count += 1
    elif g == "UNCERTAIN":
        s.glyph_uncertain_count += 1
    if not ev.get("success", True):
        s.last_error = ev.get("error")
    s.last_seen = float(ev.get("ts", s.last_seen))


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
    include_unavailable: bool = False,
) -> list[EngineRegistration]:
    """按 tier 过滤候选并按评分排序（v0.7 §4.3 / §4.5 的聚焦版）。

    - 默认排除 `UNAVAILABLE` 引擎（§4.1 资源过滤：状态位缓存）；
      `include_unavailable=True` 强制包含所有（如手动 pinned 引擎）。
    - `prefer="speed"`：按平均单页延迟升序（最快优先）
    - `prefer="accuracy"`：按字形通过率降序（最准优先）
    - 默认：贝叶斯评分降序（§3.5）
    同分时保持稳定排序（保留注册顺序）。
    """
    candidates = registry.list_by_tier(tier, include_unavailable=include_unavailable)
    if prefer == "speed":
        candidates = sorted(candidates, key=lambda e: e.avg_latency_per_page_ms)
    elif prefer == "accuracy":
        candidates = sorted(candidates, key=lambda e: e.glyph_pass_rate, reverse=True)
    else:
        candidates = sorted(candidates, key=_bayesian_score, reverse=True)
    return candidates


def probe_engines(
    registry: EngineRegistry,
    probe_result: Optional[ProbeResult] = None,
) -> None:
    """逐引擎探测可用性，刷新 EngineRegistration.status（v0.7 §3.4）。

    基础探测（env/file/port）立即执行；api 类仅做 B3 egress 合法性校验
    （validate_url），真实网络健康检查留待 E2 惰性执行。优先复用
    `probe_result` 缓存的端口/key 状态，缺失时回退实时探测。无 probe 配置
    （空 dict）的引擎保持 status 现状，不强制探测。
    """
    for reg in registry.list():
        _probe_one(reg, probe_result)


def _probe_one(reg: EngineRegistration, probe_result: Optional[ProbeResult]) -> None:
    probe = reg.meta.probe or {}
    method = probe.get("method")
    if not method:
        return  # 无探测配置：保持 status 现状
    try:
        if method == "env":
            key = probe.get("key", "")
            cached = probe_result.keys.get(key) if probe_result else None
            ok = cached if cached is not None else (os.environ.get(key) is not None)
        elif method == "file":
            ok = Path(probe.get("path", "")).expanduser().exists()
        elif method == "port":
            port = int(probe.get("port", 0))
            cached = probe_result.ports.get(str(port)) if probe_result else None
            ok = cached if cached is not None else _tcp_reachable(
                probe.get("host", "127.0.0.1"), port
            )
        elif method == "api":
            url = probe.get("url") or reg.config.base_url or ""
            if not url:
                ok = True  # 无 URL 可校验，保守放行（E2 惰性网络检查）
            else:
                validate_url(url)  # B3：egress 合法性校验，非法抛 ValueError
                ok = True
        else:
            ok = True  # 未知 method 保守放行
    except (OSError, ValueError):
        # 含 validate_url 抛的 ValueError 与 _tcp_reachable 的 OSError；
        # 未来若 validate_url 改抛 EgressBlockedError，需在此扩展。
        ok = False
    reg.status = "HEALTHY" if ok else "UNAVAILABLE"


def _tcp_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    """TCP 连通性探测（单次短超时）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
