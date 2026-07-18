"""PaddleOCR 弃用告警回归测试。

PaddleOCR ≥3.7 弃用 ``engine.ocr()``（建议改用 ``predict``），构造器旧参数
``use_angle_cls`` / ``rec_batch_num`` 等亦弃用。一旦有人把适配器改回旧 API，
这些调用会在运行时抛 DeprecationWarning。本模块用两层测试防止回退：

1. ``test_adapter_source_uses_predict_not_ocr``（静态，无需引擎，CI 可跑）：
   直接检查 ``kzocr/engine/adapters.py`` 源码不含 ``engine.ocr(`` 调用，
   作为 CI 中最廉价、最确定的回归护栏。
2. ``test_paddleocr_adapter_no_deprecation_warning``（动态，需 paddleocr）：
   用真实引擎跑一页，断言不触发任何 PaddleOCR 弃用告警。

动态测试依赖 paddleocr 已安装（CI 不装，经 ``importorskip`` 跳过）。
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path

import numpy as np
import pytest

# PaddleOCR 弃用告警的消息特征（实测）：
#   .ocr()            → "Please use `predict` instead."
#   旧构造参数        → "The parameter `use_angle_cls` has been deprecated..."
#                     → "The parameter `rec_batch_num` has been deprecated..."
_PADDLEOCR_DEPRECATION_MARKERS = (
    "Please use `predict`",  # engine.ocr() 弃用
    "has been deprecated",   # 旧构造参数（use_angle_cls / rec_batch_num 等）
)

_ADAPTER_SRC = (
    Path(__file__).resolve().parent.parent / "kzocr" / "engine" / "adapters.py"
)


def _is_paddleocr_deprecation(warning) -> bool:
    return issubclass(warning.category, DeprecationWarning) and any(
        m in str(warning.message) for m in _PADDLEOCR_DEPRECATION_MARKERS
    )


def test_adapter_source_uses_predict_not_ocr():
    """源码不得再出现 engine.ocr( 调用（CI 护栏，无需引擎）。"""
    src = _ADAPTER_SRC.read_text(encoding="utf-8")
    # 仅匹配 engine 变量上的 .ocr( 调用（tcm_ocr 平行栈用 self.ocr.ocr，不在此文件）
    assert re.search(r"engine\.ocr\(", src) is None, (
        "kzocr/engine/adapters.py 仍含 engine.ocr( 调用，"
        "PaddleOCR ≥3.7 已弃用 .ocr()，应改用 engine.predict()。"
    )


def test_paddleocr_adapter_no_deprecation_warning():
    """真实引擎跑一页，不得触发任何 PaddleOCR 弃用告警。

    回归防护：run_page 改回 engine.ocr(...) 或 _get_engine 传回
    use_angle_cls/rec_batch_num 等弃用参数时，本测试失败。
    """
    pytest.importorskip("paddleocr")
    from kzocr.engine.adapters import PaddleOCRAdapter
    from kzocr.engine.types import PageInput

    # 合成古籍页：白底 + 若干黑色"文字行"，足以触发检测/识别管线
    img = np.full((1200, 900, 3), 255, dtype=np.uint8)
    for y in range(120, 1120, 64):
        img[y : y + 10, 140 : 760, :] = 0

    # 强制重置进程级单例，确保构造器路径也被检查
    PaddleOCRAdapter._engine_global = None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        adapter = PaddleOCRAdapter()
        result = adapter.run_page(PageInput(page_num=0, img=img))

    paddle_deprecations = [w for w in caught if _is_paddleocr_deprecation(w)]
    assert not paddle_deprecations, (
        "检测到 PaddleOCR 弃用告警："
        + "; ".join(str(w.message) for w in paddle_deprecations)
    )
    assert result is not None
