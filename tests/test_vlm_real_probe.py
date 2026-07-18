"""GLM-4V-Flash 真实视觉回看（VL 真实验证 B 项）回归测试。

CI 安全：无 GLM key 时仅验证接线（glm_default 构造 + 无 key 优雅降级为 UNKNOWN），
不发起真实网络调用。KEY=KZOCR_GLM_API_KEY 或 KZOCR_LLM_API_KEY 存在时，
`test_recheck_real_call` 才会发起真实 glm-4v-flash 调用。
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from kzocr.scheduler.verifier import VisionRecheckAdapter

_GLM_KEY_PRESENT = bool(
    os.environ.get("KZOCR_GLM_API_KEY") or os.environ.get("KZOCR_LLM_API_KEY")
)

# 真实古籍 PDF（本地可用，CI 无该路径时跳过真实渲染相关用例）
_REAL_PDF = "/home/keen/0706OCR/mi_test/秘方求真-570/ocr/秘方求真-570_origin.pdf"


def test_glm_default_constructs_correctly():
    """glm_default() 应构造出 zhipu glm-4v-flash 适配器（无 key 也成立）。"""
    adapter = VisionRecheckAdapter.glm_default()
    assert adapter.model == "glm-4v-flash"
    assert adapter.base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert adapter.support_reasoning_effort is False
    assert adapter.max_tokens_cap == 1024


def test_recheck_no_key_returns_unknown():
    """无 GLM key 时 recheck 应优雅降级为 UNKNOWN（detail=not_configured）。"""
    adapter = VisionRecheckAdapter.glm_default()
    # 强制清空 key 以稳定测试
    adapter.api_key = ""
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    verdict = adapter.recheck("测试文本", img)
    assert verdict.status == "UNKNOWN"
    assert "not_configured" in (verdict.details or "")


def test_glm_default_prefers_kzocr_glm_key(monkeypatch):
    """glm_default 应优先读 KZOCR_GLM_API_KEY，回退 KZOCR_LLM_API_KEY。"""
    # 清空两个 key，仅设置 LLM key → 应回退到 LLM key
    monkeypatch.delenv("KZOCR_GLM_API_KEY", raising=False)
    monkeypatch.delenv("KZOCR_LLM_API_KEY", raising=False)
    monkeypatch.setenv("KZOCR_LLM_API_KEY", "test-llm-key")
    adapter = VisionRecheckAdapter.glm_default()
    assert adapter.api_key == "test-llm-key"

    # 同时设置 GLM key → 应优先取 GLM key
    monkeypatch.setenv("KZOCR_GLM_API_KEY", "test-glm-key")
    adapter2 = VisionRecheckAdapter.glm_default()
    assert adapter2.api_key == "test-glm-key"


@pytest.mark.skipif(not _GLM_KEY_PRESENT, reason="未设置 KZOCR_GLM_API_KEY / KZOCR_LLM_API_KEY，跳过真实调用")
@pytest.mark.skipif(not os.path.exists(_REAL_PDF), reason="本地无真实古籍 PDF，跳过真实渲染")
def test_recheck_real_call():
    """真实 glm-4v-flash 调用：正确文本应判 PASS/非 UNKNOWN，且记录 latency。"""
    import fitz

    from kzocr.engine.adapters import PaddleOCRAdapter
    from kzocr.engine.types import PageInput

    doc = fitz.open(_REAL_PDF)
    try:
        pix = doc[0].get_pixmap(dpi=150)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if img.shape[2] == 4:
            img = img[:, :, :3]
    finally:
        doc.close()

    candidate = PaddleOCRAdapter().run_page(PageInput(page_num=0, img=img)).text or ""

    adapter = VisionRecheckAdapter.glm_default()
    verdict = adapter.recheck(candidate, img, engine_label="paddleocr")

    assert verdict.status in ("PASS", "FAIL"), verdict.status
    assert "latency_ms" in (verdict.details or ""), verdict.details
    print(f"[real] 正确文本→{verdict.status} ({verdict.details})")

    wrong = "这是一段与图片内容完全无关的虚构测试文字用于检验视觉回看是否生效。"
    verdict_wrong = adapter.recheck(wrong, img, engine_label="paddleocr")
    assert verdict_wrong.status in ("FAIL", "UNKNOWN"), verdict_wrong.status
    print(f"[real] 错文本→{verdict_wrong.status} ({verdict_wrong.details})")
