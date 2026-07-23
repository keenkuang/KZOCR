#!/usr/bin/env python3
"""Benchmark OvisOCR2 quantization variants and compare recognition divergence.

Runs the local OvisOCR2 GGUF (served via llama.cpp ``llama-server``) at several
quantizations (F16 / Q8_0 / Q4_K_M) over a fixed small image set, measuring
speed (server startup + per-page latency + tokens/s) and transcription text.
It also runs PaddleOCR PP-OCRv6 medium on the same images, then computes the
recognition divergence rate between every pair of engines using
``kzocr.scheduler.cross_align.align_engines``.

The script auto-spawns and tears down one ``llama-server`` per quantization
(rotating ports), reusing the known local GGUF + mmproj paths.

Usage:
  python scripts/bench_ovisocr2.py
  python scripts/bench_ovisocr2.py --quants Q8_0 Q4_K_M   # skip F16
  python scripts/bench_ovisocr2.py --images a.png b.png
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np

from kzocr.engine.adapters import OvisOCR2Adapter, PaddleOCRAdapter
from kzocr.engine.types import PageInput
from kzocr.scheduler.cross_align import align_engines, load_confusion_set

# ── Local artifact paths (overridable via env) ──────────────────────────────
_ZFS400 = os.environ.get("KZOCR_ZFS400", "/media/keen/ZFS400")
QUANTS = {
    "F16": os.path.join(_ZFS400, "OvisOCR2-F16.gguf"),
    "Q8_0": os.path.join(_ZFS400, "OvisOCR2-Q8_0(1).gguf"),
    "Q4_K_M": os.path.join(_ZFS400, "OvisOCR2-Q4_K_M.gguf"),
}
MMPROJ = os.environ.get("KZOCR_OVISOCR2_MMPROJ", os.path.join(_ZFS400, "mmproj-F16.gguf"))
PADDLE_MED_REC = os.environ.get(
    "KZOCR_PADDLE_MED_REC",
    "/home/keen/.paddlex/official_models/PP-OCRv6_medium_rec",
)
PADDLE_MED_DET = os.environ.get(
    "KZOCR_PADDLE_MED_DET",
    "/home/keen/.paddlex/official_models/PP-OCRv6_medium_det",
)
IMG_DIR = "/home/keen/Documents/OCR0625/mi-by-ppocrv6/images"
DEFAULT_IMAGES = [f"page_{p:04d}.png" for p in (1, 5, 10, 15, 20, 25, 30)]
PORT_BASE = int(os.environ.get("KZOCR_OVISOCR2_PORT", "18088"))
PADDLE_ENGINE_NAME = "PaddleOCR-v6-medium"


def load_image_rgb(path: str) -> np.ndarray:
    from PIL import Image

    return np.array(Image.open(path).convert("RGB"))


def ocr_ovis(quant: str, model_path: str, images: list[str],
             max_tokens: int, timeout: int, port: int) -> dict:
    """Spawn a llama-server for one quant, OCR all images, tear it down."""
    adapter = OvisOCR2Adapter(
        auto_spawn=True,
        model_path=model_path,
        mmproj_path=MMPROJ,
        server_port=port,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    out: dict = {"startup_s": 0.0, "per_image": {}}
    try:
        out["startup_s"] = round(adapter._server._start_elapsed, 3) if hasattr(adapter, "_server") and adapter._server else 0.0
        for img_path in images:
            img = load_image_rgb(img_path)
            t0 = time.time()
            res = adapter.run_page(PageInput(page_num=0, img=img))
            dt = time.time() - t0
            out["per_image"][os.path.basename(img_path)] = {
                "text": res.text,
                "sec": round(dt, 3),
            }
    finally:
        adapter.close()
    return out


def ocr_paddle(images: list[str], max_tokens: int) -> dict:
    """Run PaddleOCR PP-OCRv6 medium over the images (single model load)."""
    adapter = PaddleOCRAdapter(rec_model_dir=PADDLE_MED_REC, det_model_dir=PADDLE_MED_DET)
    out: dict = {"per_image": {}}
    for img_path in images:
        img = load_image_rgb(img_path)
        t0 = time.time()
        res = adapter.run_page(PageInput(page_num=0, img=img))
        dt = time.time() - t0
        out["per_image"][os.path.basename(img_path)] = {
            "text": res.text,
            "sec": round(dt, 3),
        }
    return out


def divergence_stats(texts_a: dict, texts_b: dict, confusion_set: dict) -> dict:
    """Pairwise divergence between two engines' per-image texts."""
    total_div = 0
    total_chars = 0
    pages = 0
    per_page = []
    for name in texts_a:
        a = texts_a.get(name, "")
        b = texts_b.get(name, "")
        if not a and not b:
            continue
        divs = align_engines(a, b, confusion_set=confusion_set)
        n_div = len(divs)
        n_char = max(len(a), len(b))
        total_div += n_div
        total_chars += n_char
        pages += 1
        per_page.append(n_div)
    avg_page = total_div / pages if pages else 0.0
    rate_1k = (total_div / total_chars * 1000.0) if total_chars else 0.0
    return {
        "total_div": total_div,
        "pages": pages,
        "avg_div_per_page": round(avg_page, 2),
        "div_per_1k_chars": round(rate_1k, 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="OvisOCR2 quantization benchmark + divergence")
    ap.add_argument("--quants", nargs="+", default=list(QUANTS.keys()),
                    help="quantization names to test (subset of F16/Q8_0/Q4_K_M)")
    ap.add_argument("--images", nargs="+", default=None,
                    help="image files (default: fixed 7-page sample under OCR0625)")
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--port-base", type=int, default=PORT_BASE)
    ap.add_argument("--no-paddle", action="store_true", help="skip PaddleOCR medium comparison")
    ap.add_argument("--out", default="e2e_expand/bench_ovisocr2.json")
    args = ap.parse_args()

    images = args.images or [os.path.join(IMG_DIR, n) for n in DEFAULT_IMAGES]
    missing = [p for p in images if not os.path.isfile(p)]
    if missing:
        print(f"[ERR] missing images: {missing}")
        return 2

    confusion_set = load_confusion_set()
    results: dict = {}

    # OvisOCR2 per quant
    for i, q in enumerate(args.quants):
        if q not in QUANTS:
            print(f"[warn] unknown quant {q}, skip")
            continue
        model_path = QUANTS[q]
        if not os.path.isfile(model_path):
            print(f"[warn] model missing: {model_path}, skip {q}")
            continue
        port = args.port_base + i
        print(f"\n=== OvisOCR2 {q} (port {port}) ===", flush=True)
        results[f"OvisOCR2-{q}"] = ocr_ovis(q, model_path, images, args.max_tokens, args.timeout, port)

    # PaddleOCR medium
    if not args.no_paddle:
        print(f"\n=== {PADDLE_ENGINE_NAME} ===", flush=True)
        results[PADDLE_ENGINE_NAME] = ocr_paddle(images, args.max_tokens)

    # ── Speed report ──
    print("\n=== Speed ===")
    print(f"{'engine':<24}{'startup_s':>11}{'avg_s/page':>13}{'pages':>7}")
    for name, r in results.items():
        startup = r.get("startup_s", 0.0)
        secs = [v["sec"] for v in r["per_image"].values()]
        avg = sum(secs) / len(secs) if secs else 0.0
        print(f"{name:<24}{startup:>11.1f}{avg:>13.2f}{len(secs):>7}")

    # ── Divergence matrix ──
    engines = list(results.keys())
    print("\n=== Recognition divergence (avg div/page, div/1k chars) ===")
    header = "pair".ljust(46) + "div/page".rjust(10) + "per1k".rjust(8)
    print(header)
    matrix = {}
    for i in range(len(engines)):
        for j in range(i + 1, len(engines)):
            a, b = engines[i], engines[j]
            ta = {k: v["text"] for k, v in results[a]["per_image"].items()}
            tb = {k: v["text"] for k, v in results[b]["per_image"].items()}
            st = divergence_stats(ta, tb, confusion_set)
            matrix[f"{a}||{b}"] = st
            label = f"{a} vs {b}"
            print(f"{label:<46}{st['avg_div_per_page']:>10.1f}{st['div_per_1k_chars']:>8.1f}")

    # ── Persist ──
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    payload = {
        "images": [os.path.basename(p) for p in images],
        "speed": {
            name: {
                "startup_s": r.get("startup_s", 0.0),
                "avg_s_page": (
                    round(sum(v["sec"] for v in r["per_image"].values()) / len(r["per_image"]), 3)
                    if r["per_image"] else 0.0
                ),
                "per_image_sec": {k: v["sec"] for k, v in r["per_image"].items()},
            }
            for name, r in results.items()
        },
        "divergence_matrix": matrix,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\n[done] report -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
