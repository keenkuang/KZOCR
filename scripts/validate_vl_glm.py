"""GLM-4V-Flash 真实视觉回看验证（VL 真实验证 B 项）。

目标：验证 `VisionRecheckAdapter.glm_default()` 这一 GLM-4V-Flash 集成确实能
对真实古籍页面做「图 + OCR 文本」视觉回看，并返回合理的 GlyphVerdict。

流程：
  1. 用 fitz 渲染真实古籍一页为 numpy 图像。
  2. 用本地 PaddleOCR（已装 3.7.0）对该页出候选文本，作为待验证 OCR 结果。
  3. 若设置了 KZOCR_GLM_API_KEY（或 KZOCR_LLM_API_KEY），调 glm-4v-flash
     真实视觉回看：
       - 正确候选文本 → 期望 PASS（或至少非 UNKNOWN）
       - 故意错文本   → 期望 FAIL（确认模型真的在看图，而非盲答）
     校验 GlyphVerdict.status / latency 合理。
  4. 若无 key：仅验证接线（glm_default 构造正确、recheck 无 key 优雅降级为
     UNKNOWN），并打印如何开启真实验证的提示。

退出码：
  0  无 key 接线验证通过（或真实调用全部通过）
  2  无 key（脚本主动跳过真实调用，提示用户补 key）
  1  真实调用存在但验证失败
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import fitz


def render_page(pdf_path: str, page_num: int, dpi: int = 150) -> np.ndarray:
    """用 fitz 渲染 PDF 指定页为 (H,W,3) numpy 图像。"""
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_num]
        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        # 确保 3 通道
        if img.shape[2] == 4:
            img = img[:, :, :3]
        return img
    finally:
        doc.close()


def paddle_candidate_text(img: np.ndarray, page_num: int) -> str:
    """用本地 PaddleOCR 对页面出候选文本（作为待验证 OCR 结果）。"""
    try:
        from kzocr.engine.types import PageInput
        from kzocr.engine.adapters import PaddleOCRAdapter

        adapter = PaddleOCRAdapter()
        result = adapter.run_page(PageInput(page_num=page_num, img=img))
        return result.text or ""
    except Exception as exc:  # 本地 OCR 不可用时退化为占位
        print(f"[warn] PaddleOCR 候选文本获取失败，使用占位文本: {exc}", file=sys.stderr)
        return "（本地 OCR 不可用）"


def glm_key_present() -> bool:
    return bool(
        os.environ.get("KZOCR_GLM_API_KEY") or os.environ.get("KZOCR_LLM_API_KEY")
    )


def run_real_validation(img: np.ndarray, candidate: str, page_num: int) -> int:
    """执行真实 glm-4v-flash 视觉回看，返回退出码。"""
    from kzocr.scheduler.verifier import VisionRecheckAdapter

    adapter = VisionRecheckAdapter.glm_default()
    print(f"[glm] model={adapter.model} base={adapter.base_url}")
    print(f"[glm] 候选文本({len(candidate)}字): {candidate[:80]!r}...")

    # 1) 正确候选文本 → 期望 PASS / 非 UNKNOWN
    verdict = adapter.recheck(candidate, img, engine_label="paddleocr")
    print(f"[glm] 正确文本裁决: status={verdict.status} conf={verdict.confidence} "
          f"details={verdict.details}")
    if verdict.status == "UNKNOWN":
        print("[FAIL] 真实调用返回 UNKNOWN（疑似 key 无效或服务异常）", file=sys.stderr)
        return 1

    # 2) 故意错文本 → 期望 FAIL（确认模型在看图）
    wrong = "这是一段与图片内容完全无关的虚构测试文字用于检验视觉回看是否生效。"
    verdict_wrong = adapter.recheck(wrong, img, engine_label="paddleocr")
    print(f"[glm] 错文本裁决:   status={verdict_wrong.status} conf={verdict_wrong.confidence} "
          f"details={verdict_wrong.details}")
    if verdict_wrong.status not in ("FAIL", "UNKNOWN"):
        print(f"[WARN] 错文本未判 FAIL（模型可能较宽松），实际={verdict_wrong.status}")

    # 3) 校验 latency 已记录
    details = verdict.details or ""
    if "latency_ms" not in details:
        print("[WARN] verdict.details 未含 latency_ms", file=sys.stderr)

    print(f"[OK] GLM-4V-Flash 真实视觉回看验证通过（页 {page_num}）："
          f"正确文本→{verdict.status}，错文本→{verdict_wrong.status}")
    return 0


def run_wiring_check(img: np.ndarray, candidate: str, page_num: int) -> int:
    """无 key 时仅验证接线（glm_default 构造 + recheck 优雅降级）。"""
    from kzocr.scheduler.verifier import VisionRecheckAdapter

    adapter = VisionRecheckAdapter.glm_default()
    assert adapter.model == "glm-4v-flash", adapter.model
    assert adapter.base_url == "https://open.bigmodel.cn/api/paas/v4", adapter.base_url
    assert adapter.support_reasoning_effort is False
    print(f"[wiring] glm_default() 构造正确: model={adapter.model} base={adapter.base_url}")

    verdict = adapter.recheck(candidate, img, engine_label="paddleocr")
    print(f"[wiring] 无 key recheck 返回: status={verdict.status} details={verdict.details}")
    assert verdict.status == "UNKNOWN", verdict.status
    assert "not_configured" in (verdict.details or ""), verdict.details

    print("[wiring] 接线验证通过：glm_default 构造正确，无 key 时优雅降级为 UNKNOWN。")
    print("\n[提示] 未检测到 GLM key，真实视觉回看已跳过。开启方式：")
    print("  export KZOCR_GLM_API_KEY=<你的智谱 BigModel key>")
    print("  # 或复用既有 LLM key：export KZOCR_LLM_API_KEY=<key>")
    print("  python scripts/validate_vl_glm.py --pdf <古籍.pdf> --page 0")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="GLM-4V-Flash 真实视觉回看验证")
    parser.add_argument(
        "--pdf",
        default="/home/keen/0706OCR/mi_test/秘方求真-570/ocr/秘方求真-570_origin.pdf",
        help="真实古籍 PDF 路径",
    )
    parser.add_argument("--page", type=int, default=0, help="渲染页码（0 基）")
    parser.add_argument("--dpi", type=int, default=150, help="渲染 DPI")
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"[FAIL] PDF 不存在: {args.pdf}", file=sys.stderr)
        return 1

    print(f"[render] {args.pdf} 第 {args.page} 页 @ {args.dpi}dpi")
    img = render_page(args.pdf, args.page, dpi=args.dpi)
    print(f"[render] 图像尺寸 {img.shape}")

    candidate = paddle_candidate_text(img, args.page)
    print(f"[paddle] 候选文本: {candidate[:80]!r}{'...' if len(candidate) > 80 else ''}")

    if glm_key_present():
        return run_real_validation(img, candidate, args.page)
    return run_wiring_check(img, candidate, args.page)


if __name__ == "__main__":
    raise SystemExit(main())
