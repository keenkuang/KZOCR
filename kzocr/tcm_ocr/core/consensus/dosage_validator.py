"""
TCM Dosage Validation Module.

Validates herb dosages extracted from OCR text against standard references.
Supports:
- Chinese and Arabic numeral parsing
- Full-width/half-width character normalization
- Standard and historical unit conversions
- Context-aware herb-dosage extraction
- Non-herb word filtering
- Dual-stage validation (pre-LLM and post-LLM)
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

# Chinese digit mapping
CN_DIGITS: Dict[str, int] = {
    '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3,
    '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
    '十': 10, '百': 100, '千': 1000, '万': 10000,
    '半': 0,  # Special: '半' means half, handled separately
    '几': -1,  # Special: '几' means a few, indefinite
}

# Chinese units
CN_UNITS: List[str] = ['克', 'g', '钱', '分', '两', '斤', '千克', 'kg', '毫升', 'ml', '枚', '个', '片', '段', '寸', '握', '条', '只', '对', '对半', '汤匙', '茶匙', '杯', '碗', '升', '合', '勺']

# Non-herb words that should not be treated as herb names
NON_HERB_WORDS: List[str] = [
    '水', '火', '酒', '醋', '蜜', '糖', '盐', '油', '姜汤', '米汤',
    '黄酒', '白酒', '米醋', '蜂蜜', '白糖', '红糖', '食盐', '香油',
    '温水', '热水', '凉水', '开水', '冷水', '清水', '泉水', '井水',
    '饭前', '饭后', '空腹', '睡前', '顿服', '分服', '温服', '冷服',
    '热服', '冲服', '送服', '含服', '嚼服', '烊化', '兑服', '另煎',
    '先煎', '后下', '包煎', '煎汤', '代水', '泡服', '炖服', '蒸服',
    '外用', '内服', '涂抹', '贴敷', '熏洗', '坐浴', '滴耳', '滴鼻',
    '吹喉', '撒布', '调敷', '捣烂', '取汁', '研末', '为丸', '为散',
    '炼蜜为丸', '水泛为丸', '糊丸', '蜜丸', '水丸', '散剂', '汤剂',
    '每日', '一日', '两次', '三次', '四次', '早晚各', '早中晚',
    '文火', '武火', '急火', '慢火', '大火', '小火', '中火',
    '适量', '少许', '酌量', '酌情', '若干', '等量', '倍量', '减半',
]

# Strong context markers indicating herb context
HERB_CONTEXT_STRONG_MARKERS: List[str] = [
    '组成', '方药', '处方', '用药', '药物', '配伍', '方剂', '汤头',
    '君药', '臣药', '佐药', '使药', '君', '臣', '佐', '使',
    '加味', '减味', '化裁', '加减', '原方', '本方', '此方',
    '主治', '功效', '功用', '应用', '适应症', '宜忌',
    '水煎服', '水煎', '水煎服', '水冲服', '冲服',
]

# Negation prefixes that indicate removal/substitution
NEGATION_PREFIXES: List[str] = ['无', '去', '减', '易', '代']
# Note: '加' is intentionally excluded as it indicates addition

# Historical unit conversions (to grams)
OLD_UNIT_CONVERSION: Dict[str, float] = {
    '两': 30.0,      # 1 两 = 30g (historical standard)
    '钱': 3.0,       # 1 钱 = 3g
    '分': 0.3,       # 1 分 = 0.3g
    '斤': 500.0,     # 1 斤 = 500g
    '合': 100.0,     # 1 合 ≈ 100ml
    '升': 1000.0,    # 1 升 ≈ 1000ml
    '方寸匕': 2.0,   # 1 方寸匕 ≈ 2g
    '钱匕': 1.5,     # 1 钱匕 ≈ 1.5g
}

# Standard dosage ranges for common herbs (in grams)
# Format: {herb_name: (min_grams, max_grams, typical_unit)}
HERB_DOSAGE: Dict[str, Tuple[float, float, str]] = {
    # Qi tonics
    '人参': (3, 10, 'g'), '党参': (9, 30, 'g'), '黄芪': (9, 30, 'g'),
    '白术': (6, 12, 'g'), '山药': (15, 30, 'g'), '甘草': (2, 10, 'g'),
    '大枣': (3, 12, '枚'), '蜂蜜': (15, 30, 'g'), '西洋参': (3, 6, 'g'),
    '太子参': (9, 30, 'g'), '白扁豆': (9, 15, 'g'),

    # Blood tonics
    '当归': (6, 12, 'g'), '熟地黄': (9, 15, 'g'), '白芍': (6, 15, 'g'),
    '阿胶': (3, 9, 'g'), '何首乌': (6, 12, 'g'), '龙眼肉': (10, 15, 'g'),
    '桑葚': (9, 15, 'g'), '枸杞子': (6, 12, 'g'),

    # Yin tonics
    '北沙参': (9, 15, 'g'), '麦冬': (6, 12, 'g'), '天冬': (6, 12, 'g'),
    '石斛': (6, 12, 'g'), '玉竹': (6, 12, 'g'), '百合': (6, 12, 'g'),
    '黄精': (9, 15, 'g'), '女贞子': (6, 12, 'g'), '墨旱莲': (6, 12, 'g'),
    '龟甲': (9, 24, 'g'), '鳖甲': (9, 24, 'g'),

    # Yang tonics
    '鹿茸': (1, 3, 'g'), '巴戟天': (6, 12, 'g'), '肉苁蓉': (6, 10, 'g'),
    '淫羊藿': (6, 10, 'g'), '杜仲': (6, 10, 'g'), '续断': (9, 15, 'g'),
    '菟丝子': (6, 12, 'g'), '沙苑子': (9, 15, 'g'), '补骨脂': (6, 10, 'g'),
    '益智仁': (3, 10, 'g'), '锁阳': (5, 10, 'g'), '韭菜子': (3, 9, 'g'),
    '阳起石': (3, 6, 'g'), '紫河车': (1.5, 3, 'g'), '蛤蚧': (3, 6, 'g'),

    # Diaphoretics (pungent warm)
    '麻黄': (2, 9, 'g'), '桂枝': (3, 10, 'g'), '紫苏': (5, 10, 'g'),
    '生姜': (3, 10, 'g'), '香薷': (3, 10, 'g'), '荆芥': (5, 10, 'g'),
    '防风': (5, 10, 'g'), '羌活': (3, 10, 'g'), '白芷': (3, 10, 'g'),
    '细辛': (1, 3, 'g'), '藁本': (3, 10, 'g'), '苍耳子': (3, 10, 'g'),
    '辛夷': (3, 10, 'g'), '葱白': (3, 10, 'g'),

    # Diaphoretics (pungent cool)
    '薄荷': (3, 6, 'g'), '牛蒡子': (6, 12, 'g'), '蝉蜕': (3, 10, 'g'),
    '桑叶': (5, 10, 'g'), '菊花': (5, 10, 'g'), '葛根': (10, 15, 'g'),
    '柴胡': (3, 10, 'g'), '升麻': (3, 10, 'g'), '蔓荆子': (5, 10, 'g'),
    '淡豆豉': (6, 12, 'g'), '浮萍': (3, 10, 'g'), '木贼': (3, 10, 'g'),

    # Heat-clearing
    '石膏': (15, 60, 'g'), '知母': (6, 12, 'g'), '芦根': (15, 30, 'g'),
    '天花粉': (10, 15, 'g'), '栀子': (6, 10, 'g'), '夏枯草': (9, 15, 'g'),
    '决明子': (10, 15, 'g'), '黄芩': (3, 10, 'g'), '黄连': (2, 5, 'g'),
    '黄柏': (3, 12, 'g'), '龙胆': (3, 6, 'g'), '苦参': (4.5, 10, 'g'),
    '金银花': (6, 15, 'g'), '连翘': (6, 15, 'g'), '板蓝根': (9, 15, 'g'),
    '蒲公英': (10, 30, 'g'), '鱼腥草': (15, 25, 'g'), '射干': (3, 10, 'g'),
    '山豆根': (3, 6, 'g'), '马勃': (1.5, 6, 'g'), '白头翁': (9, 15, 'g'),
    '马齿苋': (9, 15, 'g'), '败酱草': (6, 15, 'g'), '穿心莲': (6, 10, 'g'),
    '半边莲': (10, 15, 'g'), '土茯苓': (15, 60, 'g'), '白花蛇舌草': (15, 60, 'g'),

    # Purgatives
    '大黄': (3, 15, 'g'), '芒硝': (6, 15, 'g'), '番泻叶': (2, 6, 'g'),
    '火麻仁': (10, 15, 'g'), '郁李仁': (6, 12, 'g'), '甘遂': (0.5, 1, 'g'),
    '大戟': (1.5, 3, 'g'), '芫花': (1.5, 3, 'g'), '巴豆': (0.1, 0.3, 'g'),

    # Wind-damp dispelling
    '独活': (3, 10, 'g'), '威灵仙': (6, 10, 'g'), '川乌': (1.5, 3, 'g'),
    '草乌': (1.5, 3, 'g'), '木瓜': (6, 10, 'g'), '蕲蛇': (3, 10, 'g'),
    '乌梢蛇': (6, 12, 'g'), '伸筋草': (3, 12, 'g'), '海风藤': (6, 12, 'g'),
    '路路通': (6, 12, 'g'), '秦艽': (3, 10, 'g'), '防己': (4.5, 10, 'g'),
    '桑枝': (9, 15, 'g'), '豨莶草': (9, 12, 'g'), '臭梧桐': (5, 15, 'g'),
    '络石藤': (6, 12, 'g'), '雷公藤': (10, 25, 'g'),

    # Dampness transforming
    '藿香': (5, 10, 'g'), '佩兰': (5, 10, 'g'), '苍术': (5, 10, 'g'),
    '厚朴': (3, 10, 'g'), '砂仁': (3, 6, 'g'), '白豆蔻': (3, 6, 'g'),
    '草豆蔻': (3, 6, 'g'), '草果': (3, 6, 'g'),

    # Water-draining
    '茯苓': (10, 15, 'g'), '猪苓': (6, 12, 'g'), '泽泻': (5, 10, 'g'),
    '薏苡仁': (9, 30, 'g'), '车前子': (9, 15, 'g'), '滑石': (10, 20, 'g'),
    '木通': (3, 6, 'g'), '通草': (3, 5, 'g'), '瞿麦': (9, 15, 'g'),
    '萹蓄': (9, 15, 'g'), '地肤子': (9, 15, 'g'), '海金沙': (6, 15, 'g'),
    '石韦': (6, 12, 'g'), '萆薢': (10, 15, 'g'),

    # Interior-warming
    '附子': (3, 15, 'g'), '干姜': (3, 10, 'g'), '肉桂': (1, 5, 'g'),
    '吴茱萸': (1.5, 5, 'g'), '小茴香': (3, 6, 'g'), '丁香': (1, 3, 'g'),
    '高良姜': (3, 6, 'g'), '花椒': (3, 6, 'g'),

    # Qi-regulating
    '陈皮': (3, 10, 'g'), '青皮': (3, 10, 'g'), '枳实': (3, 10, 'g'),
    '枳壳': (3, 10, 'g'), '木香': (3, 10, 'g'), '沉香': (1.5, 4.5, 'g'),
    '檀香': (2, 5, 'g'), '川楝子': (4.5, 10, 'g'), '乌药': (3, 10, 'g'),
    '荔枝核': (10, 15, 'g'), '香附': (6, 10, 'g'), '佛手': (3, 10, 'g'),
    '玫瑰花': (3, 6, 'g'), '绿萼梅': (3, 5, 'g'),

    # Digestive
    '山楂': (10, 15, 'g'), '神曲': (6, 15, 'g'), '麦芽': (10, 15, 'g'),
    '谷芽': (9, 15, 'g'), '莱菔子': (6, 10, 'g'), '鸡内金': (3, 10, 'g'),
    '鸡矢藤': (15, 30, 'g'),

    # Hemostatics
    '小蓟': (10, 15, 'g'), '大蓟': (10, 15, 'g'), '地榆': (10, 15, 'g'),
    '槐花': (10, 15, 'g'), '侧柏叶': (6, 12, 'g'), '白茅根': (15, 30, 'g'),
    '三七': (1, 3, 'g'), '茜草': (10, 15, 'g'), '蒲黄': (5, 10, 'g'),
    '白及': (6, 15, 'g'), '仙鹤草': (6, 12, 'g'), '血余炭': (6, 10, 'g'),
    '艾叶': (3, 10, 'g'), '炮姜': (3, 6, 'g'),

    # Blood-activating
    '川芎': (3, 10, 'g'), '延胡索': (3, 10, 'g'), '郁金': (5, 12, 'g'),
    '姜黄': (3, 10, 'g'), '乳香': (3, 5, 'g'), '没药': (3, 5, 'g'),
    '丹参': (5, 15, 'g'), '红花': (3, 10, 'g'), '桃仁': (5, 10, 'g'),
    '益母草': (9, 30, 'g'), '牛膝': (6, 15, 'g'), '鸡血藤': (10, 15, 'g'),
    '莪术': (6, 9, 'g'), '三棱': (5, 10, 'g'), '水蛭': (1.5, 3, 'g'),
    '穿山甲': (3, 10, 'g'), '土鳖虫': (3, 10, 'g'),

    # Phlegm-resolving
    '半夏': (3, 10, 'g'), '天南星': (3, 10, 'g'), '白附子': (3, 5, 'g'),
    '白芥子': (3, 6, 'g'), '旋覆花': (3, 10, 'g'), '白前': (3, 10, 'g'),
    '川贝母': (3, 10, 'g'), '浙贝母': (5, 10, 'g'), '瓜蒌': (10, 20, 'g'),
    '竹茹': (6, 10, 'g'), '竹沥': (30, 60, 'ml'), '天竺黄': (3, 6, 'g'),
    '前胡': (3, 10, 'g'), '桔梗': (3, 10, 'g'), '胖大海': (2, 4, '枚'),
    '海藻': (6, 12, 'g'), '昆布': (6, 12, 'g'), '海蛤壳': (10, 15, 'g'),
    '浮海石': (10, 15, 'g'),

    # Calming
    '朱砂': (0.1, 0.5, 'g'), '磁石': (9, 30, 'g'), '龙骨': (15, 30, 'g'),
    '酸枣仁': (9, 15, 'g'), '柏子仁': (10, 20, 'g'), '远志': (3, 10, 'g'),
    '合欢皮': (10, 15, 'g'), '首乌藤': (9, 15, 'g'),

    # Liver-pacifying & wind-extinguishing
    '石决明': (3, 15, 'g'), '珍珠母': (10, 25, 'g'), '牡蛎': (9, 30, 'g'),
    '代赭石': (9, 30, 'g'), '钩藤': (3, 12, 'g'), '天麻': (3, 10, 'g'),
    '地龙': (5, 10, 'g'), '全蝎': (3, 6, 'g'), '蜈蚣': (1, 3, '条'),
    '僵蚕': (5, 10, 'g'),

    # Aromatic substances
    '麝香': (0.03, 0.1, 'g'), '冰片': (0.15, 0.3, 'g'), '苏合香': (0.3, 1, 'g'),
    '石菖蒲': (3, 10, 'g'), '蟾酥': (0.015, 0.03, 'g'),
}

# Unit validation ranges (grams unless specified)
UNIT_RANGES: Dict[str, Tuple[float, float]] = {
    'g': (0.01, 500),
    '克': (0.01, 500),
    '钱': (0.1, 30),
    '分': (0.01, 10),
    '两': (0.1, 100),
    '斤': (0.1, 10),
    '枚': (0.5, 30),
    '个': (0.5, 30),
    '片': (0.1, 50),
    'ml': (0.5, 2000),
    '毫升': (0.5, 2000),
}


# ── Functions ────────────────────────────────────────────────────────────


def parse_chinese_number(text: str) -> int:
    """Parse a Chinese number string to integer.

    Supports standard Chinese numerals and compound forms like
    '十五', '二十三', '一百零五'.

    Args:
        text: Chinese number string.

    Returns:
        Parsed integer value. Returns 0 for invalid input.

    Example:
        >>> parse_chinese_number('十五')
        15
        >>> parse_chinese_number('一百二十三')
        123
        >>> parse_chinese_number('半')
        0  # Special case, caller should handle
    """
    if not text:
        return 0

    # Direct digit mapping
    if text in CN_DIGITS:
        val = CN_DIGITS[text]
        return max(val, 0)  # Return 0 for '半' and '几'

    total = 0
    current = 0
    prev_digit = 0

    i = 0
    while i < len(text):
        ch = text[i]
        if ch in CN_DIGITS:
            val = CN_DIGITS[ch]
            if val >= 10:
                # Unit character (十, 百, 千, 万)
                if prev_digit == 0:
                    prev_digit = 1  # "十" at start means 10, not 0
                current += prev_digit * val
                prev_digit = 0
            else:
                prev_digit = val
        else:
            # Non-digit character, break
            break
        i += 1

    total = current + prev_digit
    return total


def full_to_half(text: str) -> str:
    """Convert full-width characters to half-width.

    Converts full-width Arabic numerals and ASCII characters to
    their half-width equivalents.

    Args:
        text: Text potentially containing full-width characters.

    Returns:
        Text with full-width characters converted to half-width.

    Example:
        >>> full_to_half('１５ｇ')
        '15g'
        >>> full_to_half('黄芪１５克')
        '黄芪15克'
    """
    result = []
    for ch in text:
        code = ord(ch)
        # Full-width ASCII: 0xFF01-0xFF5E
        if 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        # Full-width space: 0x3000
        elif code == 0x3000:
            result.append(' ')
        else:
            result.append(ch)
    return ''.join(result)


def parse_dosage_value(text: str, unit: str) -> float:
    """Parse a dosage value from text, supporting multiple formats.

    Supports:
    - Arabic numerals: "15", "1.5", "0.5"
    - Chinese numerals: "十五", "一两" (→ 30g)
    - Mixed formats: "1两5钱" (→ 45g)

    Args:
        text: Text containing the dosage value.
        unit: The unit string (g, 克, 钱, 分, 两, etc.).

    Returns:
        Dosage value in grams (or original unit if conversion not available).
        Returns -1.0 for unparseable values.

    Example:
        >>> parse_dosage_value('15', 'g')
        15.0
        >>> parse_dosage_value('十五', 'g')
        15.0
        >>> parse_dosage_value('1两5钱', 'g')
        45.0
    """
    if not text or not text.strip():
        return -1.0

    text = text.strip()

    # Try Arabic numeral parsing first
    # Handle decimals and integers
    arabic_match = re.match(r'^([0-9]+\.?[0-9]*)', text)
    if arabic_match:
        try:
            value = float(arabic_match.group(1))
            # Convert to grams if unit has conversion
            if unit in OLD_UNIT_CONVERSION:
                value *= OLD_UNIT_CONVERSION[unit]
            return value
        except ValueError:
            pass

    # Try Chinese numeral parsing
    cn_match = re.match(r'^([一二两三四五六七八九十百千万零半]+)', text)
    if cn_match:
        cn_num = cn_match.group(1)

        # Handle compound units like "1两5钱"
        compound = re.findall(r'([0-9]*\.?[0-9]*|[一二两三四五六七八九十百千万零半]+)(钱|分|两|克|g)', text)
        if compound and len(compound) > 1:
            total = 0.0
            for val_str, u in compound:
                if not val_str:
                    val_str = '1'
                if val_str in CN_DIGITS:
                    val = float(CN_DIGITS[val_str])
                else:
                    try:
                        val = float(val_str)
                    except ValueError:
                        val = float(parse_chinese_number(val_str))
                if u in OLD_UNIT_CONVERSION:
                    total += val * OLD_UNIT_CONVERSION[u]
                else:
                    total += val
            return total

        # Single Chinese number
        value = float(parse_chinese_number(cn_num))
        if unit in OLD_UNIT_CONVERSION:
            value *= OLD_UNIT_CONVERSION[unit]
        return value

    # Handle "半" (half) specially
    if text.startswith('半'):
        if unit in OLD_UNIT_CONVERSION:
            return 0.5 * OLD_UNIT_CONVERSION[unit]
        return 0.5

    return -1.0


def is_in_herb_context(context: str, herb: str) -> bool:
    """Check if the given context indicates a herb prescription context.

    Looks for strong markers like '组成', '方药', '处方', etc.

    Args:
        context: Surrounding text context.
        herb: The herb name being checked.

    Returns:
        True if context suggests herb prescription context.

    Example:
        >>> is_in_herb_context('方药组成：黄芪15g', '黄芪')
        True
        >>> is_in_herb_context('患者自述症状', '黄芪')
        False
    """
    for marker in HERB_CONTEXT_STRONG_MARKERS:
        if marker in context:
            return True

    # Check if context contains dosage pattern (number + unit after herb)
    pattern = re.search(herb + r'\s*[\d一二两三四五六七八九十半]+\s*[克g钱分两]', context)
    if pattern:
        return True

    return False


def extract_dosages_with_herb(text: str) -> List[Dict[str, Any]]:
    """Extract all herb-dosage pairs from text.

    Pattern: herb_name + [optional space] + number + unit
    Example: "黄芪15g", "当归 10 克", "白术三钱"

    Args:
        text: Input text to parse.

    Returns:
        List of dosage dictionaries:
            - herb (str): Herb name
            - value (float): Dosage value
            - unit (str): Dosage unit
            - span (Tuple[int, int]): Character span in original text

    Example:
        >>> extract_dosages_with_herb('黄芪15g，当归10克')
        [{'herb': '黄芪', 'value': 15.0, 'unit': 'g', 'span': (0, 4)},
         {'herb': '当归', 'value': 10.0, 'unit': '克', 'span': (5, 10)}]
    """
    results: List[Dict[str, Any]] = []
    if not text:
        return results

    text = full_to_half(text)

    # Pattern: herb_name + optional space + number (Arabic or Chinese) + unit
    # This regex handles: 黄芪15g, 黄芪 15 g, 黄芪十五克, 黄芪1两5钱
    dosage_pattern = re.compile(
        r'(' + '|'.join(re.escape(h) for h in HERB_DOSAGE.keys()) + r')'  # herb name
        r'\s*'  # optional whitespace
        r'('  # dosage value group
        r'(?:[0-9]+\.?[0-9]*(?:\s*[钱分两克gml毫升枚个片])?)'  # Arabic with optional compound unit
        r'|'
        r'(?:[一二两三四五六七八九十百千万零半]+(?:\s*[钱分两克gml毫升枚个片])?)'  # Chinese with optional compound
        r')'
        r'\s*'
        r'([克g钱分两枚个片毫升ml]?)'  # unit (optional if in value)
    )

    # Also try a simpler pattern for common cases
    simple_pattern = re.compile(
        r'(' + '|'.join(re.escape(h) for h in HERB_DOSAGE.keys()) + r')'
        r'\s*'
        r'([0-9]+\.?[0-9]*|[一二两三四五六七八九十百千万零半]+)'
        r'\s*'
        r'([克g钱分两枚个片毫升ml])'
    )

    # Try simple pattern first
    for match in simple_pattern.finditer(text):
        herb = match.group(1)
        value_str = match.group(2)
        unit = match.group(3)

        # Skip if it's a non-herb word
        if is_non_herb_word(text, match.start(1), match.end(1)):
            continue

        value = parse_dosage_value(value_str, unit)
        if value < 0:
            continue

        results.append({
            'herb': herb,
            'value': value,
            'unit': unit,
            'span': (match.start(), match.end()),
        })

    # Try compound pattern for complex cases
    compound_pattern = re.compile(
        r'(' + '|'.join(re.escape(h) for h in HERB_DOSAGE.keys()) + r')'
        r'\s*'
        r'(([0-9]*\.?[0-9]*[钱分两])+)'
    )

    for match in compound_pattern.finditer(text):
        herb = match.group(1)
        compound_str = match.group(2)

        # Skip if already captured by simple pattern
        already_captured = any(
            r['herb'] == herb and spans_overlap(r['span'], (match.start(), match.end()))
            for r in results
        )
        if already_captured:
            continue

        if is_non_herb_word(text, match.start(1), match.end(1)):
            continue

        # Parse compound dosage
        value = parse_dosage_value(compound_str, 'g')  # Convert to grams
        if value > 0:
            results.append({
                'herb': herb,
                'value': value,
                'unit': 'g',
                'span': (match.start(), match.end()),
            })

    # Remove duplicates based on span overlap
    filtered: List[Dict[str, Any]] = []
    for r in results:
        if not any(spans_overlap(r['span'], f['span']) and r['herb'] == f['herb']
                   for f in filtered):
            filtered.append(r)

    return filtered


def is_non_herb_word(text: str, match_start: int, match_end: int) -> bool:
    """Check if the matched text position corresponds to a non-herb word.

    Args:
        text: Full text.
        match_start: Start position of match.
        match_end: End position of match.

    Returns:
        True if the match is part of a non-herb word.
    """
    # Check if the surrounding context contains non-herb markers
    context_start = max(0, match_start - 5)
    context_end = min(len(text), match_end + 5)
    context = text[context_start:context_end]

    for word in NON_HERB_WORDS:
        if word in context:
            # Check if the herb name is part of the non-herb phrase
            herb = text[match_start:match_end]
            if herb in word:
                return True

    return False


def validate_dosages(
    text: str,
    stage: str = 'post_ocr',
    pub_year: Optional[int] = None
) -> List[Dict[str, Any]]:
    """Validate herb dosages in text against standard references.

    Checks each extracted herb-dosage pair against standard dosage ranges
    and generates alerts for out-of-range values.

    Args:
        text: Text containing herb-dosage pairs.
        stage: Validation stage ('post_ocr' or 'post_llm').
        pub_year: Publication year for historical unit context.

    Returns:
        List of alert dictionaries:
            - herb (str): Herb name
            - value (float): Detected dosage value
            - range (Tuple[float, float]): Standard range
            - severity (str): 'error', 'warning', or 'info'
            - stage (str): Validation stage
            - span (Tuple[int, int]): Text span
            - message (str): Human-readable alert message

    Example:
        >>> alerts = validate_dosages('黄芪500g')
        >>> print(alerts[0]['message'])
        '黄芪剂量500.0g超出标准范围(9-30g)'
    """
    alerts: List[Dict[str, Any]] = []
    dosages = extract_dosages_with_herb(text)

    for dosage in dosages:
        herb = dosage['herb']
        value = dosage['value']
        unit = dosage['unit']
        span = dosage['span']

        # Look up standard range
        std_range = HERB_DOSAGE.get(herb)
        if std_range is None:
            continue  # Unknown herb, skip

        min_val, max_val, std_unit = std_range

        # Convert to standard unit if needed
        if unit != std_unit and unit in OLD_UNIT_CONVERSION:
            # Already converted in parse_dosage_value
            pass

        # Determine severity
        if value < min_val * 0.5:
            severity = 'error'
            message = f"{herb}剂量{value}{unit}远低于标准范围({min_val}-{max_val}{std_unit})"
        elif value < min_val:
            severity = 'warning'
            message = f"{herb}剂量{value}{unit}低于标准范围({min_val}-{max_val}{std_unit})"
        elif value > max_val * 2:
            severity = 'error'
            message = f"{herb}剂量{value}{unit}严重超出标准范围({min_val}-{max_val}{std_unit})"
        elif value > max_val:
            severity = 'warning'
            message = f"{herb}剂量{value}{unit}超出标准范围({min_val}-{max_val}{std_unit})"
        else:
            continue  # Within range, no alert

        # Check for historical context
        if pub_year and pub_year < 1959:
            # Pre-1959 publications used traditional units
            message += f" (注意：{pub_year}年出版物可能使用传统剂量单位)"
            severity = 'info' if severity == 'warning' else severity

        alerts.append({
            'herb': herb,
            'value': value,
            'range': (min_val, max_val),
            'severity': severity,
            'stage': stage,
            'span': span,
            'message': message,
        })

    return alerts


def dual_stage_dosage_check(
    raw_text: str,
    llm_text: str,
    pub_year: Optional[int] = None
) -> Dict[str, Any]:
    """Perform dual-stage dosage validation (pre-LLM and post-LLM).

    Validates dosages both before and after LLM processing to detect
    any dosage changes introduced by the LLM that might be errors.

    Args:
        raw_text: Raw OCR text (pre-LLM).
        llm_text: LLM-processed text (post-LLM).
        pub_year: Publication year for context.

    Returns:
        Dictionary with:
            - pre_llm_alerts: Alerts from raw text
            - post_llm_alerts: Alerts from LLM text
            - force_human_review: Whether review is forced
            - escalations: List of escalation reasons

    Example:
        >>> result = dual_stage_dosage_check('黄芪15g', '黄芪50g')
        >>> print(result['force_human_review'])
        True  # LLM changed dosage from 15g to 50g
    """
    # Pre-LLM validation
    pre_alerts = validate_dosages(raw_text, stage='pre_llm', pub_year=pub_year)

    # Post-LLM validation
    post_alerts = validate_dosages(llm_text, stage='post_llm', pub_year=pub_year)

    # Compare dosage changes
    raw_dosages = {d['herb']: d for d in extract_dosages_with_herb(raw_text)}
    llm_dosages = {d['herb']: d for d in extract_dosages_with_herb(llm_text)}

    escalations: List[str] = []

    # Check for dosage changes between stages
    all_herbs = set(raw_dosages.keys()) | set(llm_dosages.keys())
    for herb in all_herbs:
        raw_d = raw_dosages.get(herb)
        llm_d = llm_dosages.get(herb)

        if raw_d and llm_d:
            raw_val = raw_d['value']
            llm_val = llm_d['value']
            if raw_val > 0 and llm_val > 0:
                change_ratio = abs(llm_val - raw_val) / raw_val
                if change_ratio > 0.5:  # >50% change
                    escalations.append(
                        f"剂量变更：{herb} {raw_val}{raw_d['unit']} → "
                        f"{llm_val}{llm_d['unit']} (变化{change_ratio*100:.0f}%)"
                    )
        elif raw_d and not llm_d:
            escalations.append(f"剂量删除：{herb} {raw_d['value']}{raw_d['unit']}")
        elif llm_d and not raw_d:
            std_range = HERB_DOSAGE.get(herb)
            if std_range:
                min_val, max_val, _ = std_range
                if not (min_val <= llm_d['value'] <= max_val):
                    escalations.append(
                        f"新增异常剂量：{herb} {llm_d['value']}{llm_d['unit']} "
                        f"超出标准范围({min_val}-{max_val}g)"
                    )

    # Force review if significant issues found
    force_review = (
        len(escalations) > 0
        or any(a['severity'] == 'error' for a in post_alerts)
        or len([a for a in post_alerts if a['severity'] == 'warning']) >= 3
    )

    return {
        'pre_llm_alerts': pre_alerts,
        'post_llm_alerts': post_alerts,
        'force_human_review': force_review,
        'escalations': escalations,
    }


def spans_overlap(span_a: Tuple[int, int], span_b: Tuple[int, int]) -> bool:
    """Check if two text spans overlap.

    Args:
        span_a: First span as (start, end).
        span_b: Second span as (start, end).

    Returns:
        True if the spans overlap.

    Example:
        >>> spans_overlap((0, 5), (3, 8))
        True
        >>> spans_overlap((0, 3), (5, 8))
        False
    """
    start_a, end_a = span_a
    start_b, end_b = span_b
    return max(start_a, start_b) < min(end_a, end_b)
