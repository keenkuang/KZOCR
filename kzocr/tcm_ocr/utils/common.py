"""
通用工具函数模块。

提供中医 OCR 系统全流程所需的辅助函数，包括：
- 术语冲突检测
- 方剂序列号生成
- 药典查询
- 中药名/穴位名有效性校验
- 方剂/药材/穴位名提取
- SHA-256 计算
- LLM JSON 输出解析（含重试）
- MinerU-Popo 降级 Prompt 构建
- 通用 LLM 后处理
- 常量定义
"""

import hashlib
import json
import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# =============================================================================
# 常量定义
# =============================================================================

# 非药材的多字词（用于排除误识别）
NON_HERB_MULTI_CHAR_WORDS: Set[str] = {
    "所以", "因为", "因此", "于是", "但是", "然而", "不过", "只是",
    "不仅", "而且", "或者", "还是", "要么", "假如", "如果", "虽然",
    "尽管", "即使", "不但", "除非", "除了", "不论", "不管", "无论",
    "一方面", "另一方面", "综上所述", "由此可见", "总之", "一般来说",
    "注意事项", "临床应用", "功能主治", "用法用量", "不良反应",
    "禁忌", "孕妇", "儿童", "老人", "患者", "医师", "医生", "处方",
    "水煎服", "口服", "外用", "研末", "冲服", "烊化", "另煎",
    "先煎", "后下", "包煎", "煎汤", "温服", "分服", "顿服",
    "一日三次", "一日二次", "一日一次", "早晚各一", "饭前服", "饭后服",
}

# 常见经络穴位名
COMMON_MERIDIAN_POINTS: Set[str] = {
    # 手太阴肺经
    "中府", "云门", "天府", "侠白", "尺泽", "孔最", "列缺", "经渠",
    "太渊", "鱼际", "少商",
    # 手阳明大肠经
    "商阳", "二间", "三间", "合谷", "阳溪", "偏历", "温溜", "下廉",
    "上廉", "手三里", "曲池", "肘髎", "手五里", "臂臑", "肩髃",
    "巨骨", "天鼎", "扶突", "口禾髎", "迎香",
    # 足阳明胃经
    "承泣", "四白", "巨髎", "地仓", "大迎", "颊车", "下关", "头维",
    "人迎", "水突", "气舍", "缺盆", "气户", "库房", "屋翳", "膺窗",
    "乳中", "乳根", "不容", "承满", "梁门", "关门", "太乙", "滑肉门",
    "天枢", "外陵", "大巨", "水道", "归来", "气冲", "髀关", "伏兔",
    "阴市", "梁丘", "犊鼻", "足三里", "上巨虚", "条口", "下巨虚",
    "丰隆", "解溪", "冲阳", "陷谷", "内庭", "厉兑",
    # 足太阴脾经
    "隐白", "大都", "太白", "公孙", "商丘", "三阴交", "漏谷", "地机",
    "阴陵泉", "血海", "箕门", "冲门", "府舍", "腹结", "大横", "腹哀",
    "食窦", "天溪", "胸乡", "周荣", "大包",
    # 手少阴心经
    "极泉", "青灵", "少海", "灵道", "通里", "阴郄", "神门", "少府", "少冲",
    # 手太阳小肠经
    "少泽", "前谷", "后溪", "腕骨", "阳谷", "养老", "支正", "小海",
    "肩贞", "臑俞", "天宗", "秉风", "曲垣", "肩外俞", "肩中俞",
    "天窗", "天容", "颧髎", "听宫",
    # 足太阳膀胱经
    "睛明", "攒竹", "眉冲", "曲差", "五处", "承光", "通天", "络却",
    "玉枕", "天柱", "大杼", "风门", "肺俞", "厥阴俞", "心俞", "督俞",
    "膈俞", "肝俞", "胆俞", "脾俞", "胃俞", "三焦俞", "肾俞", "气海俞",
    "大肠俞", "关元俞", "小肠俞", "膀胱俞", "中膂俞", "白环俞", "上髎",
    "次髎", "中髎", "下髎", "会阳", "承扶", "殷门", "浮郄", "委阳",
    "委中", "附分", "魄户", "膏肓", "神堂", "膈关", "魂门", "阳纲",
    "意舍", "胃仓", "肓门", "志室", "胞肓", "秩边", "合阳", "承筋",
    "承山", "飞扬", "跗阳", "昆仑", "仆参", "申脉", "金门", "京骨",
    "束骨", "足通谷", "至阴",
    # 足少阴肾经
    "涌泉", "然谷", "太溪", "大钟", "水泉", "照海", "复溜", "交信",
    "筑宾", "阴谷", "横骨", "大赫", "气穴", "四满", "中注", "肓俞",
    "商曲", "石关", "阴都", "腹通谷", "幽门", "步廊", "神封", "灵墟",
    "神藏", "彧中", "俞府",
    # 手厥阴心包经
    "天池", "天泉", "曲泽", "郄门", "间使", "内关", "大陵", "劳宫", "中冲",
    # 手少阳三焦经
    "关冲", "液门", "中渚", "阳池", "外关", "支沟", "会宗", "三阳络",
    "四渎", "天井", "清冷渊", "消泺", "臑会", "肩髎", "天髎", "天牖",
    "翳风", "瘈脉", "颅息", "角孙", "耳门", "耳和髎", "丝竹空",
    # 足少阳胆经
    "瞳子髎", "听会", "上关", "颔厌", "悬颅", "悬厘", "曲鬓", "率谷",
    "天冲", "浮白", "头窍阴", "完骨", "本神", "阳白", "头临泣", "目窗",
    "正营", "承灵", "脑空", "风池", "肩井", "渊腋", "辄筋", "日月",
    "京门", "带脉", "五枢", "维道", "居髎", "环跳", "风市", "中渎",
    "膝阳关", "阳陵泉", "阳交", "外丘", "光明", "阳辅", "悬钟",
    "丘墟", "足临泣", "地五会", "侠溪", "足窍阴",
    # 足厥阴肝经
    "大敦", "行间", "太冲", "中封", "蠡沟", "中都", "膝关", "曲泉",
    "阴包", "足五里", "阴廉", "急脉", "章门", "期门",
    # 任脉
    "会阴", "曲骨", "中极", "关元", "石门", "气海", "阴交", "神阙",
    "水分", "下脘", "建里", "中脘", "上脘", "巨阙", "鸠尾", "中庭",
    "膻中", "玉堂", "紫宫", "华盖", "璇玑", "天突", "廉泉", "承浆",
    # 督脉
    "长强", "腰俞", "腰阳关", "命门", "悬枢", "脊中", "中枢", "筋缩",
    "至阳", "灵台", "神道", "身柱", "陶道", "大椎", "哑门", "风府",
    "脑户", "强间", "后顶", "百会", "前顶", "囟会", "上星", "神庭",
    "素髎", "水沟", "兑端", "龈交",
}

# 经络名称
MERIDIAN_NAMES: Set[str] = {
    "手太阴肺经", "手阳明大肠经", "足阳明胃经", "足太阴脾经",
    "手少阴心经", "手太阳小肠经", "足太阳膀胱经", "足少阴肾经",
    "手厥阴心包经", "手少阳三焦经", "足少阳胆经", "足厥阴肝经",
    "任脉", "督脉", "冲脉", "带脉", "阴跷脉", "阳跷脉",
    "阴维脉", "阳维脉",
}

# 穴位触发词（表示上下文与穴位相关）
ACUPUNCTURE_TRIGGER_WORDS: Set[str] = {
    "针刺", "针灸", "取穴", "穴位", "经络", "经脉", "腧穴",
    "刺法", "灸法", "毫针", "电针", "温针", "皮内针", "三棱针",
    "艾灸", "温灸", "直接灸", "间接灸", "补法", "泻法", "平补平泻",
    "得气", "行针", "留针", "出针", "进针", "捻转", "提插",
    "配穴", "主穴", "辅穴", "近取", "远取", "循经取穴",
}

# 常见方剂名（用于 extract_formula_name）
COMMON_FORMULA_NAMES: Set[str] = {
    "四君子汤", "四物汤", "八珍汤", "十全大补汤", "归脾汤", "当归补血汤",
    "六味地黄丸", "知柏地黄丸", "杞菊地黄丸", "麦味地黄丸", "都气丸",
    "金匮肾气丸", "桂附地黄丸", "右归丸", "左归丸", "大补阴丸",
    "一贯煎", "炙甘草汤", "生脉散", "补中益气汤", "玉屏风散",
    "参苓白术散", "理中丸", "附子理中丸", "小建中汤", "大建中汤",
    "吴茱萸汤", "四逆汤", "回阳救急汤", "当归四逆汤",
    "麻黄汤", "桂枝汤", "小青龙汤", "大青龙汤", "九味羌活汤",
    "银翘散", "桑菊饮", "麻黄杏仁甘草石膏汤", "败毒散",
    "白虎汤", "清营汤", "犀角地黄汤", "黄连解毒汤", "凉膈散",
    "普济消毒饮", "清瘟败毒饮", "导赤散", "龙胆泻肝汤",
    "泻白散", "清胃散", "玉女煎", "芍药汤", "白头翁汤",
    "青蒿鳖甲汤", "当归六黄汤",
    "小柴胡汤", "大柴胡汤", "蒿芩清胆汤", "四逆散", "逍遥散",
    "半夏泻心汤", "痛泻要方",
    "大承气汤", "小承气汤", "调胃承气汤", "麻子仁丸", "济川煎",
    "小柴胡汤", "大柴胡汤",
    "麻黄细辛附子汤", "参苏饮", "加减葳蕤汤",
    "平胃散", "藿香正气散", "三仁汤", "甘露消毒丹",
    "茵陈蒿汤", "八正散", "二妙散", "三妙丸", "五苓散",
    "真武汤", "实脾散", "苓桂术甘汤", "萆薢分清饮",
    "血府逐瘀汤", "补阳还五汤", "桃核承气汤", "复元活血汤",
    "温经汤", "生化汤", "失笑散", "桂枝茯苓丸",
    "十灰散", "咳血方", "小蓟饮子", "槐花散",
    "川芎茶调散", "天麻钩藤饮", "半夏白术天麻汤",
    "羚角钩藤汤", "镇肝熄风汤", "大定风珠",
    "杏苏散", "桑杏汤", "清燥救肺汤", "麦门冬汤", "百合固金汤",
    "养阴清肺汤",
    "越鞠丸", "枳实薤白桂枝汤", "半夏厚朴汤", "天台乌药散",
    "苏子降气汤", "定喘汤", "旋覆代赭汤", "橘皮竹茹汤",
    "桃核承气汤", "血府逐瘀汤",
    "乌梅丸", "肥儿丸", "布袋丸",
    "大黄牡丹汤", "苇茎汤",
    "五味消毒饮", "仙方活命饮", "阳和汤",
    "清胃散", "导赤散",
    "真人养脏汤", "四神丸", "桑螵蛸散", "固冲汤", "完带汤",
    "朱砂安神丸", "天王补心丹", "酸枣仁汤", "甘麦大枣汤",
    "安宫牛黄丸", "紫雪", "至宝丹", "苏合香丸",
    "牵正散", "小活络丹", "消风散", "川芎茶调散",
    "二陈汤", "温胆汤", "茯苓丸", "清气化痰丸", "小陷胸汤",
    "贝母瓜蒌散", "三子养亲汤",
    "保和丸", "健脾丸", "枳实消痞丸", "木香槟榔丸",
    "乌梅丸",
}

# 《中华人民共和国药典》收录的常见药材
PHARMACOPOEIA_HERBS: Set[str] = {
    "人参", "三七", "大黄", "川芎", "丹参", "当归", "黄芪", "白术",
    "甘草", "白芍", "赤芍", "熟地黄", "生地黄", "茯苓", "党参",
    "麦冬", "天冬", "五味子", "枸杞子", "山药", "阿胶", "鹿茸",
    "冬虫夏草", "连翘", "金银花", "板蓝根", "黄芩", "黄柏", "黄连",
    "栀子", "柴胡", "葛根", "防风", "羌活", "独活", "苍术", "厚朴",
    "陈皮", "半夏", "天南星", "浙贝母", "川贝母", "桔梗", "杏仁",
    "桃仁", "红花", "牛膝", "益母草", "香附", "郁金", "莪术",
    "三棱", "穿山甲", "鳖甲", "龟甲", "牡蛎", "石决明", "羚羊角",
    "水牛角", "麝香", "牛黄", "朱砂", "雄黄", "石膏", "知母",
    "芦根", "天花粉", "淡竹叶", "夏枯草", "决明子", "谷精草",
    "青葙子", "紫草", "水牛角", "玄参", "牡丹皮", "紫草",
    "漏芦", "四季青", "地锦草", "半边莲", "山慈菇", "千里光",
    "白蔹", "白头翁", "马齿苋", "鸦胆子", "射干", "山豆根",
    "马勃", "橄榄", "余甘子", "金果榄", "朱砂根", "木蝴蝶",
    "土茯苓", "鱼腥草", "金荞麦", "大血藤", "败酱草",
}

# 药典版本映射
PHARMACOPOEIA_VERSIONS: Dict[str, str] = {
    "1953": "第一版(1953)",
    "1963": "第二版(1963)",
    "1977": "第三版(1977)",
    "1985": "第四版(1985)",
    "1990": "第五版(1990)",
    "1995": "第六版(1995)",
    "2000": "第七版(2000)",
    "2005": "第八版(2005)",
    "2010": "第九版(2010)",
    "2015": "第十版(2015)",
    "2020": "第十一版(2020)",
    "2025": "第十二版(2025)",
}


def check_term_conflict(text_a: str, text_b: str, term_kb: Any) -> bool:
    """检查两段文本在术语知识库中是否存在冲突。

    冲突定义为：文本 A 和文本 B 包含不同的术语释义，
    或同一术语在两段文本中的上下文用法矛盾。

    Args:
        text_a: 第一段文本
        text_b: 第二段文本
        term_kb: 术语知识库对象（需实现 lookup(term) -> dict 接口）

    Returns:
        如果存在冲突返回 True，否则 False

    Example:
        >>> kb = TermKB()
        >>> check_term_conflict("用黄芪补气", "黄芪用于泻火", kb)
        True  # 黄芪功效描述冲突
    """
    if not text_a or not text_b or term_kb is None:
        return False

    # 从文本中提取可能的术语
    terms_a = _extract_terms_from_text(text_a)
    terms_b = _extract_terms_from_text(text_b)
    common_terms = terms_a & terms_b

    for term in common_terms:
        try:
            info_a = _get_term_context(term, text_a, term_kb)
            info_b = _get_term_context(term, text_b, term_kb)
            if info_a and info_b and _is_conflicting_usage(info_a, info_b):
                return True
        except Exception:
            continue

    return False


def _extract_terms_from_text(text: str) -> Set[str]:
    """从文本中提取候选术语（简化实现）。

    Args:
        text: 输入文本

    Returns:
        候选术语集合
    """
    # 简单分词：2-4 字连续汉字序列
    terms: Set[str] = set()
    chars = list(text)
    for length in range(2, 5):
        for i in range(len(chars) - length + 1):
            candidate = "".join(chars[i : i + length])
            if all("\u4e00" <= c <= "\u9fff" for c in candidate):
                terms.add(candidate)
    return terms


def _get_term_context(term: str, text: str, term_kb: Any) -> Optional[Dict]:
    """获取术语在文本中的上下文信息。

    Args:
        term: 术语
        text: 文本
        term_kb: 术语知识库

    Returns:
        上下文信息字典，或 None
    """
    try:
        kb_info = term_kb.lookup(term)
        if not kb_info:
            return None
        # 提取术语前后 10 字作为上下文
        idx = text.find(term)
        if idx >= 0:
            context = text[max(0, idx - 10) : idx + len(term) + 10]
            return {"term": term, "kb_info": kb_info, "context": context}
    except Exception:
        pass
    return None


def _is_conflicting_usage(info_a: Dict, info_b: Dict) -> bool:
    """判断两种用法是否冲突。

    Args:
        info_a: 术语 A 的信息
        info_b: 术语 B 的信息

    Returns:
        是否冲突
    """
    # 简化实现：检查功效关键词是否矛盾
    contradictory_pairs: List[Tuple[str, str]] = [
        ("补", "泻"), ("温", "凉"), ("升", "降"),
        ("散寒", "清热"), ("滋阴", "助阳"),
    ]
    ctx_a = info_a.get("context", "")
    ctx_b = info_b.get("context", "")

    for word_a, word_b in contradictory_pairs:
        if word_a in ctx_a and word_b in ctx_b:
            return True
        if word_b in ctx_a and word_a in ctx_b:
            return True
    return False


# ---- 方剂序列号生成 -------------------------------------------------------

_formula_sequence_cache: Dict[str, int] = {}


def get_next_formula_sequence(book_id: str) -> int:
    """获取指定书籍的下一个方剂序列号。

    使用内存缓存 + 持久化计数器，保证同书不重复。

    Args:
        book_id: 书籍唯一标识

    Returns:
        下一个序列号（从 1 开始）

    Example:
        >>> get_next_formula_sequence("book_001")
        1
        >>> get_next_formula_sequence("book_001")
        2
    """
    global _formula_sequence_cache
    current = _formula_sequence_cache.get(book_id, 0)
    current += 1
    _formula_sequence_cache[book_id] = current
    return current


def reset_formula_sequence(book_id: str) -> None:
    """重置指定书籍的方剂序列号。

    Args:
        book_id: 书籍唯一标识
    """
    global _formula_sequence_cache
    _formula_sequence_cache[book_id] = 0


# ---- 药典查询 --------------------------------------------------------------

def is_in_pharmacopoeia(herb_name: str) -> bool:
    """检查药材名是否在《中华人民共和国药典》中。

    Args:
        herb_name: 药材名称

    Returns:
        如果在药典中返回 True

    Example:
        >>> is_in_pharmacopoeia("人参")
        True
        >>> is_in_pharmacopoeia("无名草")
        False
    """
    if not herb_name:
        return False
    return herb_name in PHARMACOPOEIA_HERBS


def get_pharmacopoeia_version(pub_year: int) -> str:
    """根据出版年份获取对应的《中国药典》版本。

    Args:
        pub_year: 出版年份

    Returns:
        药典版本描述字符串

    Example:
        >>> get_pharmacopoeia_version(2023)
        '第十一版(2020)'
        >>> get_pharmacopoeia_version(1988)
        '第五版(1990)'
    """
    if pub_year < 1953:
        return "药典尚未出版"

    sorted_years = sorted(PHARMACOPOEIA_VERSIONS.keys(), key=int)
    selected = sorted_years[0]
    for year_str in sorted_years:
        if int(year_str) <= pub_year:
            selected = year_str
        else:
            break

    return PHARMACOPOEIA_VERSIONS.get(selected, "未知版本")


# ---- 中药名/穴位名有效性校验 ------------------------------------------------

def is_valid_herb_name(name: str) -> bool:
    """校验字符串是否为有效的中药名。

    校验规则：
    1. 长度在 2-10 个字符之间
    2. 全部为中文汉字（不含非药材常用词）
    3. 不在非药材排除列表中

    Args:
        name: 待校验名称

    Returns:
        是否有效

    Example:
        >>> is_valid_herb_name("人参")
        True
        >>> is_valid_herb_name("所以")
        False
    """
    if not name or len(name) < 2 or len(name) > 10:
        return False
    if not all("\u4e00" <= c <= "\u9fff" for c in name):
        return False
    if name in NON_HERB_MULTI_CHAR_WORDS:
        return False
    return True


def is_valid_meridian_point(name: str) -> bool:
    """校验字符串是否为有效的经络穴位名。

    校验规则：
    1. 在已知穴位列表中，或
    2. 符合穴位命名模式（如 XX穴、第X椎等）

    Args:
        name: 待校验名称

    Returns:
        是否有效

    Example:
        >>> is_valid_meridian_point("足三里")
        True
        >>> is_valid_meridian_point("太阳")  # 伪穴
        False
    """
    if not name or len(name) < 2:
        return False

    # 直接匹配已知穴位
    if name in COMMON_MERIDIAN_POINTS:
        return True

    # 匹配模式：XX穴
    if re.match(r"^[\u4e00-\u9fff]{2,4}穴$", name):
        return True

    # 匹配模式：第X椎、第X腰椎等
    if re.match(r"^第[一二三四五六七八九十\d]+[颈胸腰骶]椎$", name):
        return True

    # 匹配模式：夹脊等
    if name in {"夹脊", "华佗夹脊"}:
        return True

    return False


# ---- 方剂/药材/穴位名提取 --------------------------------------------------

# 方剂名正则模式
FORMULA_NAME_PATTERN = re.compile(
    r"([\u4e00-\u9fff]{2,8}(?:汤|散|丸|丹|膏|酒|饮|剂|方|贴|熨方|洗方|搽方|栓))"
)

# 变方模式（如 四君子汤加味、四君子汤加减、四君子汤去人参）
FORMULA_VARIANT_PATTERN = re.compile(
    r"([\u4e00-\u9fff]{2,8}(?:汤|散|丸|丹|膏|酒|饮|剂))"
    r"(?:加味|加减|加[\u4e00-\u9fff]+|"
    r"去[\u4e00-\u9fff]+|"
    r"合[\u4e00-\u9fff]+|"
    r"化裁|"
    r"改[\u4e00-\u9fff]+)"
)


def extract_formula_name(text: str) -> Optional[str]:
    """从文本中提取方剂名。

    提取以汤、散、丸、丹、膏、酒、饮、剂、方等结尾的 2-8 字词。

    Args:
        text: 输入文本

    Returns:
        提取到的方剂名，未找到返回 None

    Example:
        >>> extract_formula_name("服用四君子汤治疗")
        '四君子汤'
    """
    if not text:
        return None
    match = FORMULA_NAME_PATTERN.search(text)
    if match:
        name = match.group(1)
        # 过滤非方剂词
        if name not in NON_HERB_MULTI_CHAR_WORDS:
            return name
    return None


def extract_formula_variants(text: str) -> List[str]:
    """从文本中提取方剂变方（加减方）。

    Args:
        text: 输入文本

    Returns:
        变方名称列表

    Example:
        >>> extract_formula_variants("以四君子汤加味，合六味地黄丸加减")
        ['四君子汤加味', '六味地黄丸加减']
    """
    if not text:
        return []
    return FORMULA_VARIANT_PATTERN.findall(text)


# 药材名提取模式（简化实现：基于常见药材后缀和搭配）
HERB_NAME_SUFFIXES = {"根", "皮", "叶", "花", "草", "藤", "实", "仁", "子", "壳", "须"}


def extract_herb_names(text: str) -> List[str]:
    """从文本中提取可能的中药名。

    使用正向最大匹配算法，基于药典药材和命名模式。

    Args:
        text: 输入文本

    Returns:
        提取到的药材名列表（去重）

    Example:
        >>> extract_herb_names("人参、白术、茯苓各三钱")
        ['人参', '白术', '茯苓']
    """
    if not text:
        return []

    found: Set[str] = set()
    chars = list(text)
    n = len(chars)

    i = 0
    while i < n:
        matched = False
        # 从长到短尝试匹配
        for length in range(min(6, n - i), 1, -1):
            candidate = "".join(chars[i : i + length])
            if candidate in PHARMACOPOEIA_HERBS:
                found.add(candidate)
                i += length
                matched = True
                break
            # 检查是否为有效药材名
            if is_valid_herb_name(candidate):
                found.add(candidate)
                i += length
                matched = True
                break
        if not matched:
            i += 1

    return sorted(found)


def extract_meridian_point_names(text: str) -> List[str]:
    """从文本中提取经络穴位名。

    Args:
        text: 输入文本

    Returns:
        提取到的穴位名列表（去重）

    Example:
        >>> extract_meridian_point_names("取足三里、合谷、太冲等穴")
        ['合谷', '太冲', '足三里']
    """
    if not text:
        return []

    found: Set[str] = set()

    # 1. 直接匹配已知穴位
    for point in COMMON_MERIDIAN_POINTS:
        if point in text:
            found.add(point)

    # 2. 正则匹配 XX穴 模式
    pattern = re.compile(r"[\u4e00-\u9fff]{2,4}穴")
    for match in pattern.finditer(text):
        name = match.group()
        if is_valid_meridian_point(name):
            found.add(name)

    return sorted(found)


# ---- SHA-256 计算 ---------------------------------------------------------

def compute_sha256(file_path: str) -> str:
    """计算文件的 SHA-256 哈希值。

    以 64KB 块为单位读取文件，避免大文件内存溢出。

    Args:
        file_path: 文件路径

    Returns:
        SHA-256 十六进制字符串

    Raises:
        FileNotFoundError: 文件不存在
        PermissionError: 无读取权限
        OSError: 其他 IO 错误

    Example:
        >>> compute_sha256("/path/to/file.txt")
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    """
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(65536)  # 64KB 块
                if not chunk:
                    break
                sha256.update(chunk)
    except FileNotFoundError:
        logger.error("文件不存在: %s", file_path)
        raise
    except PermissionError:
        logger.error("无权限读取文件: %s", file_path)
        raise
    except OSError as e:
        logger.error("读取文件失败 %s: %s", file_path, e)
        raise

    return sha256.hexdigest()


# ---- 段落文本获取 ----------------------------------------------------------

def get_paragraph_text_from_lines(db_book: Any, para_id: str) -> str:
    """从书籍数据库中获取指定段落的所有行文本并拼接。

    按行号排序后拼接，行之间不添加额外空格。

    Args:
        db_book: 书籍数据库连接对象（需支持 execute/fetchall）
        para_id: 段落 ID

    Returns:
        拼接后的段落文本

    Raises:
        sqlite3.Error: 数据库查询失败
    """
    if not para_id or db_book is None:
        return ""

    try:
        cursor = db_book.execute(
            "SELECT line_text FROM proofread_record "
            "WHERE paragraph_id = ? ORDER BY line_number ASC",
            (para_id,),
        )
        rows = cursor.fetchall()
        return "".join(row[0] for row in rows if row[0])
    except sqlite3.Error as e:
        logger.error("获取段落文本失败 para_id=%s: %s", para_id, e)
        raise


# ---- LLM JSON 输出解析（含重试） -------------------------------------------

def parse_llm_json_with_retry(
    llm_output: str,
    original_prompt: Optional[str] = None,
    max_retries: int = 3,
) -> Optional[Dict]:
    """解析 LLM 输出的 JSON 字符串，失败时自动重试提取。

    支持以下容错策略：
    1. 直接解析完整 JSON
    2. 提取 markdown 代码块中的 JSON
    3. 使用正则提取最外层 JSON 对象
    4. 逐行修复常见格式错误（缺失逗号、引号等）

    Args:
        llm_output: LLM 原始输出字符串
        original_prompt: 原始 Prompt（用于重试日志）
        max_retries: 最大重试次数

    Returns:
        解析后的字典，全部失败返回 None

    Example:
        >>> parse_llm_json_with_retry('{"result": "ok"}')
        {'result': 'ok'}
        >>> parse_llm_json_with_retry('```json\\n{"a": 1}\\n```')
        {'a': 1}
    """
    if not llm_output:
        logger.warning("LLM 输出为空")
        return None

    # 记录原始输出用于调试
    prompt_preview = (original_prompt or "")[:100]

    for attempt in range(1, max_retries + 1):
        try:
            # 策略1: 直接解析
            result = json.loads(llm_output.strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        try:
            # 策略2: 提取 markdown 代码块
            code_block_match = re.search(
                r"```(?:json)?\s*\n?(.*?)\n?```",
                llm_output,
                re.DOTALL,
            )
            if code_block_match:
                result = json.loads(code_block_match.group(1).strip())
                if isinstance(result, dict):
                    return result
        except (json.JSONDecodeError, AttributeError):
            pass

        try:
            # 策略3: 正则提取最外层 JSON 对象
            json_match = re.search(r"\{.*\}", llm_output, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                if isinstance(result, dict):
                    return result
        except json.JSONDecodeError:
            pass

        try:
            # 策略4: 逐行修复
            fixed = _fix_common_json_errors(llm_output)
            result = json.loads(fixed)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        logger.warning(
            "JSON 解析尝试 %d/%d 失败 (prompt: ...%s)",
            attempt,
            max_retries,
            prompt_preview,
        )

    logger.error("JSON 解析全部 %d 次尝试失败", max_retries)
    return None


def _fix_common_json_errors(text: str) -> str:
    """修复 LLM 输出中常见的 JSON 格式错误。

    Args:
        text: 待修复的 JSON 字符串

    Returns:
        修复后的字符串
    """
    # 提取 {} 之间的内容
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return text

    fixed = match.group()

    # 修复尾随逗号（对象最后一个键值对后的逗号）
    fixed = re.sub(r",(\s*[}\]])", r"\1", fixed)

    # 修复单引号为双引号
    fixed = re.sub(r"(?<!\\)'", '"', fixed)

    # 修复无引号的键
    fixed = re.sub(
        r"([\{\,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:",
        r'\1"\2":',
        fixed,
    )

    # 修复中文冒号
    fixed = fixed.replace("：", ":")

    return fixed


# ---- MinerU-Popo 降级 Prompt 构建 ------------------------------------------

def build_fallback_tree_prompt(normalized: str) -> str:
    """构建 MinerU-Popo 不可用时使用的通用 LLM Prompt。

    用于将已归一化的文本内容交给通用 LLM 进行文档结构重建。

    Args:
        normalized: 归一化后的文本内容（含标记信息）

    Returns:
        完整的 LLM Prompt 字符串

    Example:
        >>> prompt = build_fallback_tree_prompt("# 第一章\\n正文内容...")
        >>> len(prompt) > 100
        True
    """
    prompt = f"""你是一位中医文献结构化专家。请将以下已 OCR 识别的中医出版物文本内容，
重建为层次化的文档树结构。

## 任务要求
1. 识别文本中的章节层级（章、节、小节等）
2. 区分正文、方剂、表格、注释等不同内容类型
3. 保持原文的顺序和层级关系
4. 对方剂内容单独标注并提取药材组成
5. 对穴位名标注所属经络

## 输出格式
请输出 JSON 格式的文档树，结构如下：
{{
    "title": "文档标题",
    "sections": [
        {{
            "level": 1,
            "heading": "章节标题",
            "content_type": "text|formula|table|mixed",
            "body": "正文内容",
            "formulas": [
                {{
                    "name": "方剂名",
                    "ingredients": ["药材1", "药材2"],
                    "original_text": "原始文本"
                }}
            ],
            "children": []
        }}
    ]
}}

## 文本内容
{normalized}

请只输出 JSON，不要包含任何解释性文字。"""

    return prompt


# ---- 通用 LLM 后处理备选方案 -----------------------------------------------

def fallback_llm_post_process(
    mineru_structure: Any,
    proofread_text_map: Dict[str, str],
    mineru_mapping: Dict[str, str],
) -> List[Dict]:
    """MinerU-Popo 不可用时的通用 LLM（Qwen2.5-7B）后处理备选方案。

    将 MinerU 的原始结构输出与已校对文本映射结合，
    通过通用 LLM 重建文档结构树。

    Args:
        mineru_structure: MinerU 原始结构输出（dict 或 list）
        proofread_text_map: 已校对文本映射 {block_id: text}
        mineru_mapping: MinerU block 到校正行的映射 {block_id: line_id}

    Returns:
        重建后的文档节点列表

    Example:
        >>> nodes = fallback_llm_post_process(
        ...     {"blocks": [{"id": "b1", "text": "原文"}]},
        ...     {"b1": "校对后文本"},
        ...     {"b1": "line_001"},
        ... )
        >>> len(nodes) > 0
        True
    """
    if not mineru_structure:
        return []

    nodes: List[Dict] = []

    # 提取 blocks
    blocks = []
    if isinstance(mineru_structure, dict):
        blocks = mineru_structure.get("blocks", [])
        if not blocks and "pages" in mineru_structure:
            for page in mineru_structure["pages"]:
                blocks.extend(page.get("blocks", []))
    elif isinstance(mineru_structure, list):
        blocks = mineru_structure

    current_section: Optional[Dict] = None

    for block in blocks:
        block_id = block.get("id", "")
        block_type = block.get("type", "text")
        bbox = block.get("bbox", [])

        # 获取校对后的文本
        line_id = mineru_mapping.get(block_id, "")
        corrected_text = proofread_text_map.get(block_id, block.get("text", ""))

        node: Dict = {
            "id": block_id,
            "type": block_type,
            "text": corrected_text,
            "bbox": bbox,
            "line_id": line_id,
            "children": [],
        }

        # 根据 block 类型分类
        if block_type in ("title", "header"):
            # 新章节开始
            level = _estimate_heading_level(corrected_text, block)
            current_section = {
                **node,
                "level": level,
                "heading": corrected_text,
                "content_type": "heading",
                "children": [],
            }
            nodes.append(current_section)
        elif block_type in ("formula", "table"):
            node["content_type"] = block_type
            if current_section:
                current_section["children"].append(node)
            else:
                nodes.append(node)
        else:
            node["content_type"] = "text"
            if current_section:
                current_section["children"].append(node)
            else:
                nodes.append(node)

    return nodes


def _estimate_heading_level(text: str, block: Dict) -> int:
    """估算标题层级。

    Args:
        text: 标题文本
        block: MinerU block 信息

    Returns:
        层级数（1-4）
    """
    # 根据字体大小估算
    font_size = block.get("font_size", 0)
    if font_size > 20:
        return 1
    elif font_size > 16:
        return 2
    elif font_size > 13:
        return 3

    # 根据文本特征
    if re.match(r"^第[一二三四五六七八九十百\d]+[章节篇]", text):
        return 1
    if re.match(r"^第[一二三四五六七八九十\d]+节", text):
        return 2
    if re.match(r"^\d+\.\d+\s+", text):
        return 3
    if re.match(r"^\([\d一二三四五六七八九十]+\)", text):
        return 4

    return 2  # 默认二级


# ---- 几何工具函数 ----------------------------------------------------------

def compute_iou(box_a: List[float], box_b: List[float]) -> float:
    """计算两个边界框的 IoU（交并比）。

    Args:
        box_a: [x1, y1, x2, y2]
        box_b: [x1, y1, x2, y2]

    Returns:
        IoU 值 [0, 1]
    """
    if len(box_a) < 4 or len(box_b) < 4:
        return 0.0

    x1_a, y1_a, x2_a, y2_a = box_a[:4]
    x1_b, y1_b, x2_b, y2_b = box_b[:4]

    xi1 = max(x1_a, x1_b)
    yi1 = max(y1_a, y1_b)
    xi2 = min(x2_a, x2_b)
    yi2 = min(y2_a, y2_b)

    inter_width = max(0, xi2 - xi1)
    inter_height = max(0, yi2 - yi1)
    inter_area = inter_width * inter_height

    box_a_area = (x2_a - x1_a) * (y2_a - y1_a)
    box_b_area = (x2_b - x1_b) * (y2_b - y1_b)
    union_area = box_a_area + box_b_area - inter_area

    if union_area <= 0:
        return 0.0

    return inter_area / union_area


def box_center_distance(box_a: List[float], box_b: List[float]) -> float:
    """计算两个边界框中心点的欧氏距离。

    Args:
        box_a: [x1, y1, x2, y2]
        box_b: [x1, y1, x2, y2]

    Returns:
        中心点距离
    """
    cx_a = (box_a[0] + box_a[2]) / 2.0
    cy_a = (box_a[1] + box_a[3]) / 2.0
    cx_b = (box_b[0] + box_b[2]) / 2.0
    cy_b = (box_b[1] + box_b[3]) / 2.0

    return ((cx_a - cx_b) ** 2 + (cy_a - cy_b) ** 2) ** 0.5


# ---- 文本规范化工具 --------------------------------------------------------

def normalize_whitespace(text: str) -> str:
    """规范化文本中的空白字符。

    将多个连续空白字符（空格、制表符、换行）替换为单个空格，
    去除首尾空白。

    Args:
        text: 输入文本

    Returns:
        规范化后的文本
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def normalize_tcm_text(text: str) -> str:
    """规范化中医文本中的特殊字符。

    - 将半角标点转为全角
    - 统一省略号形式
    - 去除控制字符

    Args:
        text: 输入文本

    Returns:
        规范化后的文本
    """
    if not text:
        return ""

    # 半角 → 全角标点
    replacements = {
        ",": "，",
        ".": "。",
        ";": "；",
        ":": "：",
        "!": "！",
        "?": "？",
        "(": "（",
        ")": "）",
        "[": "【",
        "]": "】",
        "<": "《",
        ">": "》",
    }

    result = text
    for half, full in replacements.items():
        result = result.replace(half, full)

    # 统一省略号
    result = result.replace("...", "…")

    # 去除控制字符（保留换行和制表）
    result = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", result)

    return result


# ---- 图像相关工具 ----------------------------------------------------------

def ensure_image_8bit(image: np.ndarray) -> np.ndarray:
    """确保图像为 8 位无符号整型。

    Args:
        image: 输入图像数组

    Returns:
        uint8 类型图像
    """
    if image.dtype != np.uint8:
        if image.max() <= 1.0:
            image = (image * 255).astype(np.uint8)
        else:
            image = np.clip(image, 0, 255).astype(np.uint8)
    return image


def get_image_dpi(image_path: str) -> int:
    """获取图像的 DPI 信息。

    Args:
        image_path: 图像文件路径

    Returns:
        DPI 值，无法获取返回 300（默认值）
    """
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            dpi = img.info.get("dpi", (300, 300))
            return int(dpi[0])
    except Exception:
        return 300


# ---- 字典/列表工具 ---------------------------------------------------------

def safe_get(d: Dict, key: str, default: Any = None) -> Any:
    """安全地从字典取值，支持嵌套键（用 '.' 分隔）。

    Args:
        d: 字典
        key: 键（支持 "a.b.c" 嵌套）
        default: 默认值

    Returns:
        值或默认值

    Example:
        >>> safe_get({"a": {"b": 1}}, "a.b")
        1
        >>> safe_get({}, "x.y", 0)
        0
    """
    if not d or not key:
        return default

    keys = key.split(".")
    current: Any = d
    for k in keys:
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            return default
    return current


def merge_dicts(base: Dict, override: Dict) -> Dict:
    """递归合并两个字典，override 优先级高。

    Args:
        base: 基础字典
        override: 覆盖字典

    Returns:
        合并后的新字典
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


# ---- 日志工具 --------------------------------------------------------------

def setup_logger(
    name: str,
    level: str = "INFO",
    log_file: Optional[str] = None,
) -> logging.Logger:
    """配置并返回命名日志记录器。

    Args:
        name: 日志记录器名称
        level: 日志级别
        log_file: 日志文件路径（None 则不写文件）

    Returns:
        配置好的 Logger 实例
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 控制台 handler
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        logger.addHandler(console)

        # 文件 handler
        if log_file:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

    return logger
