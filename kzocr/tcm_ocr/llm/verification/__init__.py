"""
字形验证模块。
"""

from kzocr.tcm_ocr.llm.verification.glyph_verifier import GlyphVerifier
from kzocr.tcm_ocr.llm.verification.critical_fields import (
    get_critical_fields,
    is_disaster_field,
    get_glyph_candidates_for_char,
    get_field_weight,
    PatternCache,
)

__all__ = [
    "GlyphVerifier",
    "get_critical_fields",
    "is_disaster_field",
    "get_glyph_candidates_for_char",
    "get_field_weight",
    "PatternCache",
]
