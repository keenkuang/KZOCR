"""与 OCR 引擎无关的归一化数据结构。

kimi 引擎（tcm_ocr）输出 → 这些结构 → 适配器写入 zai 控制台数据库。
这样 KZOCR 编排层不依赖任何具体引擎实现，便于 mock 与真实引擎切换。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EngineResult:
    engine: str            # mineru / ppocr / unirec / doctr / tesseract / paddle_vl / dots_ocr
    text: str
    confidence: float = 0.9
    latency_ms: int = 0


@dataclass
class ProofreadRecord:
    original_text: str
    corrected_text: str
    change_type: str = "glyph"   # glyph/dosage/negation/herb/meridian/other
    severity: str = "info"       # info/warning/critical
    notes: Optional[str] = None
    triggered_pattern: Optional[str] = None


@dataclass
class LineResult:
    sequence_in_paragraph: int = 1
    engine_texts: dict[str, str] = field(default_factory=dict)  # {mineru, ppocr, engine3, engine4}
    consensus: Optional[str] = None        # rawVoteText
    llm_corrected: Optional[str] = None    # llmCorrectedText
    glyph_verified: Optional[str] = None    # glyphVerifiedText
    final: Optional[str] = None             # finalText
    human_final: Optional[str] = None       # humanFinalText（人工终校后填入）
    confidence: float = 0.9
    engine_results: list[EngineResult] = field(default_factory=list)
    proofreads: list[ProofreadRecord] = field(default_factory=list)
    disputed: bool = False
    missing_char_alert: Optional[str] = None
    extra_char_alert: Optional[str] = None
    char_level_json: Optional[str] = None


@dataclass
class ParagraphResult:
    sequence_in_page: int = 1
    node_type: str = "text"   # text/heading/formula/list_item/quote
    is_heading: bool = False
    heading_level: Optional[int] = None
    is_formula: bool = False
    lines: list[LineResult] = field(default_factory=list)


@dataclass
class PageResult:
    page_num: int
    layout_type: str = "text"   # text/table/multi_column/formula_list
    paragraphs: list[ParagraphResult] = field(default_factory=list)


@dataclass
class HerbPattern:
    correct_name: str
    ocr_error_pattern: str
    pattern_type: str = "glyph_shape"
    is_toxic: bool = False
    severity: str = "critical"
    source_books: Optional[str] = None   # JSON 数组字符串
    evidence_count: int = 1


@dataclass
class MeridianPattern:
    correct_name: str
    ocr_error_pattern: str
    entity_type: str = "meridian"   # meridian/point/extra_point
    meridian_belonging: Optional[str] = None
    body_region: Optional[str] = None
    severity: str = "critical"
    source_books: Optional[str] = None
    evidence_count: int = 1


@dataclass
class ContextPattern:
    pattern_text: str
    pattern_type: str   # same_as_above/add_to_above/subtract_from/cross_page_continued
    regex: Optional[str] = None
    example: Optional[str] = None
    discovered_count: int = 1
    source_books: Optional[str] = None


@dataclass
class TermEntry:
    term_name: str
    sublib: str        # 方剂/中药/经络/穴位/病证/治法...
    error_pattern: Optional[str] = None
    correct_form: Optional[str] = None
    scope: str = "global"   # global/publisher/book/era
    scope_score: int = 1
    confidence: float = 0.9


@dataclass
class FormulaIngredient:
    herb_name: str
    dosage_value: Optional[str] = None
    unit: Optional[str] = None
    role_in_formula: Optional[str] = None  # 君/臣/佐/使
    is_toxic: bool = False


@dataclass
class FormulaEntry:
    formula_name: str
    ingredients: list[FormulaIngredient] = field(default_factory=list)


@dataclass
class BookResult:
    book_code: str
    title: str
    author: str = "佚名"
    publisher: str = "未知"
    pub_year: int = 2000
    pub_era: str = "laser"          # lead_print/transition/laser
    book_type: str = "formula"      # formula/clinical/classic/textbook
    pages: list[PageResult] = field(default_factory=list)
    herb_patterns: list[HerbPattern] = field(default_factory=list)
    meridian_patterns: list[MeridianPattern] = field(default_factory=list)
    context_patterns: list[ContextPattern] = field(default_factory=list)
    terms: list[TermEntry] = field(default_factory=list)
    formulas: list[FormulaEntry] = field(default_factory=list)
    final_markdown: str = ""        # 导出文档（人工终校后填充）

    # 元信息：本次结果来自哪个引擎 / 是否 mock
    engine_label: str = "unknown"
    is_mock: bool = False
