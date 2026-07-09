"""Mock 引擎：在无重依赖（MinerU/PaddleOCR/torch/LLM）环境下产生一份结构合理的
TCM OCR 结果，使 适配器→zai 库→导出→kHUB 推送 全链路可端到端验证。

真实引擎见 `run.py`（调用 kimi 的 tcm_ocr.pipeline.book_pipeline.BookPipeline）。
"""
from __future__ import annotations

import time
from kzocr.engine.types import (
    BookResult, PageResult, ParagraphResult, LineResult, EngineResult,
    ProofreadRecord, HerbPattern, MeridianPattern, ContextPattern, TermEntry,
    FormulaEntry, FormulaIngredient,
)


def mock_book_result(book_code: str = "TCM-MOCK-001") -> BookResult:
    # 多引擎对同一行的识别差异（模拟 OCR 易错：白术→白木、足三里→足三裹）
    lines_p1 = [
        LineResult(
            sequence_in_paragraph=1,
            engine_texts={
                "mineru": "方用白术三钱，茯苓二钱。",
                "ppocr": "方用白木三钱，茯苓二钱。",
                "engine3": "方用白术三钱，茯苓二钱。",
                "engine4": "方用白木三钱，茯芩二钱。",
            },
            consensus="方用白术三钱，茯苓二钱。",
            llm_corrected="方用白术三钱，茯苓二钱。",
            glyph_verified="方用白术三钱，茯苓二钱。",
            final="方用白术三钱，茯苓二钱。",
            confidence=0.96,
            engine_results=[
                EngineResult("mineru", "方用白术三钱，茯苓二钱。", 0.97, 120),
                EngineResult("ppocr", "方用白木三钱，茯苓二钱。", 0.91, 80),
                EngineResult("engine3", "方用白术三钱，茯苓二钱。", 0.95, 200),
                EngineResult("engine4", "方用白木三钱，茯芩二钱。", 0.88, 150),
            ],
            crop_img_path=f"/mnt/source/{book_code}/page_001_line_001.png",
            proofreads=[
                ProofreadRecord(
                    original_text="方用白木三钱，茯苓二钱。",
                    corrected_text="方用白术三钱，茯苓二钱。",
                    change_type="herb",
                    severity="critical",
                    notes="白木→白术（glyph_shape）",
                    triggered_pattern="HERB-白术",
                )
            ],
        ),
        LineResult(
            sequence_in_paragraph=2,
            engine_texts={
                "mineru": "每日一剂，水煎服。",
                "ppocr": "每日一剂，水煎服。",
                "engine3": "每日一剂，水煎服。",
                "engine4": "每日一剂，水前服。",
            },
            consensus="每日一剂，水煎服。",
            llm_corrected="每日一剂，水煎服。",
            glyph_verified="每日一剂，水煎服。",
            final="每日一剂，水煎服。",
            confidence=0.94,
            engine_results=[
                EngineResult("mineru", "每日一剂，水煎服。", 0.96, 110),
                EngineResult("ppocr", "每日一剂，水煎服。", 0.93, 70),
                EngineResult("engine3", "每日一剂，水煎服。", 0.95, 190),
                EngineResult("engine4", "每日一剂，水前服。", 0.9, 140),
            ],
        ),
    ]
    lines_p2 = [
        LineResult(
            sequence_in_paragraph=1,
            engine_texts={
                "mineru": "取足三里、合谷以调气和胃。",
                "ppocr": "取足三裹、合谷以调气和胃。",
                "engine3": "取足三里、合谷以调气和胃。",
                "engine4": "取足三裹、合谷以调气和胃。",
            },
            consensus="取足三里、合谷以调气和胃。",
            llm_corrected="取足三里、合谷以调气和胃。",
            glyph_verified="取足三里、合谷以调气和胃。",
            final="取足三里、合谷以调气和胃。",
            confidence=0.93,
            crop_img_path=f"/mnt/source/{book_code}/page_002_line_001.png",
            engine_results=[
                EngineResult("mineru", "取足三里、合谷以调气和胃。", 0.95, 130),
                EngineResult("ppocr", "取足三裹、合谷以调气和胃。", 0.9, 85),
                EngineResult("engine3", "取足三里、合谷以调气和胃。", 0.94, 210),
                EngineResult("engine4", "取足三裹、合谷以调气和胃。", 0.89, 160),
            ],
            proofreads=[
                ProofreadRecord(
                    original_text="取足三裹、合谷以调气和胃。",
                    corrected_text="取足三里、合谷以调气和胃。",
                    change_type="meridian",
                    severity="critical",
                    notes="足三裹→足三里（经络穴位）",
                    triggered_pattern="MERIDIAN-足三里",
                )
            ],
        ),
    ]

    book = BookResult(
        book_code=book_code,
        title="中医方剂验案选（样张）",
        author=" mock 引擎",
        publisher="演示出版社",
        pub_year=2010,
        pub_era="laser",
        book_type="formula",
        pages=[
            PageResult(page_num=1, paragraphs=[ParagraphResult(sequence_in_page=1, lines=lines_p1)]),
            PageResult(page_num=2, paragraphs=[ParagraphResult(sequence_in_page=1, lines=lines_p2)]),
        ],
        herb_patterns=[
            HerbPattern(correct_name="白术", ocr_error_pattern="白木", pattern_type="glyph_shape",
                        is_toxic=False, severity="critical", source_books='["TCM-MOCK-001"]', evidence_count=3),
            HerbPattern(correct_name="茯苓", ocr_error_pattern="茯芩", pattern_type="glyph_similar",
                        is_toxic=False, severity="warning", source_books='["TCM-MOCK-001"]', evidence_count=2),
        ],
        meridian_patterns=[
            MeridianPattern(correct_name="足三里", ocr_error_pattern="足三裹", entity_type="point",
                           meridian_belonging="足阳明胃经", body_region="下肢", severity="critical",
                           source_books='["TCM-MOCK-001"]', evidence_count=4),
        ],
        context_patterns=[
            ContextPattern(pattern_text="上方加味", pattern_type="add_to_above",
                           regex="上方加味", example="上方加味，加陈皮一钱。",
                           discovered_count=1, source_books='["TCM-MOCK-001"]'),
        ],
        terms=[
            TermEntry(term_name="白术", sublib="中药", error_pattern="白木", correct_form="白术",
                      scope="global", scope_score=1, confidence=0.98),
            TermEntry(term_name="足三里", sublib="穴位", error_pattern="足三裹", correct_form="足三里",
                      scope="global", scope_score=1, confidence=0.97),
        ],
        formulas=[
            FormulaEntry(
                formula_name="健脾汤",
                ingredients=[
                    FormulaIngredient(herb_name="白术", dosage_value="三钱", unit="钱", role_in_formula="君", is_toxic=False),
                    FormulaIngredient(herb_name="茯苓", dosage_value="二钱", unit="钱", role_in_formula="臣", is_toxic=False),
                ],
            )
        ],
        engine_label="mock",
        is_mock=True,
    )
    book.final_markdown = _render_markdown(book)
    return book


def _render_markdown(book: BookResult) -> str:
    lines = [f"# {book.title}", "", f"> 来源：{book.publisher}（{book.pub_year}） | 引擎：{book.engine_label} | 生成于 {time.strftime('%Y-%m-%d %H:%M')}", ""]
    for p in book.pages:
        lines.append(f"## 第 {p.page_num} 页")
        lines.append("")
        for para in p.paragraphs:
            for ln in para.lines:
                text = ln.human_final or ln.final or ln.consensus or ""
                lines.append(text)
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 三大永久范式库（本批沉淀）")
    lines.append("")
    lines.append("### 药名 OCR 范式")
    for h in book.herb_patterns:
        lines.append(f"- {h.correct_name} ← {h.ocr_error_pattern}（{h.pattern_type}, {h.severity}）")
    lines.append("")
    lines.append("### 经络穴位 OCR 范式")
    for m in book.meridian_patterns:
        lines.append(f"- {m.correct_name} ← {m.ocr_error_pattern}（{m.entity_type}, {m.meridian_belonging}）")
    lines.append("")
    return "\n".join(lines)
