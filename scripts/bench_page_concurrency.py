#!/usr/bin/env python3
"""页级并发/并行基准估算工具。

测量当前顺序编排的页级吞吐（pages/min），估算页面级并行（多引擎并发处理不同页）
的理论最大加速比。支持 mock 引擎（无真实 OCR 依赖）。

用法：
    python scripts/bench_page_concurrency.py                  # 默认 mock 3 页 × 2 引擎
    python scripts/bench_page_concurrency.py --pages 20       # 模拟 20 页
    python scripts/bench_page_concurrency.py --engines 3 --page-latency 2.0  # 3 引擎，2s/页
"""

from __future__ import annotations

import argparse
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(level=logging.WARNING)
_logger = logging.getLogger(__name__)

# ── Mock 引擎结果 ──

_MOCK_TEXT = (
    "桂枝汤方：桂枝三两，芍药三两，甘草二两，生姜三两，大枣十二枚。"
    "上五味，㕮咀三味，以水七升，微火煮取三升，去滓，适寒温，服一升。"
)


@dataclass
class MockEngine:
    name: str
    latency_s: float = 1.0   # 单页平均耗时秒数
    jitter: float = 0.2      # 耗时的随机波动比例
    fail_rate: float = 0.0   # 失败率

    def run_page(self, page_num: int) -> tuple[str, float]:
        """模拟执行一页。返回 (text, latency_s)。"""
        delay = self.latency_s * (1 + random.uniform(-self.jitter, self.jitter))
        delay = max(0.1, delay)
        time.sleep(delay)
        if random.random() < self.fail_rate:
            raise RuntimeError(f"{self.name} failed on page {page_num}")
        return _MOCK_TEXT, delay


@dataclass
class BenchmarkResult:
    """一次基准运行的结果。"""
    label: str
    total_pages: int
    total_time_s: float
    pages_per_min: float
    latency_per_page_s: float
    failures: int = 0


def run_sequential(
    engines: list[MockEngine],
    total_pages: int,
) -> BenchmarkResult:
    """当前顺序编排：逐页依次尝试引擎，成功即跳过。
    
    模拟 orchestrator 的 Tier1→Tier2→Tier3 逻辑：每页先试第一个引擎，
    失败则试下一个，全部失败则记失败。
    """
    start = time.monotonic()
    failures = 0
    for pno in range(total_pages):
        ok = False
        for eng in engines:
            try:
                eng.run_page(pno)
                ok = True
                break
            except RuntimeError:
                continue
        if not ok:
            failures += 1
    elapsed = time.monotonic() - start
    return BenchmarkResult(
        label="顺序（当前）",
        total_pages=total_pages,
        total_time_s=round(elapsed, 2),
        pages_per_min=round(total_pages / elapsed * 60, 1) if elapsed > 0 else 0,
        latency_per_page_s=round(elapsed / total_pages, 2) if total_pages > 0 else 0,
        failures=failures,
    )


def run_page_parallel(
    engines: list[MockEngine],
    total_pages: int,
    max_workers: int = 4,
) -> BenchmarkResult:
    """页面级并行：多引擎并发处理不同页。

    每页分配一个引擎（轮询或随机），各页通过 ThreadPoolExecutor 并发执行。
    模拟未来「不同页不同引擎并行」的场景。
    """
    def _process_one_page(pno: int, eng: MockEngine) -> bool:
        try:
            eng.run_page(pno)
            return True
        except RuntimeError:
            return False

    start = time.monotonic()
    failures = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {}
        for pno in range(total_pages):
            eng = engines[pno % len(engines)]  # 轮询分配引擎
            futs[pool.submit(_process_one_page, pno, eng)] = pno
        for fut in as_completed(futs):
            if not fut.result():
                failures += 1
    elapsed = time.monotonic() - start
    return BenchmarkResult(
        label=f"页级并行（{max_workers} workers）",
        total_pages=total_pages,
        total_time_s=round(elapsed, 2),
        pages_per_min=round(total_pages / elapsed * 60, 1) if elapsed > 0 else 0,
        latency_per_page_s=round(elapsed / total_pages, 2) if total_pages > 0 else 0,
        failures=failures,
    )


def estimate_theoretical_speedup(
    engines: list[MockEngine],
    total_pages: int,
) -> dict:
    """估算理论最大加速比。

    顺序: sum 各引擎耗时
    并行: 最慢引擎耗时 + 剩余页分摊
    
    Returns:
        dict with seq_time, par_time, speedup estimates.
    """
    # 简化模型：假设每页用最快的引擎，且引擎无状态争抢
    best = min(e.latency_s for e in engines)  # 最快引擎单页耗时
    worst = max(e.latency_s for e in engines)
    avg = sum(e.latency_s for e in engines) / len(engines)

    seq_time = total_pages * avg  # 每页试所有引擎平均
    # 理想并行：页数 / workers * 最快引擎耗时
    workers = len(engines)  # 假设每引擎分配一个线程
    par_time = (total_pages / workers) * best
    return {
        "total_pages": total_pages,
        "engines": len(engines),
        "fastest_engine_s": round(best, 2),
        "avg_engine_s": round(avg, 2),
        "seq_estimate_s": round(seq_time, 1),
        "par_estimate_s": round(par_time, 1),
        "speedup_estimate": round(seq_time / par_time, 1) if par_time > 0 else 1,
    }


def print_report(
    results: list[BenchmarkResult],
    theoretical: dict,
) -> None:
    """打印基准报告。"""
    print("=" * 60)
    print("页级并发基准估算报告")
    print("=" * 60)
    print()

    # 实际测量
    print("─" * 40)
    print("实际测量（含随机 jitter）")
    print("─" * 40)
    print(f"{'模式':<25} {'页数':<6} {'耗时(s)':<10} {'页/min':<10} {'每页(s)':<10} {'失败':<6}")
    for r in results:
        print(
            f"{r.label:<25} {r.total_pages:<6} {r.total_time_s:<10} "
            f"{r.pages_per_min:<10} {r.latency_per_page_s:<10} {r.failures:<6}"
        )
    if len(results) >= 2:
        seq = results[0]
        par = results[1]
        if par.total_time_s > 0:
            actual_speedup = round(seq.total_time_s / par.total_time_s, 1)
            print(f"\n实际加速比: {actual_speedup}×")

    print()
    print("─" * 40)
    print("理论估算（无 jitter、竞争）")
    print("─" * 40)
    t = theoretical
    print(f"总页数: {t['total_pages']}")
    print(f"引擎数: {t['engines']}")
    print(f"最快引擎: {t['fastest_engine_s']}s/页")
    print(f"引擎平均: {t['avg_engine_s']}s/页")
    print(f"顺序估算: {t['seq_estimate_s']}s")
    print(f"并行估算: {t['par_estimate_s']}s")
    print(f"理论加速比: {t['speedup_estimate']}×")

    print()
    if t["speedup_estimate"] >= 2:
        print("结论：页面级并行有显著加速潜力，建议实现并发编排。")
    elif t["speedup_estimate"] >= 1.5:
        print("结论：页面级并行有一定收益，可考虑有限的并发（2-3 worker）。")
    else:
        print("结论：页面级并行收益有限（引擎耗时接近，或页数较少）。")


def main() -> None:
    parser = argparse.ArgumentParser(description="页级并发基准估算工具")
    parser.add_argument("--pages", type=int, default=10, help="模拟页数（默认 10）")
    parser.add_argument("--engines", type=int, default=2, help="引擎数量（默认 2）")
    parser.add_argument("--page-latency", type=float, default=0.5, help="引擎平均耗时秒数（默认 0.5）")
    parser.add_argument("--jitter", type=float, default=0.3, help="耗时波动比例（默认 0.3）")
    parser.add_argument("--fail-rate", type=float, default=0.0, help="引擎失败率（默认 0）")
    parser.add_argument("--max-workers", type=int, default=4, help="并行 Worker 数（默认 4）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args()

    random.seed(args.seed)

    engines = [
        MockEngine(
            name=f"eng{i}",
            latency_s=args.page_latency * (0.8 + 0.4 * i / max(1, args.engines - 1)),
            jitter=args.jitter,
            fail_rate=args.fail_rate,
        )
        for i in range(args.engines)
    ]

    print(f"引擎配置:")
    for e in engines:
        print(f"  {e.name}: {e.latency_s}s/页 (jitter={e.jitter}, fail={e.fail_rate})")
    print()

    seq = run_sequential(engines, args.pages)
    par = run_page_parallel(engines, args.pages, max_workers=args.max_workers)
    theo = estimate_theoretical_speedup(engines, args.pages)

    print_report([seq, par], theo)


if __name__ == "__main__":
    main()
