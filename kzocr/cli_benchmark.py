"""kzocr benchmark CLI 子命令 — v0.7 §7.1。

利用 ``EngineRegistry`` 的 NDJSON 持久化（``persist_benchmarks()`` /
``load_benchmarks()``）提供引擎性能基准的查询与管理。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from kzocr.config import load_config
from kzocr.scheduler.registry import EngineRegistry, probe_engines


def _load_registry(benchmark_dir: str) -> EngineRegistry:
    """构造 EngineRegistry 并加载持久化 benchmark。"""
    reg = EngineRegistry(benchmark_dir=benchmark_dir or None)
    if benchmark_dir:
        reg.load_benchmarks()
    return reg


def cmd_bench_status(args) -> int:
    """``kzocr benchmark status`` — 显示各引擎统计。"""
    cfg = load_config()
    reg = _load_registry(cfg.scheduler.benchmark_dir)
    engines = reg.list()
    if not engines:
        print("无 benchmark 数据（benchmark_dir 未配置或为空）")
        return 0
    print(f"{'Engine':<16} {'Tier':>4} {'Calls':>6} {'Pass':>5} "
          f"{'Fail':>5} {'Latency':>7} {'Status':>12}")
    print("-" * 60)
    for r in engines:
        s = r.stats
        lat = (
            f"{s.recent_avg_latency_ms:.0f}ms"
            if s.rolling_latencies
            else "—"
        )
        print(
            f"{r.meta.name:<16} {r.meta.tier:>4} {s.total_calls:>6} "
            f"{s.glyph_pass_count:>5} {s.glyph_fail_count:>5} {lat:>7} {r.status:>12}"
        )
    return 0


def cmd_bench_history(args) -> int:
    """``kzocr benchmark history`` — 显示原始 NDJSON 事件。"""
    cfg = load_config()
    benchmark_dir = cfg.scheduler.benchmark_dir
    if not benchmark_dir or not Path(benchmark_dir).is_dir():
        print("benchmark_dir 未配置或目录不存在")
        return 1
    engine_filter = args.engine
    for ndjson in sorted(Path(benchmark_dir).glob("*.ndjson")):
        if engine_filter and engine_filter not in ndjson.stem:
            continue
        for line in ndjson.read_text(encoding="utf-8").strip().splitlines():
            print(line)
    return 0


def cmd_bench_run(args) -> int:
    """``kzocr benchmark run`` — 构造注册中心、探测可用引擎、显示统计。"""
    cfg = load_config()
    reg = _load_registry(cfg.scheduler.benchmark_dir)
    probe_engines(reg)
    print(
        f"已探测 {len(reg.list())} 引擎"
        f"，benchmark_dir={cfg.scheduler.benchmark_dir or '(未配置)'}"
    )
    return cmd_bench_status(args)


def cmd_bench_reset(args) -> int:
    """``kzocr benchmark reset`` — 清空 benchmark 目录。"""
    cfg = load_config()
    benchmark_dir = cfg.scheduler.benchmark_dir
    if not benchmark_dir or not Path(benchmark_dir).is_dir():
        print("benchmark_dir 未配置或目录不存在，无需重置")
        return 0
    if not args.force:
        confirm = input(f"确认清空以下目录所有 benchmark 数据？\n  {benchmark_dir}\n(yes/no): ")
        if confirm.lower() not in ("yes", "y"):
            print("已取消")
            return 1
    shutil.rmtree(benchmark_dir)
    print(f"已清空 {benchmark_dir}")
    return 0
