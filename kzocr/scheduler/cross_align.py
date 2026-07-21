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
# 语义级错词对黑名单（Layer2：方剂词组/同音/语义错，交给 M6 语义校验）
_DEFAULT_PHRASE_PATH = Path(__file__).resolve().parent.parent / "resources" / "confusion_phrase.json"

# 标点 + 空白：对齐前统一剥离，避免标点差异淹没真实字符分歧
_PUNCT = set("，。、；：！？“”‘’（）《》—…·「」『』〈〉【】〔〕,.!?;:\"'`()[]{}<>~·—-…")
_WS = set(" \t　\n\r")

# 中文数字：古籍方剂剂量多用中文数字（二/三/钱），与阿拉伯数字同属高风险分歧
_CN_NUM = set("〇零一二三四五六七八九十百千万两半")


def strip_punct(s: str) -> str:
    """去掉标点与空白（用于对齐前的文本归一化）。"""
    return "".join(ch for ch in s if ch not in _PUNCT and ch not in _WS)


def align_boxes_to_text(
    text_a: str,
    char_boxes: Optional[list[list[list[int]]]],
) -> Optional[list[list[int]]]:
    """把逐行 char_boxes 展平并对齐到去标点后的 text_a，供 ``run_cross_align`` 的 boxes_a。

    KZOCR 的 ``char_boxes`` 为 ``list[line][char][x1,y1,x2,y2]``，而 ``align_engines``
    的 ``boxes_a`` 要求与去标点后的文本逐字 1:1（``len(boxes_a) == len(strip_punct(text_a))``）。

    对齐策略：
    - 展平为单字符框列表；
    - 仅当展平框数 == ``text_a`` 字符数（即 1 框/字）时，逐字去标点/空白并携框，
      返回对齐后的逐字框列表；
    - 否则（某行缺框、框数不符等）返回 ``None``，调用方据此走整页退化（degraded），
      避免静默错配。

    Returns:
        对齐后的逐字框列表，或 ``None``（无框 / 长度不符 / 去标点后长度仍不符）。
    """
    if not char_boxes:
        return None
    flat = [box for line in char_boxes for box in line]
    if len(flat) != len(text_a):
        return None
    out: list[list[int]] = []
    for ch in text_a:
        if ch in _PUNCT or ch in _WS:
            continue
        out.append(flat[len(out)])
    # 二次校验：与 align_engines 内部 strip_punct 后的长度一致
    if len(out) != len(strip_punct(text_a)):
        return None
    return out


def _load_confusion_file(path: Path, *, raw: bool = False) -> dict[str, str] | list[dict]:
    """从单个 JSON 文件加载形近字黑名单。

    raw=False（默认）：返回 {wrong: correct}（跳过 category=='正确'/wrong==correct）。
    raw=True：返回原始 list[dict]（保留 level/category 等字段，供 load_confusion_keys 使用）。
    文件缺失/解析失败返回空字典/空列表（不影响主流程）。
    """
    if not path.is_file():
        return {} if not raw else []
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {} if not raw else []
    if not isinstance(raw_data, list):
        return {} if not raw else []
    validate_confusion_rows(raw_data)  # 程序启动自检：捕获自匹配/结构错误
    if raw:
        return raw_data
    out: dict = {}
    for row in raw_data:
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


def validate_confusion_rows(rows: list[dict]) -> None:
    """运行时自检黑名单（用户规范）：捕获自匹配(no-op)与结构错误。

    每次加载黑名单时自动调用（程序启动即执行），发现异常仅告警不中断主流程，
    防止后续人工扩充清单引入无效条目（如 "麻黄":["麻黄"] 自环）。
    """
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            print(f"黑名单异常：第 {i} 行不是对象，已跳过: {row!r}")
            continue
        wrong = row.get("wrong")
        correct = row.get("correct")
        if not wrong or not correct:
            print(f"黑名单异常：第 {i} 行缺少 wrong/correct 字段，已跳过: {row!r}")
            continue
        if wrong == correct:
            print(f"黑名单异常：{wrong}: 包含自身 {correct}（自匹配无效，应删除）")


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


# ── 两层黑名单辅助：Layer1 字符级（confusion_set.json）+ Layer2 词级（confusion_phrase.json） ──
# Layer1：字形相近误识别，强制 M4 复核；Layer2：词组/同音/语义错，交 M6 语义校验。
_LEVEL_RANK = {"一级高危": 1, "二级中频": 2, "三级通用": 3}
_LEVEL_PATH = {1: "一级高危", 2: "二级中频", 3: "三级通用"}

_KEYS_CACHE: Optional[dict] = None
_KEYS_SPLIT_CACHE: Optional[dict] = None  # load_confusion_keys_split 专用缓存
_PHRASE_CACHE: Optional[list] = None


def _merge_split_keys(rows: list[dict]) -> dict:
    """Layer1 分侧合并：把 list[{wrong, correct, level, ...}] 合并为
    ``{"wrong": {char: rank}, "correct": {char: rank}}``，同字符取最高级（最小 rank）。

    静态集与学习集行格式一致，调用方先拼接二者再传入即可统一合并；rank 取自
    ``_LEVEL_RANK``，缺失 level 按三级通用处理。
    """
    wrong: dict[str, int] = {}
    correct: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        w, c = row.get("wrong"), row.get("correct")
        level = row.get("level", "三级通用")
        rank = _LEVEL_RANK.get(level, 3)
        if not w or not c or w == c:
            continue
        for ch, side in ((w, wrong), (c, correct)):
            prev = side.get(ch)
            if prev is None or rank < prev:
                side[ch] = rank
    return {"wrong": wrong, "correct": correct}


def load_confusion_keys(path: Optional[Path] = None, *, reload: bool = False) -> dict:
    """Layer1 字符级：返回 {字符: 最高级 level}（静态集 + 学习集自动合并）。

    任一字符只要参与某个混淆对（无论作 wrong 还是 correct/基准字），都视为高风险字形，
    供 ConfusionKeyPresenceDetector 做"前置静态筛查（零算力）"——输出含一级高危基准字即强制 M4。
    静态集与自学习集（learned_confusion.json）合并，使终校回流的混淆对即时生效。
    缓存常驻内存，reload=True 强制重读。
    """
    global _KEYS_CACHE
    if not reload and _KEYS_CACHE is not None:
        return _KEYS_CACHE
    rows = _load_confusion_file(Path(path) if path else _DEFAULT_CONFUSION_PATH, raw=True)
    rows = list(rows) + list(_load_confusion_file(_LEARNED_CONFUSION_PATH, raw=True))
    split = _merge_split_keys(rows)
    merged: dict[str, int] = {}
    for ch, r in split["wrong"].items():
        merged[ch] = min(merged.get(ch, 99), r)
    for ch, r in split["correct"].items():
        merged[ch] = min(merged.get(ch, 99), r)
    _KEYS_CACHE = {ch: _LEVEL_PATH[r] for ch, r in merged.items()}
    return _KEYS_CACHE


def load_confusion_keys_split(path: Optional[Path] = None, *, reload: bool = False) -> dict:
    """Layer1 分侧：区分误认侧(wrong) / 基准侧(correct)。返回 {"wrong": ..., "correct": ...}。

    wrong 侧：OCR 可能误输出的字符（如把"朴"误认成"补"，则"补"在 wrong 侧）。
    correct 侧：正确/基准字符（如"朴"）。
    双向字符（同时出现在 wrong 和 correct 侧，如 补↔朴）→ 两侧都记。

    供 ConfusionKeyPresenceDetector 做"分侧强弱标定"——含误认字强标（confidence=0.55），
    含正确基准字弱标（confidence=0.35）。
    独立缓存 _KEYS_SPLIT_CACHE（不破坏已有 _KEYS_CACHE）。
    """
    global _KEYS_SPLIT_CACHE
    # 只有使用默认路径时才写入全局缓存（自定义路径不覆盖全局缓存，避免测试间污染）
    use_cache = path is None
    if use_cache and not reload and _KEYS_SPLIT_CACHE is not None:
        return _KEYS_SPLIT_CACHE
    rows = _load_confusion_file(Path(path) if path else _DEFAULT_CONFUSION_PATH, raw=True)
    rows = list(rows) + list(_load_confusion_file(_LEARNED_CONFUSION_PATH, raw=True))
    wrong: dict[str, int] = {}
    correct: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        w, c = row.get("wrong"), row.get("correct")
        level = row.get("level", "三级通用")
        rank = _LEVEL_RANK.get(level, 3)
        if not w or not c or w == c:
            continue
        for ch, side in ((w, wrong), (c, correct)):
            prev = side.get(ch)
            if prev is None or rank < prev:
                side[ch] = rank
    result = {
        "wrong": {ch: _LEVEL_PATH[r] for ch, r in wrong.items()},
        "correct": {ch: _LEVEL_PATH[r] for ch, r in correct.items()},
    }
    if use_cache:
        _KEYS_SPLIT_CACHE = result
    return result


def load_confusion_phrases(path: Optional[Path] = None, *, reload: bool = False) -> list:
    """Layer2 词级：返回 confusion_phrase.json 的 list[{wrong, correct, level, category, note}]。"""
    global _PHRASE_CACHE
    if not reload and _PHRASE_CACHE is not None:
        return _PHRASE_CACHE
    p = Path(path) if path else _DEFAULT_PHRASE_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
    except (json.JSONDecodeError, OSError):
        data = []
    data = data if isinstance(data, list) else []
    validate_confusion_rows(data)  # 程序启动自检：捕获自匹配/结构错误
    _PHRASE_CACHE = data
    return _PHRASE_CACHE


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
    # 同步分侧/键集缓存：学习对归三级通用（最低级），已存在的更高优先级不降级
    if _KEYS_CACHE is not None:
        _KEYS_CACHE.setdefault(wrong, "三级通用")
        _KEYS_CACHE.setdefault(correct, "三级通用")
    if _KEYS_SPLIT_CACHE is not None:
        _KEYS_SPLIT_CACHE.setdefault("wrong", {}).setdefault(wrong, "三级通用")
        _KEYS_SPLIT_CACHE.setdefault("correct", {}).setdefault(correct, "三级通用")
    return True


@dataclass
class Divergence:
    """一个跨引擎字符级分歧点。

    page_no 由 `run_cross_align` / 调用方填充；纯 `align_engines` 默认 0。
    priority='P0' 表示数字/剂量分歧，'P1' 表示命中形近字黑名单，二者均优先送人工/视觉仲裁（历史曾用 'high'，语义等同）。
    """

    page_no: int = 0
    div_type: str = "replace"  # 'replace' | 'delete' | 'insert'
    a_seg: str = ""  # 引擎 A 侧片段（insert 时为空）
    b_seg: str = ""  # 引擎 B 侧片段（delete 时为空）
    a_context: str = ""  # ±ctx 字上下文，分歧处用【】标出
    boxes: list[list[int]] = field(default_factory=list)  # 引擎 A 侧字符 box 列表（可选）
    priority: str = "normal"  # 'P0' | 'P1' | 'normal'（历史曾用 'high'，现由 P0/P1 取代；orchestrator 将三者一并归入高优先队列）
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


def _is_priority(a_seg: str, b_seg: str, confusion_set: Optional[dict]) -> str:
    """数字/剂量分歧 → P0；形近字黑名单命中 → P1；正常 → normal。"""
    seg = a_seg + b_seg
    # P0: 阿拉伯数字或中文数字——古籍方剂剂量最易错且最危险
    if any(ch.isdigit() for ch in seg):
        return "P0"
    if any(ch in _CN_NUM for ch in seg):
        return "P0"
    # P1: 形近字黑名单命中（单字替换且 A→B 在混淆表中）
    if confusion_set and len(a_seg) == 1 and len(b_seg) == 1:
        if confusion_set.get(a_seg) == b_seg:
            return "P1"
    return "normal"


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
        # boxes_a 应与去标点后的 a 等长；长度不符则放弃框，避免 IndexError / 静默错配
        if boxes_a and i2 > i1 and len(boxes_a) == len(a):
            boxes = [boxes_a[k] for k in range(i1, i2)]
        else:
            boxes = []
        divs.append(
            Divergence(
                page_no=0,
                div_type=tag,
                a_seg=a_seg,
                b_seg=b_seg,
                a_context=a[ctx_l:i1] + "【" + a_seg + "】" + a[i2:ctx_r],
                boxes=boxes,
                priority=_is_priority(a_seg, b_seg, confusion_set),
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
