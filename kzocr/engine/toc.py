"""F1: TOC 抽取与章节层级重建。

纯文本方案（不依赖 VLM），从 E4 产出的 pages_text 中自动发现目录页、
解析目录条目、构建层级树，挂到 BookResult.toc。

B1 修正：NFC 归一化 + Levenshtein 模糊匹配目录标题关键词（繁简+常见 OCR 混淆）。
B2 修正：缩进阶为辅助（检测到连续空格时使用），主策略编号深度+关键词。
R3 采纳：正则均加 {1,200} 量词上限防 ReDoS。
R5 采纳：节标题匹配扩展 "第一节"、"一、"、"（一）" 等。
R6 采纳：中文数字→阿拉伯数字转换。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

from kzocr.engine.types import BookResult, TocEntry, TocTree

_logger = logging.getLogger(__name__)

# ── TOC 标题关键词（默认，B1 模糊匹配用）──
TOC_HEADER_KEYWORDS = [
    "目录", "目錄", "目録", "總目", "總目錄", "纲目", "綱目", "總綱",
    "条", "條目", "叙目", "敘目", "卷首", "凡例",
    "CONTENTS", "TABLE OF CONTENTS",
]

# ── 中文数字→阿拉伯数字（R6）──
CN2ARABIC = {
    "〇": 0, "零": 0, "○": 0,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5,
    "陆": 6, "柒": 7, "捌": 8, "玖": 9, "拾": 10,
    "百": 100, "佰": 100, "千": 1000, "仟": 1000,
}
# 中文数字序列（用于连续匹配）
_CN_DIGITS = frozenset("〇零○一二三四五六七八九十壹贰叁肆伍陆柒捌玖拾百佰千仟")


def _cn_page_to_int(s: str) -> int:
    """将中文页码转为阿拉伯数字。支持 1-9999。如"三六"→36，"三十"→30。"""
    s = s.strip().replace(" ", "")
    if not s:
        return 0
    digits = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    multipliers = {"十": 10, "百": 100, "千": 1000, "萬": 10000}
    total = 0
    cur = 0
    for ch in s:
        if ch in digits:
            cur = cur * 10 + digits[ch]
        elif ch in multipliers:
            mult = multipliers[ch]
            if cur == 0:
                cur = 1
            total += cur * mult
            cur = 0
        else:
            # 非数字字符停止解析
            break
    total += cur
    return total


def _extract_page_number(text_part: str) -> int:
    """从文本尾部提取页码。支持阿拉伯、中文数字、汉字后缀。"""
    text_part = text_part.strip()
    # 尝试阿拉伯数字
    m = re.search(r"(\d+)\s*(?:叶|頁|页)?$", text_part)
    if m:
        return int(m.group(1))
    # 尝试中文数字（如"三十"、"三六"）
    m = re.search(rf"([{''.join(_CN_DIGITS)}]+)", text_part)
    if m:
        cn = m.group(1)
        return _cn_page_to_int(cn)
    return 0


def _levenshtein(a: str, b: str) -> float:
    """编辑距离比（1.0=完全匹配，0.0=完全不匹配）。"""
    if not a or not b:
        return 0.0
    # 简短实现：当字符串较短时用 DP（≤20 字符）
    if len(a) > 20 or len(b) > 20:
        # 长串仅用包含检测
        return 1.0 if a in b or b in a else 0.0
    n, m = len(a), len(b)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            temp = dp[j]
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = temp
    return 1.0 - dp[m] / max(n, m)


# ── 常见 OCR 混淆字对（用于 B1 模糊匹配增强）──
_OCR_CONFUSIONS = {
    "日": {"目"},
    "目": {"日", "自"},
    "自": {"目"},
    "隶": {"录", "聿"},
    "录": {"隶", "彔"},
    "彔": {"录"},
    "未": {"末"},
    "末": {"未"},
    "已": {"己", "巳"},
    "己": {"已", "巳"},
    "巳": {"已", "己"},
}

def _ocr_confusable_score(a: str, b: str) -> float:
    """带 OCR 混淆感知的相似度评分。0.0-1.0。"""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        return 0.0
    match = 0
    total = max(len(a), len(b))
    for ca, cb in zip(a, b):
        if ca == cb:
            match += 1
        elif ca in _OCR_CONFUSIONS and cb in _OCR_CONFUSIONS[ca]:
            match += 0.5  # 混淆对半匹配
        elif cb in _OCR_CONFUSIONS and ca in _OCR_CONFUSIONS[cb]:
            match += 0.5
    return match / total if total > 0 else 0.0


def _fuzzy_match_header(line: str, keywords: list[str]) -> bool:
    """B1：NFC 归一化 + 空白正规化 + OCR 混淆感知的目录标题匹配。

    对多行文本，逐行匹配关键词（页面中任一标题行匹配即视为目录页）。
    """
    if not line:
        return False
    norm = unicodedata.normalize("NFC", line)
    for raw_line in norm.split("\n"):
        stripped = raw_line.strip()
        if not stripped or len(stripped) > 100:
            continue
        # 去除空白（含全角空格、零宽字符），使"目　录"→"目录"
        collapsed = re.sub(r"\s", "", stripped)
        if len(collapsed) < 2:
            continue
        for kw in keywords:
            kw_norm = unicodedata.normalize("NFC", kw)
            # 直接子串匹配（collapsed 无空白）
            if kw_norm in collapsed:
                return True
            # OCR 混淆感知匹配（等长时）
            if len(collapsed) == len(kw_norm) and _ocr_confusable_score(collapsed, kw_norm) >= 0.6:
                return True
            # 滑动子窗口编辑距离
            for i in range(max(0, len(collapsed) - len(kw_norm) - 2), len(collapsed)):
                sub = collapsed[i: i + len(kw_norm)]
                if sub and len(sub) >= 2 and _levenshtein(sub, kw_norm) >= 0.75:
                    return True
    return False


# ── 条目行模式（R3 量词上限 {1,200}）──
# 编号前缀: §N / 第N章 / 第N篇 / N.N.N / (N) / ①
_ENTRY_PREFIX = re.compile(
    r"^(§?\s*\d+(?:\.\d{1,3}){0,4}\s*|"         # §N / 1 / 1.1 / 1.1.1
    r"第[一二三四五六七八九十\d]{1,2}[章篇卷节]?\s*|"   # 第N章 / 第一章
    r"[（(]\s*[一二三四五六七八九十\d]+\s*[）)]\s*|"    # （一）/ (1)
    r"[①②③④⑤⑥⑦⑧⑨⑩]\s*|"                            # 圆圈数字
    r"[一二三四五六七八九十]{1,2}[、．.]?\s*)"             # 一、/ 一．
)


def _is_likely_toc_line(line: str) -> bool:
    """判断一行是否像 TOC 条目（含标题+页码模式）。"""
    stripped = line.strip()
    if not stripped or len(stripped) < 6:
        return False
    # 有编号前缀或标题尾部含层级关键词
    if _ENTRY_PREFIX.match(stripped):
        has_page = bool(re.search(r"\d+\s*$", stripped)) or bool(
            re.search(rf"[{''.join(_CN_DIGITS)}]", stripped[-6:])
        )
        return has_page
    # 无编号但有省略号/全角空格分隔符+页码
    if "……" in stripped or "..." in stripped:
        has_page = bool(re.search(r"[\d一二三四五六七八九十]+\s*$", stripped))
        return has_page
    # 尾部直接跟数字（如"内科秘验方……………………1"）
    parts = re.split(r"[．·．・．\s]{2,}", stripped)
    if len(parts) >= 2 and re.search(r"\d", parts[-1]):
        return True
    return False


def _detect_level_from_line(stripped: str, prev_level: int) -> int:
    """根据行内容推导层级 1-5（B2：缩进仅辅助，主策略编号深度+关键词）。"""
    # 检测尾部关键词
    titles = _ENTRY_PREFIX.sub("", stripped).strip()
    # 第 1 层：卷/科/门/部 尾部关键词
    if re.search(r"(卷|科|门|部|類|类)$", titles):
        return 1
    # 第 2 层：章/篇 关键词（含以"第N章"、"第N篇"开头的行）
    if re.match(r"第[\d一二三四五六七八九十零〇]+[章篇]", stripped):
        return 2
    # 第 3 层：节 关键词（含"第N节"、"第一節"、"§N"）
    if re.match(r"(第[\d一二三四五六七八九十零〇]+[节節]|§\s*\d+)", stripped):
        return 3
    # 编号深度检测
    m = _ENTRY_PREFIX.match(stripped)
    if m:
        prefix = m.group(1).strip()
        dot_count = prefix.count(".")
        if dot_count >= 3:
            return 5
        elif dot_count == 2:
            return 4
        elif dot_count == 1:
            return 3
        elif dot_count == 0:
            # 无点号，看是否 §N 或 "第N章"
            if prefix.startswith("§"):
                return 3
            if re.match(r"第[\d一二三四五六七八九十]+[章篇]", prefix):
                return 2
            if re.match(r"[①②③④⑤⑥⑦⑧⑨⑩]|[（(]", prefix):
                return 5
            # 单数字前缀：按 prev_level 推断
            return max(1, prev_level)
    # 缩进检测（B2 辅助）：连续空格数超过 2 可能提级
    leading_spaces = len(stripped) - len(stripped.lstrip())
    if leading_spaces >= 4:
        return min(5, prev_level + 1)
    if leading_spaces >= 2:
        return min(5, prev_level)
    return prev_level


def discover_toc_pages(
    pages_text: list[str],
    keywords: Optional[list[str]] = None,
    min_entries: int = 2,
) -> list[int]:
    """B1 修正：NFC+模糊匹配关键词发现目录页。

    逐页扫描，找出同时满足以下条件的页：
    - 含 TOC 标题关键词（模糊匹配）
    - 含 ≥ min_entries 行"标题+页码"模式的行

    Returns:
        目录页 index 列表。
    """
    if not pages_text:
        return []
    kw = keywords or TOC_HEADER_KEYWORDS
    found: list[int] = []
    for i, text in enumerate(pages_text):
        if not text:
            continue
        header_found = _fuzzy_match_header(text, kw)
        if not header_found:
            continue
        # 统计条目行数
        lines = text.split("\n")
        entry_count = sum(1 for ln in lines if _is_likely_toc_line(ln))
        if entry_count >= min_entries:
            found.append(i)
            _logger.info("[toc] page %d: toc detected (entries=%d)", i, entry_count)
    return found


def parse_toc(
    pages_text: list[str],
    toc_page_nums: list[int],
) -> list[dict]:
    """解析目录页文本为 flat entries。

    B2：缩进主策略降为辅助，主靠编号深度+关键词。
    R5：节标题匹配扩展 "第一节"、"一、"、"（一）"。
    R6：中文数字页码→阿拉伯转换。

    Args:
        pages_text: 全书逐页文本。
        toc_page_nums: discover_toc_pages 发现的目录页号。

    Returns:
        [{"level": int, "title": str, "page": int, "section_no": str}, ...]
    """
    entries: list[dict] = []
    prev_level = 1

    for page_num in toc_page_nums:
        if page_num < 0 or page_num >= len(pages_text):
            continue
        text = pages_text[page_num]
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped or len(stripped) < 4:
                continue

            # 条目必含至少一个中文字符
            if not re.search(r"[\u4e00-\u9fff]", stripped):
                continue

            level = _detect_level_from_line(stripped, prev_level)

            # 提取标题与页码
            # 格式1: "内科秘验方 ………………………… 30"
            # 格式2: "§1 治感冒秘方 30"
            # 格式3: "1.1 特效感冒宁 ………… 1"
            # 格式4: "紫苏 … 30"（无编号）

            # 尝试提取编号
            section_no = ""
            m = _ENTRY_PREFIX.match(stripped)
            if m:
                section_no = m.group(1).strip()
                rest = stripped[m.end():].strip()
            else:
                rest = stripped

            # 从尾部提取页码
            page = _extract_page_number(rest)
            if page == 0:
                # 再试整行尾部
                page = _extract_page_number(stripped)
                if page == 0:
                    # 无页码 → 非条目，跳过
                    continue

            # 移除页码与分隔符取标题
            title = re.sub(
                r"(?:[．·．・．]{3,}|-{3,}|…{2,}|\s{3,}).*$", "", rest
            ).strip()
            if not title:
                title = rest.strip()
            # 从标题末尾去掉数字（页码擦除残留）
            title = re.sub(rf"\s*{re.escape(str(page))}\s*叶?頁?页?$", "", title).strip()
            if not title:
                continue

            prev_level = level
            entries.append({
                "level": level,
                "title": title,
                "page": page,
                "section_no": section_no,
            })

    return entries


def build_toc_tree(entries: list[dict]) -> Optional[TocTree]:
    """将 flat entries 构建为层级树。

    面包屑路径 O(n) 算法。section_no 重复时 warnings（R9 采纳不崩）。

    Returns:
        TocTree 或 None（entries 为空时）。
    """
    if not entries:
        return None

    entries = sorted(entries, key=lambda e: (e.get("page", 0), e.get("level", 1)))
    roots: list[TocEntry] = []
    breadcrumbs: dict[int, TocEntry] = {}  # level → 当前最深层节点
    seen_nos: set[str] = set()
    max_depth = 0

    for e in entries:
        level = e["level"]
        title = e["title"]
        page = e["page"]
        section_no = e.get("section_no", "")

        # R9：section_no 重复时 warnings
        if section_no:
            if section_no in seen_nos:
                _logger.warning("[toc] duplicate section_no: %s (page=%d, title=%s)", section_no, page, title)
            else:
                seen_nos.add(section_no)

        node = TocEntry(level=level, title=title, page=page, section_no=section_no)
        max_depth = max(max_depth, level)

        if level == 1 or not breadcrumbs:
            # 顶级条目
            roots.append(node)
            breadcrumbs = {level: node}
        else:
            # 找父级（level-1）
            parent = breadcrumbs.get(level - 1)
            if parent is None:
                # 无父级时向上找存在的最高层级
                for pl in range(level - 1, 0, -1):
                    parent = breadcrumbs.get(pl)
                    if parent is not None:
                        break
            if parent is None:
                # 完全无父级 → 放 root
                roots.append(node)
            else:
                parent.sub_entries.append(node)
            # 更新面包屑：清除本层及以下的面包屑
            breadcrumbs = {k: v for k, v in breadcrumbs.items() if k < level}
            breadcrumbs[level] = node

    return TocTree(max_depth=max_depth, entries=roots)


def build_toc(pages_text: list[str]) -> Optional[TocTree]:
    """B5 修正：无目录页时返回 None。便利函数：discover → parse → build。"""
    toc_pages = discover_toc_pages(pages_text)
    if not toc_pages:
        return None
    entries = parse_toc(pages_text, toc_pages)
    if not entries:
        return None
    return build_toc_tree(entries)


def enrich_book_result(result: BookResult) -> BookResult:
    """B3 修正：就地修改 result.toc 后返回同一引用（非深拷贝）。

    R2 交叉验证预留：可扩展为接受 heading_map 做对齐校验。
    """
    if not result.pages:
        return result
    texts = [p.text for p in result.pages]
    result.toc = build_toc(texts)
    return result
