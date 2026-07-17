"""KZOCR 跨引擎 token 级模糊对齐（借鉴 ocr_pipeline_v2 验证设计）。

取代「逐字 PK join」（ocr_pipeline_v2 假设 D 实测已证伪：VLM 输出归一化文本，
char_idx 投影对齐无意义）。改为对两引擎**全文**做字符级 diff（difflib 最优对齐），
抽取分歧点（replace/delete/insert）作为误认候选，**数字/剂量类分歧优先高亮**，
供 HumanGate / 视觉仲裁判定。

设计要点（与 ocr_pipeline_v2 一致，经四角色评审 + 豆包帖独立印证）：
- 两边文本都**必须去标点/空白**后再对齐，否则标点差异会淹没真实字符分歧。
- `align_engines` 是纯函数（不调网络），便于单测。
- 数字/剂量分歧（阿拉伯 6↔5、9↔3… 及中文 二↔三、五↔三…）与形近字黑名单（芩↔苓…）标记 high 优先级。
- 分歧落 `cross_divergence` 表（CREATE TABLE IF NOT EXISTS，幂等）。

与 KZOCR 的关系：KZOCR 当前是「逐 tier 验证→失败降级」（Tier1 不过才上 Tier2），
从不比对两个引擎的文本。本模块补齐这一环——在 Tier1/Tier2 双产出时比对，
把剂量数字等关键分歧精准标出，送现有 HumanGate 或视觉仲裁（VisionRecheckAdapter）。

用法（单页）：
    from kzocr.scheduler.cross_align import align_engines, Divergence, write_divergences
    divs = align_engines(tier1_page_text, tier2_page_text, ctx=8)
    write_divergences(db_path, page_no, divs, engine_a="tier1", engine_b="tier2")
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from kzocr.engines.atomic import atomic_write

# 形近字黑名单默认路径（与 kzocr/resources/confusion_set.json 一致）
_DEFAULT_CONFUSION_PATH = Path(__file__).resolve().parent.parent / "resources" / "confusion_set.json"
# 自学习混淆集：运行时由人工/仲裁确认的新混淆对追加于此，叠加在静态集之上（可进化）
_LEARNED_CONFUSION_PATH = Path(__file__).resolve().parent.parent / "resources" / "learned_confusion.json"

# 标点 + 空白：对齐前统一剥离，避免标点差异淹没真实字符分歧
_PUNCT = set("，。、；：！？“”‘’（）《》—…·「」『』〈〉【】〔〕,.!?;:\"'`()[]{}<>~·—-…")
_WS = set(" \t　\n\r")

# 中文数字：古籍方剂剂量多用中文数字（二/三/钱），与阿拉伯数字同属高风险分歧
_CN_NUM = set("〇零一二三四五六七八九十百千万两半")


def strip_punct(s: str) -> str:
    """去掉标点与空白（用于对齐前的文本归一化）。"""
    return "".join(ch for ch in s if ch not in _PUNCT and ch not in _WS)


def _load_confusion_file(path: Path) -> dict:
    """从单个 JSON 文件加载形近字黑名单为 {wrong: correct}（跳过 category=='正确'/wrong==correct）。

    文件缺失/解析失败返回空字典（不影响对齐主流程）。
    """
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict = {}
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            wrong = row.get("wrong")
            correct = row.get("correct")
            category = row.get("category", "")
            if not wrong or not correct:
                continue
            if category == "正确" or wrong == correct:
                continue
            out[wrong] = correct
    return out


# 内存常驻缓存：黑名单"静态呆在内存里"供快速调用，内容则经 add_learned_confusion 动态优化
_CONFUSION_CACHE: Optional[dict] = None


def load_confusion_set(path: Optional[Path] = None, *, reload: bool = False) -> dict:
    """加载形近字黑名单为 {wrong: correct}，并叠加自学习集（learned 覆盖静态）。

    读取 confusion_set.json（list of {wrong, correct, category}），跳过
    category=='正确' 或 wrong==correct 的条目（避免误判 UNKNOWN）。
    自学习集 `learned_confusion.json` 若存在则合并其上（让新发现的混淆立即生效，
    实现"可进化"）。

    为兼顾"常驻内存、快速调用"，首次构建后缓存在模块级 `_CONFUSION_CACHE`，
    后续调用直接返回缓存（reload=True 强制重读，或新增学习项时自动更新缓存）。
    任一文件缺失/解析失败不影响主流程。
    """
    global _CONFUSION_CACHE
    if not reload and _CONFUSION_CACHE is not None:
        return _CONFUSION_CACHE
    out = _load_confusion_file(Path(path) if path else _DEFAULT_CONFUSION_PATH)
    out.update(_load_confusion_file(_LEARNED_CONFUSION_PATH))  # 学习集覆盖静态集
    _CONFUSION_CACHE = out
    return out


def reload_confusion_set() -> dict:
    """强制从磁盘重读并刷新内存缓存（例如在外部修改静态集后）。"""
    return load_confusion_set(reload=True)


def add_learned_confusion(wrong: str, correct: str, source: str = "") -> bool:
    """把新发现的形近混淆对追加到 `learned_confusion.json`（原子写入，去重）。

    用于"黑名单自学习/进化"：人工在 Web 校对台确认某对形近字、或仲裁发现新混淆时，
    调用本函数持久化，并**同步更新内存缓存**，下次比对立即生效（内容动态、调用静态）。
    返回 True=新增，False=参数非法或已存在（noop）。

    安全：经由 atomic_write 的 allowed_base 约束写入路径（防路径穿越，同 C2 修复）。
    """
    global _CONFUSION_CACHE
    wrong, correct = (wrong or "").strip(), (correct or "").strip()
    if not wrong or not correct or wrong == correct:
        return False
    data: list = []
    if _LEARNED_CONFUSION_PATH.is_file():
        try:
            data = json.loads(_LEARNED_CONFUSION_PATH.read_text(encoding="utf-8")) or []
        except (json.JSONDecodeError, OSError):
            data = []
    if not isinstance(data, list):
        data = []
    for row in data:
        if isinstance(row, dict) and row.get("wrong") == wrong and row.get("correct") == correct:
            return False  # 已存在
    data.append({
        "wrong": wrong,
        "correct": correct,
        "category": "learned",
        "source": source,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    })
    atomic_write(
        _LEARNED_CONFUSION_PATH,
        json.dumps(data, ensure_ascii=False, indent=2),
        allowed_base=_LEARNED_CONFUSION_PATH.parent,
    )
    # 同步更新内存缓存（内容动态优化，常驻内存供快速调用）
    if _CONFUSION_CACHE is not None:
        _CONFUSION_CACHE[wrong] = correct
    return True


@dataclass
class Divergence:
    """一个跨引擎字符级分歧点。

    page_no 由 `run_cross_align` / 调用方填充；纯 `align_engines` 默认 0。
    priority='high' 表示数字/剂量分歧或命中形近字黑名单，应优先送人工/视觉仲裁。
    """

    page_no: int = 0
    div_type: str = "replace"  # 'replace' | 'delete' | 'insert'
    a_seg: str = ""  # 引擎 A 侧片段（insert 时为空）
    b_seg: str = ""  # 引擎 B 侧片段（delete 时为空）
    a_context: str = ""  # ±ctx 字上下文，分歧处用【】标出
    boxes: list[list[int]] = field(default_factory=list)  # 引擎 A 侧字符 box 列表（可选）
    priority: str = "normal"  # 'high' | 'normal'
    status: str = "pending"  # pending / arbitrated / accepted_a / accepted_b / both_wrong / skipped
    engine_a: str = ""
    engine_b: str = ""


@dataclass
class DivergenceArbitration:
    """视觉仲裁（Box-Guided VL）对一个分歧点的裁决结果（视觉仲裁共享类型）。

    decision 取值与含义：
      - accepted_a : VL 确认图片真实字 = 引擎 A 侧字符（a_seg）
      - accepted_b : VL 确认图片真实字 = 引擎 B 侧字符（b_seg）
      - both_wrong : VL 给出第三字（两者皆错）→ 强制人工
      - uncertain  : VL 无法判定 → 送 L3 本地 LLM 兜底
      - manual     : is_match=False / conf<0.65 / JSON 解析失败 / 无图像 / box 非法 → 强制人工

    mode：'box_guided'（精确裁框）| 'degraded'（无 char box，整页缩图 + 上下文提示）。
    当前 KZOCR 归一化数据无逐字 bbox，`Divergence.boxes` 为空 → 走 degraded。
    """

    page_no: int = 0
    div_index: int = 0
    decision: str = "manual"
    confidence: float = 0.0
    real_char: str = ""
    raw: str = ""
    engine: str = ""
    mode: str = "degraded"


def _is_priority(a_seg: str, b_seg: str, confusion_set: Optional[dict]) -> bool:
    """数字/剂量分歧 或 形近字黑名单命中 → 高优先级。"""
    seg = a_seg + b_seg
    # 阿拉伯数字：古籍方剂剂量最易错且最危险（6↔5、9↔3、8↔3、69↔53…）
    if any(ch.isdigit() for ch in seg):
        return True
    # 中文数字：方剂剂量多用中文数字（二↔三、五↔三…），同属高风险
    if any(ch in _CN_NUM for ch in seg):
        return True
    # 形近字黑名单：单字替换且 A→B 在混淆表中（芩↔苓、炙↔灸、黄↔皇、麥↔麦…）
    if confusion_set and len(a_seg) == 1 and len(b_seg) == 1:
        if confusion_set.get(a_seg) == b_seg:
            return True
    return False


def align_engines(
    text_a: str,
    text_b: str,
    ctx: int = 8,
    confusion_set: Optional[dict] = None,
    boxes_a: Optional[list[list[int]]] = None,
) -> list[Divergence]:
    """对两引擎全文做字符级最优对齐，抽取分歧点。

    参数：
        text_a: 引擎 A 全文本（可含标点/空白，内部会归一化）
        text_b: 引擎 B 全文本（可含标点/空白，内部会归一化）
        ctx: 上下文窗口大小（字符数）
        confusion_set: 形近字黑名单 wrong->correct，命中则分歧标 high（可选）
        boxes_a: 与 text_a 字符一一对应的 box 列表（可选，供视觉仲裁裁图）
    返回：Divergence 列表（已过滤 equal 段），page_no 默认 0
    """
    import difflib

    a = strip_punct(text_a)
    b = strip_punct(text_b)
    n = len(a)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    divs: list[Divergence] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        ctx_l, ctx_r = max(0, i1 - ctx), min(n, i2 + ctx)
        a_seg = a[i1:i2]
        b_seg = b[j1:j2]
        boxes = [boxes_a[k] for k in range(i1, i2)] if (boxes_a and i2 > i1) else []
        divs.append(
            Divergence(
                page_no=0,
                div_type=tag,
                a_seg=a_seg,
                b_seg=b_seg,
                a_context=a[ctx_l:i1] + "【" + a_seg + "】" + a[i2:ctx_r],
                boxes=boxes,
                priority="high" if _is_priority(a_seg, b_seg, confusion_set) else "normal",
            )
        )
    return divs


def run_cross_align(
    page_no: int,
    text_a: str,
    text_b: str,
    ctx: int = 8,
    confusion_set: Optional[dict] = None,
    boxes_a: Optional[list[list[int]]] = None,
    engine_a: str = "tier1",
    engine_b: str = "tier2",
) -> list[Divergence]:
    """单页端到端：两引擎文本 → 对齐 → 分歧列表（已填 page_no / 引擎标签）。"""
    divs = align_engines(text_a, text_b, ctx=ctx, confusion_set=confusion_set, boxes_a=boxes_a)
    for d in divs:
        d.page_no = page_no
        d.engine_a = engine_a
        d.engine_b = engine_b
    return divs


def write_divergences(
    db_path: str | Path,
    page_no: int,
    divs: Iterable[Divergence],
    engine_a: str = "",
    engine_b: str = "",
) -> int:
    """把分歧点写入 cross_divergence 表（表不存在则自建，幂等）。返回写入行数。"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cross_divergence (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                page_no     INTEGER NOT NULL,
                div_type    TEXT    NOT NULL,
                a_seg       TEXT    NOT NULL DEFAULT '',
                b_seg       TEXT    NOT NULL DEFAULT '',
                a_context   TEXT    NOT NULL DEFAULT '',
                boxes       TEXT    NOT NULL DEFAULT '[]',
                priority    TEXT    NOT NULL DEFAULT 'normal',
                status      TEXT    NOT NULL DEFAULT 'pending',
                engine_a    TEXT    NOT NULL DEFAULT '',
                engine_b    TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    DEFAULT (datetime('now'))
            )
            """
        )
        rows = 0
        for d in divs:
            conn.execute(
                """
                INSERT INTO cross_divergence
                    (page_no, div_type, a_seg, b_seg, a_context, boxes,
                     priority, status, engine_a, engine_b)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    d.page_no if d.page_no else page_no,
                    d.div_type,
                    d.a_seg,
                    d.b_seg,
                    d.a_context,
                    json.dumps(d.boxes, ensure_ascii=False),
                    d.priority,
                    d.status,
                    d.engine_a or engine_a,
                    d.engine_b or engine_b,
                ),
            )
            rows += 1
        conn.commit()
        return rows
    finally:
        conn.close()
