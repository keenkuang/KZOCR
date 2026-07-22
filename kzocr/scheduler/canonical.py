"""两点修订 stage 2/3：字级 canonical 数据模型 + ErrorRecord 错误识别记录体系。

本模块定义字级规范化实体与其构造/派生/对齐的纯函数：

- ``CanonicalChar``：一个「字」的权威实体（bbox / 字图路径 / 行段页归属 / 代理置信度 /
  最终贡献引擎 / 修订者），初始来自 paddleocrv6，可被程序/人工修订。
- ``EngineCharRecord``：某引擎对该字的原始记录（逐字对齐到 canonical 位置后挂载）。
- ``ErrorRecord``：由跨引擎分歧 ``cross_divergence`` 派生的错误识别记录，反哺识别率。

坐标系铁律：所有 bbox 均为**版心图（经 ``_crop_to_body``）、dpi=150、不缩放、原点版心图左上角**，
与 ``line.char_boxes`` 完全一致。绝不使用 VL 缩放坐标。

里程碑：M0 定义 dataclass；M1 实现 ``build_canonical_chars`` / ``map_divergence_to_canonical``；
M3 实现 ``derive_error_records``。落库方法在 ``kzocr/storage/db.py``。
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

_logger = logging.getLogger(__name__)


@dataclass
class EngineCharRecord:
    """某引擎对某个 canonical 字的原始识别记录（逐字对齐后挂载）。

    bbox 为该引擎自身的字符框；当两引擎字数不一（delete/insert）时可能为 None。
    is_final 标记该引擎是否最终贡献了 canonical 字（consensus 胜出方）。
    """

    engine: str
    char_text: str
    confidence: float = 0.9
    confidence_source: str = "default"  # 'line'|'engine'|'default'
    bbox: Optional[list[int]] = None  # [x1,y1,x2,y2]
    is_final: bool = False


@dataclass
class CanonicalChar:
    """字级规范化实体（stage 2 核心）。

    char_pos 为行内 0-based 字位；bbox 为版心图 dpi=150 不缩放坐标；
    char_img_path 为相对 ``<db_dir>/<book_code>_crops/`` 的 PNG（best-effort，可空）；
    confidence 为行级代理（真逐字置信度管线暂无），confidence_source 标注来源；
    final_engine 为最终贡献引擎（初始 paddleocrv6）；revised_by 标记是否被修订。
    """

    page_num: int
    para_seq: int
    line_seq: int
    char_pos: int
    char_text: str
    bbox: list[int]  # [x1,y1,x2,y2]，未知时为 []
    char_img_path: Optional[str] = None
    confidence: float = 0.9
    confidence_source: str = "default"  # 'line'|'engine'|'default'
    final_engine: Optional[str] = None
    revised_by: str = "none"  # 'none'|'program'|'human'
    engine_records: list[EngineCharRecord] = field(default_factory=list)


@dataclass
class ErrorRecord:
    """错误识别记录（stage 3）：由 cross_divergence 派生，反哺识别率。

    wrong_char/correct_char：引擎识别错误字符与正确字符（correct 优先 human_final 否则 consensus）。
    source_divergence_id：指向 cross_divergence.id（FK）。
    error_type：'replace'|'delete'|'insert'。status：'pending'|'confirmed'|'rejected'。
    """

    page_no: int
    line_seq: Optional[int]
    char_pos: Optional[int]
    engine: str
    wrong_char: Optional[str]
    correct_char: Optional[str]
    source_divergence_id: Optional[int]
    error_type: str  # 'replace'|'delete'|'insert'
    status: str = "pending"  # 'pending'|'confirmed'|'rejected'
    confidence: Optional[float] = None


def _align_positions(from_text: str, to_text: str) -> list[Optional[int]]:
    """把 ``from_text`` 对齐到 ``to_text``，返回长度 ``len(to_text)`` 的列表：

    每个 ``to_text`` 位置对应 ``from_text`` 的索引（无对应则 None）。
    基于 difflib 字符级最优对齐（先去噪见 cross_align.strip_punct 思路可扩展）。
    """
    sm = difflib.SequenceMatcher(None, list(from_text), list(to_text), autojunk=False)
    res: list[Optional[int]] = [None] * len(to_text)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                res[j1 + k] = i1 + k
        elif tag == "replace":
            # 段级替换：位置逐一配对（min 长度内），多余侧不映射
            m, n = i2 - i1, j2 - j1
            for k in range(min(m, n)):
                res[j1 + k] = i1 + k
        # 'delete'：from 侧字符不在 to_text 中 → 跳过
        # 'insert'：to 侧字符不在 from 中 → res 保持 None
    return res


def build_canonical_chars(
    engine_texts: dict[str, str],
    consensus: str,
    char_boxes: list[list[int]],
    line_identity: tuple[int, int, int],
    line_confidence: float = 0.9,
    primary_engine: str = "paddleocrv6",
    source_pdf: str = "",
    db_dir: str = "",
    book_code: str = "",
) -> list[CanonicalChar]:
    """由两引擎原文 + consensus 构造逐字 canonical 实体列表（stage 2 核心纯函数）。

    - 以 consensus 为字级主干（与 ``line.char_boxes`` 坐标对齐）。
    - 用 difflib 把每个引擎文本对齐到 consensus，得到每字位该引擎原始字符（或 None）。
    - final_engine：取该字位与 consensus 一致的引擎，否则 primary_engine。
    - bbox：取自 primary 引擎对齐位置对应的 char_boxes（字数不一可能为空 → []）。
    - char_img_path：若 ``source_pdf``/``db_dir``/``book_code`` 齐备，best-effort 切片回填。

    坐标系：char_boxes 须为版心图 dpi=150 不缩放坐标（与 BookDB 一致）。
    """
    page_num, para_seq, line_seq = line_identity
    engines = list(engine_texts.keys())
    if not engines:
        return []
    a_engine = primary_engine if primary_engine in engine_texts else engines[0]
    b_engine = next((e for e in engines if e != a_engine), a_engine)
    a_text = engine_texts.get(a_engine, "")
    b_text = engine_texts.get(b_engine, "")

    a_pos = _align_positions(a_text, consensus)
    b_pos = _align_positions(b_text, consensus)

    doc_cache: dict = {}
    img_cache: dict = {}
    chars: list[CanonicalChar] = []
    try:
        for j, c in enumerate(consensus):
            a_idx = a_pos[j]
            b_idx = b_pos[j]
            a_char = a_text[a_idx] if a_idx is not None else None
            b_char = b_text[b_idx] if b_idx is not None else None

            records: list[EngineCharRecord] = []
            if a_char is not None:
                records.append(
                    EngineCharRecord(
                        engine=a_engine, char_text=a_char,
                        confidence=line_confidence, confidence_source="line",
                    )
                )
            if b_char is not None and b_engine != a_engine:
                records.append(
                    EngineCharRecord(
                        engine=b_engine, char_text=b_char,
                        confidence=line_confidence, confidence_source="line",
                    )
                )

            # final_engine：与 consensus 一致的引擎；都不同则 primary
            if a_char is not None and a_char == c:
                final = a_engine
            elif b_char is not None and b_char == c:
                final = b_engine
            else:
                final = a_engine if a_engine else None
            for r in records:
                r.is_final = (r.engine == final)

            # bbox：primary 引擎对齐位置 → char_boxes（字数不一可能无框 → []）
            bbox: list[int] = []
            if a_idx is not None and 0 <= a_idx < len(char_boxes):
                bbox = list(char_boxes[a_idx])

            char_img_path: Optional[str] = None
            if bbox and source_pdf and db_dir and book_code:
                from kzocr.storage.crop_images import crop_char_to_png

                char_img_path = crop_char_to_png(
                    source_pdf, page_num, bbox, para_seq, line_seq, j,
                    book_code, db_dir, doc_cache=doc_cache, img_cache=img_cache,
                )

            chars.append(
                CanonicalChar(
                    page_num=page_num, para_seq=para_seq, line_seq=line_seq,
                    char_pos=j, char_text=c, bbox=bbox,
                    char_img_path=char_img_path,
                    confidence=line_confidence, confidence_source="line",
                    final_engine=final, engine_records=records,
                )
            )
    finally:
        if doc_cache:
            from kzocr.storage.crop_images import close_doc_cache

            close_doc_cache(doc_cache)

    return chars


def map_divergence_to_canonical(divergence: Any, canonical_text: str) -> list[int]:
    """把分歧段锚定到 canonical 文本的字位列表（供 ErrorRecord 定位/前端高亮）。

    优先匹配 ``a_seg``，其次 ``b_seg``（delete 仅 a_seg 在 canonical；insert 仅 b_seg）。
    找不到返回空列表。``divergence`` 须有 ``a_seg``/``b_seg`` 属性。
    """
    for seg in (getattr(divergence, "a_seg", ""), getattr(divergence, "b_seg", "")):
        if seg:
            pos = canonical_text.find(seg)
            if pos >= 0:
                return list(range(pos, pos + len(seg)))
    return []


def derive_error_records(
    divergence: Any,
    canonical_text: str,
    engine_a: str,
    engine_b: str,
    page_no: int,
    line_seq: Optional[int] = None,
    human_final: Optional[str] = None,
    source_divergence_id: Optional[int] = None,
) -> list[ErrorRecord]:
    """由单条跨引擎分歧派生错误识别记录（stage 3 核心纯函数）。

    gold（正确字符）优先取 ``human_final`` 该字位，否则取 ``canonical_text`` 该字位。
    - ``replace`` 单字：gold 与哪侧一致，则另一侧为错（wrong=该侧片段，correct=gold）；
      两侧都与 gold 不同 → 两条记录（两引擎各错）。
    - ``delete``：a 侧多字、b 侧漏识 → 记 engine_b 漏识（wrong=None, correct=a_seg）。
    - ``insert``：b 侧多字、a 侧无 → 记 engine_b 多识（wrong=b_seg, correct=None）。
    返回 0~2 条 ErrorRecord（无匹配字位时 char_pos=None）。
    """
    a_seg = getattr(divergence, "a_seg", "") or ""
    b_seg = getattr(divergence, "b_seg", "") or ""
    div_type = getattr(divergence, "div_type", "")

    positions = map_divergence_to_canonical(divergence, canonical_text)
    char_pos = positions[0] if positions else None

    gold: Optional[str] = None
    if char_pos is not None:
        if human_final and 0 <= char_pos < len(human_final):
            gold = human_final[char_pos]
        elif 0 <= char_pos < len(canonical_text):
            gold = canonical_text[char_pos]

    recs: list[ErrorRecord] = []
    if div_type == "replace":
        if len(a_seg) == 1 and len(b_seg) == 1:
            if gold == a_seg:
                recs.append(ErrorRecord(page_no, line_seq, char_pos, engine_b,
                                        b_seg, a_seg, source_divergence_id, "replace"))
            elif gold == b_seg:
                recs.append(ErrorRecord(page_no, line_seq, char_pos, engine_a,
                                        a_seg, b_seg, source_divergence_id, "replace"))
            else:
                recs.append(ErrorRecord(page_no, line_seq, char_pos, engine_a,
                                        a_seg, gold, source_divergence_id, "replace"))
                recs.append(ErrorRecord(page_no, line_seq, char_pos, engine_b,
                                        b_seg, gold, source_divergence_id, "replace"))
        else:
            # 多字替换：各侧片段与 gold 不同则记一条
            if gold is not None:
                if gold != a_seg:
                    recs.append(ErrorRecord(page_no, line_seq, char_pos, engine_a,
                                            a_seg, gold, source_divergence_id, "replace"))
                if gold != b_seg:
                    recs.append(ErrorRecord(page_no, line_seq, char_pos, engine_b,
                                            b_seg, gold, source_divergence_id, "replace"))
    elif div_type == "delete":
        # a 侧多字、b 侧漏识 → engine_b 漏识
        recs.append(ErrorRecord(page_no, line_seq, char_pos, engine_b,
                                None, a_seg or None, source_divergence_id, "delete"))
    elif div_type == "insert":
        # b 侧多字、a 侧无 → engine_b 多识
        recs.append(ErrorRecord(page_no, line_seq, char_pos, engine_b,
                                b_seg or None, None, source_divergence_id, "insert"))
    return recs


def build_page_canonical_and_errors(
    page_lines: list[tuple[int, int, str, list]],
    engine_a_text: str,
    engine_b_text: str,
    engine_a: str,
    engine_b: str,
    page_no: int,
    line_confidence: float = 0.9,
    divs: Optional[list] = None,
    human_final_map: Optional[dict] = None,
    source_pdf: str = "",
    db_dir: str = "",
    book_code: str = "",
) -> tuple[list[CanonicalChar], list[ErrorRecord]]:
    """页面级便捷函数：跨页所有行构造 canonical 字 + 派生 error 记录。

    ``page_lines``：``(para_seq, line_seq, consensus_text, char_boxes)`` 列表（按 flat 行序）。
    ``engine_a_text``/``engine_b_text`` 为整页两引擎原文（按 ``\\n`` 切分映射到各行）。
    ``divs`` 为整页分歧（Divergence 列表）；对每条分歧，best-effort 匹配首个包含其片段的行，
    派生 ErrorRecord（避免跨行重复：每分歧仅挂首个匹配行）。

    返回 ``(canonical_chars, error_records)``，供 orchestrator / e2e 落库。
    """
    a_lines = engine_a_text.split("\n")
    b_lines = engine_b_text.split("\n")
    if human_final_map is None:
        human_final_map = {}

    canon: list[CanonicalChar] = []
    errs: list[ErrorRecord] = []
    for flat, (para_seq, line_seq, consensus_text, cb) in enumerate(page_lines):
        a_line = a_lines[flat] if flat < len(a_lines) else ""
        b_line = b_lines[flat] if flat < len(b_lines) else ""
        canon.extend(
            build_canonical_chars(
                {engine_a: a_line, engine_b: b_line},
                consensus_text,
                cb,
                (page_no, para_seq, line_seq),
                line_confidence=line_confidence,
                primary_engine=engine_a,
                source_pdf=source_pdf,
                db_dir=db_dir,
                book_code=book_code,
            )
        )
        if divs:
            hf = human_final_map.get((page_no, para_seq, line_seq))
            for d in divs:
                a_seg = getattr(d, "a_seg", "") or ""
                b_seg = getattr(d, "b_seg", "") or ""
                if (a_seg and a_seg in consensus_text) or (b_seg and b_seg in consensus_text):
                    errs.extend(
                        derive_error_records(
                            d, consensus_text, engine_a, engine_b, page_no,
                            line_seq=line_seq, human_final=hf,
                            source_divergence_id=getattr(d, "id", None),
                        )
                    )
                    break  # 每分歧仅挂首个匹配行
    return canon, errs

