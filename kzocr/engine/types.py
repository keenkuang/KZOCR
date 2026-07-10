"""与 OCR 引擎无关的归一化数据结构。

kimi 引擎（tcm_ocr）输出 → 这些结构 → 适配器写入 zai 控制台数据库。
这样 KZOCR 编排层不依赖任何具体引擎实现，便于 mock 与真实引擎切换。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Protocol

import numpy as np


# ── 字形校验状态（B1 裁决：枚举，不占用 glyph_verified 文本列）──
GlyphStatus = Literal["PASS", "RARE", "UNKNOWN", "FAIL", "UNCERTAIN"]

# ── 引擎健康状态(v0.7 设计 §3.1,EngineRegistration.status 引用)──
EngineStatus = Literal["HEALTHY", "DEGRADED", "UNAVAILABLE"]


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
    glyph_verified: Optional[str] = None    # glyphVerifiedText（B1：保留为文本）
    glyph_status: Optional[GlyphStatus] = None  # B1：字形校验枚举，不占用文本列
    final: Optional[str] = None             # finalText
    human_final: Optional[str] = None       # humanFinalText（人工终校后填入）
    confidence: float = 0.9
    engine_results: list[EngineResult] = field(default_factory=list)
    proofreads: list[ProofreadRecord] = field(default_factory=list)
    disputed: bool = False
    missing_char_alert: Optional[str] = None
    extra_char_alert: Optional[str] = None
    char_level_json: Optional[str] = None
    crop_img_path: Optional[str] = None      # B7：裁剪图引用（存路径不存像素）


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


# ── 引擎探测结果（K3/N2 裁决：显式字段表，支持 select_adapters 确定性测试）──
@dataclass
class ProbeResult:
    """运行环境探测结果，由 probe_environment() 返回，可注入用于 CI 确定性测试。"""
    gpu: bool = False
    vram_gb: float = 0.0
    cpu_cores: int = 1
    ports: dict[str, bool] = field(default_factory=dict)     # {"18080": True, ...}
    keys: dict[str, bool] = field(default_factory=dict)      # {"sensenova": True, ...} 仅存 key 是否存在,不存明文值(对齐 v0.7 §3.3)
    allow_cloud_vision: bool = False


# ── 适配器元信息（B2 裁决：每适配器一份，注册用）──
AdapterKind = Literal["page", "book"]


@dataclass
class AdapterMeta:
    """适配器注册元信息，决定 select_adapters 的候选排序和过滤。"""
    name: str                                      # paddleocr, rapidocr, sensenova...
    label: str                                     # 对外显示名 "PaddleOCR", "SenseNova"
    kind: AdapterKind = "page"                     # page=页级(page), book=书级(BookPipeline shim)
    tier: int = 1                                  # Tier 归属 (1/2/3)
    batch_capable: bool = False                    # 是否支持书级输入 (BookPipeline)
    supports_confidence: bool = True
    supports_context: bool = False
    min_vram_gb: float = 0.0
    default_enabled: bool = True
    requires_gpu: bool = False
    requires_network: bool = False
    probe: dict = field(default_factory=dict)      # 探测配置


# ── 引擎统一执行协议（v0.7）──
class EngineRunner(Protocol):
    """引擎统一执行接口。"""

    def run_page(self, page: "PageInput") -> "AdapterPageResult":
        """页级执行：输入单页图像，返回归一化结果。"""
        ...

    def run_book(self, pdf_path: str) -> "BookResult":
        """书级执行：输入 PDF 路径，返回全书结果。仅在 kind='book' 时支持。"""
        ...


@dataclass
class PageInput:
    """引擎输入：渲染后的单页数据。"""
    page_num: int
    img: np.ndarray              # rendered page image (H,W,3);对齐 v0.7 设计 §6.1(ARCH-2)
    layout: "PageLayout | None" = None
    context: Optional[str] = None    # previous page bottom 15% text


@dataclass
class PageLayout:
    """页面版式信息。由渲染阶段的光学布局分析得出。"""
    page_num: int
    orientation: str = "horizontal"   # "horizontal" | "vertical" | "mixed"
    is_vertical: bool = False
    estimated_lines: int = 0


@dataclass
class EngineCallRecord:
    """单次引擎调用的完整记录。用于 trace 和运维排障。"""
    page: int
    tier: int
    engine: str
    latency_ms: float
    glyph_status: Optional[GlyphStatus] = None
    error: Optional[str] = None


@dataclass
class EngineConfig:
    """引擎配置类型。用于 EngineRegistration.config，替代裸 dict。
    只存环境变量名引用，不存明文凭证。"""
    api_key_env: Optional[str] = None   # env var name, NOT the key value
    base_url: Optional[str] = None
    extra: dict = field(default_factory=dict)


# ── 适配器统一输出（B2 裁决：所有适配器 return 此结构，不得自行折算 LineResult）──
@dataclass
class AdapterPageResult:
    """适配器单页识别结果，由 adapter_to_line_result() 统一折算为 LineResult。"""
    text: str
    confidence: float = 0.9
    char_confidences: Optional[list[float]] = None   # 字级置信度，与 text 等长（None=不支持）
    crop_img_path: Optional[str] = None              # B7：裁剪图路径引用，不存像素
    meta: Optional[AdapterMeta] = None


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
    failed_pages: dict[int, str] = field(default_factory=dict)  # D2: 失败页记录 {page_num: reason}
    final_markdown: str = ""        # 导出文档（人工终校后填充）

    # 元信息：本次结果来自哪个引擎 / 是否 mock
    engine_label: str = "unknown"
    is_mock: bool = False
