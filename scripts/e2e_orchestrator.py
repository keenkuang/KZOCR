#!/usr/bin/env python3
"""通过 orchestrator 全路径的端到端验证（取代直接驱动引擎的方式）。

注册 PaddleOCR 为 Tier1、RapidOCR 为 Tier2，走完整 orchestrate_book 流水线：
  渲染 → 版心裁切 → Tier1 识别 → 验证 → cross-check → 共识抽样

依赖：paddleocr、rapidocr_onnxruntime（本机已装）。
用法：
  python scripts/e2e_orchestrator.py <pdf> [--pages 10] [--cross-check] [--sample-rate 0.1]
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

from kzocr.engine.adapters import PaddleOCRAdapter, RapidOCRAdapter
from kzocr.engine.types import AdapterMeta, EngineConfig
from kzocr.scheduler.orchestrator import orchestrate_book
from kzocr.scheduler.registry import EngineRegistry
from kzocr.scheduler.scheduler import EngineOverrides


@dataclass
class E2EConfig:
    max_pages: int = 50
    total_timeout_s: int = 7200
    max_time_per_page_ms: int = 120000
    allow_cloud_vision: bool = False
    book_type: str = "古籍"
    pub_era: str = ""
    output_dir: str = ""
    trace_dir: str = ""
    db_dir: str = ""


def main() -> int:
    ap = argparse.ArgumentParser(description="orchestrator 全路径端到端验证")
    ap.add_argument("pdf", help="古籍 PDF 路径")
    ap.add_argument("--book-code", default="ORCH-E2E")
    ap.add_argument("--pages", type=int, default=5, help="处理页数")
    ap.add_argument("--db-dir", default="e2e_orch_db")
    ap.add_argument("--cross-check", action="store_true", help="启用成功页 cross-check")
    ap.add_argument("--sample-rate", type=float, default=0.0, help="共识错误抽样率")
    args = ap.parse_args()

    if not os.path.isfile(args.pdf):
        print(f"[ERR] PDF 不存在: {args.pdf}", file=sys.stderr)
        return 2

    os.makedirs(args.db_dir, exist_ok=True)

    # ── 注册引擎 ──
    print("[info] 加载 PaddleOCRAdapter（Tier1）...")
    paddle = PaddleOCRAdapter()
    print("[info] 加载 RapidOCRAdapter（Tier2）...")
    rapid = RapidOCRAdapter()

    reg = EngineRegistry(benchmark_dir=os.path.join(args.db_dir, "benchmark"))
    reg.register_adapter(
        AdapterMeta(name="paddleocr", label="PaddleOCR PP-OCRv6", tier=1, batch_capable=False),
        EngineConfig(), adapter=paddle,
    )
    reg.register_adapter(
        AdapterMeta(name="rapidocr", label="RapidOCR", tier=2, requires_network=False),
        EngineConfig(), adapter=rapid,
    )

    cfg = E2EConfig(
        max_pages=args.pages,
        db_dir=args.db_dir,
    )
    overrides = EngineOverrides(
        enable_cross_check=args.cross_check,
        consensus_sample_rate=args.sample_rate,
    )

    print(f"[info] 编排参数: pages={args.pages} cross_check={args.cross_check} sample_rate={args.sample_rate}")
    print(f"[info] 启动 orchestrate_book ...")
    import time
    t0 = time.time()
    result = orchestrate_book(args.pdf, args.book_code, cfg, reg, overrides=overrides)
    elapsed = time.time() - t0

    total = len(result.pages) + len(result.failed_pages) + len(result.uncertain_pages)
    print(f"\n=== 编排完成 ({elapsed:.0f}s) ===")
    print(f"  成功: {len(result.pages)}  失败: {len(result.failed_pages)}  不确定: {len(result.uncertain_pages)}  共: {total}")
    if result.failed_pages:
        print(f"  失败页: {list(result.failed_pages.keys())}")
    if result.uncertain_pages:
        print(f"  不确定页: {list(result.uncertain_pages.keys())}")

    # 检查 cross_divergence 表
    from kzocr.storage.db import BookDB
    db = BookDB(args.book_code, db_dir=args.db_dir)
    divs = db.get_cross_divergences()
    if divs:
        high = [d for d in divs if d["priority"] == "high"]
        print(f"  跨引擎分歧: {len(divs)} (high {len(high)})")
    anomalies = db.get_anomalies()
    if anomalies:
        print(f"  异常记录: {len(anomalies)}")
    db.close()
    return 0


if __name__ == "__main__":
    from dataclasses import dataclass
    raise SystemExit(main())
