"""
灾难性字段检测与字形候选管理。

为中医 OCR 校对系统提供关键安全字段的识别与管理：
- 有毒/大毒药材名称（修改可能导致用药安全风险）
- 常用穴位名称（修改可能导致治疗位置错误）
- 剂量单位（修改可能导致剂量错误）
- 否定词（修改可能导致语义翻转）
- 易混淆字形候选获取

支持 PatternCache 动态扩展与冷启动硬编码基线。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────
# 硬编码基线（冷启动使用）
# ───────────────────────────────────────────────────────

# 有毒/大毒药材基线集合
_BASELINE_TOXIC_HERBS: Set[str] = {
    "附子", "朱砂", "雄黄", "马钱子", "巴豆", "大戟", "甘遂", "芫花",
    "商陆", "牵牛子", "千金子", "乌头", "草乌", "川乌", "天南星", "半夏",
    "白附子", "斑蝥", "青娘虫", "红娘虫", "蟾酥", "轻粉", "砒石", "砒霜",
    "水银", "红粉", "藤黄", "闹羊花", "雪上一枝蒿", "白降丹", "红升丹",
    "洋金花", "天仙子", "曼陀罗", "两头尖", "狼毒", "罂粟壳", "硫磺",
    "全蝎", "蜈蚣", "僵蚕", "蜂房", "穿山甲",
}

# 常用穴位基线集合
_BASELINE_MERIDIAN_POINTS: Set[str] = {
    "合谷", "足三里", "三阴交", "太冲", "太溪", "阳陵泉", "内关", "外关",
    "曲池", "百会", "风池", "大椎", "命门", "肾俞", "脾俞", "肝俞",
    "心俞", "肺俞", "膈俞", "关元", "气海", "神阙", "中脘", "膻中",
    "印堂", "人中", "涌泉", "劳宫", "少商", "商阳", "隐白", "厉兑",
    "至阴", "睛明", "攒竹", "四白", "颊车", "下关", "天枢", "丰隆",
    "支沟", "列缺", "照海", "申脉", "公孙", "商丘", "血海", "阴陵泉",
    "地机", "漏谷", "筑宾", "交信", "复溜", "跗阳", "飞扬", "承山",
    "委中", "承扶", "环跳", "风市", "悬钟", "丘墟", "足临泣", "侠溪",
    "行间", "中封", "蠡沟", "中都", "膝关", "曲泉", "阴包", "章门",
    "期门", "大敦", "太白", "大都", "箕门", "冲门", "府舍", "腹结",
    "大横", "腹哀", "食窦", "天溪", "胸乡", "周荣", "大包", "极泉",
    "青灵", "少海", "灵道", "通里", "阴郄", "神门", "少府", "少冲",
    "天池", "天泉", "曲泽", "郄门", "间使", "大陵", "中冲", "关冲",
    "液门", "中渚", "阳池", "会宗", "三阳络", "四渎", "天井", "清冷渊",
    "消泺", "臑会", "肩髎", "天髎", "天牖", "翳风", "瘈脉", "颅息",
    "角孙", "耳门", "耳和髎", "丝竹空", "瞳子髎", "听会", "上关", "颔厌",
    "悬颅", "悬厘", "曲鬓", "率谷", "天冲", "浮白", "头窍阴", "完骨",
    "本神", "阳白", "头临泣", "目窗", "正营", "承灵", "脑空", "肩井",
    "渊腋", "辄筋", "日月", "京门", "带脉", "五枢", "维道", "居髎",
    "中渎", "膝阳关", "阳交", "外丘", "光明", "阳辅", "地五会", "足窍阴",
    "长强", "腰俞", "腰阳关", "悬枢", "脊中", "中枢", "筋缩", "至阳",
    "灵台", "神道", "身柱", "陶道", "哑门", "风府", "脑户", "强间",
    "后顶", "前顶", "囟会", "上星", "神庭", "素髎", "水沟", "兑端",
    "龈交", "会阴", "曲骨", "中极", "石门", "阴交", "水分", "下脘",
    "建里", "上脘", "巨阙", "鸠尾", "中庭", "玉堂", "紫宫", "华盖",
    "璇玑", "天突", "廉泉", "承浆",
}

# 剂量单位
_BASELINE_DOSAGE_UNITS: Set[str] = {"g", "mg", "kg", "克", "钱", "两", "分", "厘", "ml", "毫升", "升", "合", "勺"}

# 否定词
_BASELINE_NEGATION_WORDS: Set[str] = {"不", "无", "非", "忌", "禁", "勿", "慎", "莫", "毋", "未", "没", "别", "休", "弗", "勿"}

# 易混淆字形对
_BASELINE_CONFUSABLE_PAIRS: Set[Tuple[str, str]] = {
    ("术", "木"), ("芩", "苓"), ("己", "已"), ("己", "巳"), ("已", "巳"),
    ("炙", "灸"), ("芍", "勺"), ("葯", "药"), ("酒", "洒"),
    ("参", "參"), ("黄", "黃"), ("连", "連"), ("车", "車"),
    ("泽", "澤"), ("麦", "麥"), ("门", "門"), ("东", "東"),
    ("贝", "貝"), ("细", "細"), ("荆", "荊"), ("苍", "倉"),
    ("薄", "簿"), ("藿", "霍"), ("蓬", "篷"), ("萎", "痿"),
    ("麝", "射"), ("砂", "沙"), ("仁", "人"), ("蔻", "寇"),
    ("苡", "以"), ("芡", "欠"), ("朴", "扑"), ("楝", "练"),
    ("枳", "只"), ("壳", "克"), ("乌", "鸟"), ("佛", "拂"),
    ("五", "互"), ("味", "未"), ("附", "付"), ("半", "羊"),
    ("夏", "复"), ("菖", "昌"), ("蒲", "浦"), ("志", "忘"),
    ("枣", "棘"), ("柏", "伯"), ("杏", "呆"), ("麻", "嘛"),
    ("栀", "卮"), ("连", "联"), ("翘", "翘"), ("花", "化"),
    ("杷", "把"), ("冬", "终"), ("麦", "来"), ("瓜", "爪"),
    ("薏", "意"), ("楂", "查"), ("莱", "来"), ("菔", "服"),
    ("曲", "典"), ("药", "约"), ("党", "党"), ("艽", "九"),
    ("活", "话"), ("仙", "山"), ("风", "凤"), ("羌", "姜"),
    ("本", "木"), ("白", "日"), ("龙", "尤"), ("藤", "腾"),
    ("公", "功"), ("长", "常"), ("卿", "聊"), ("络", "各"),
    ("石", "右"), ("雀", "鹊"), ("枝", "技"), ("桐", "铜"),
    ("皮", "被"), ("加", "如"), ("蕲", "祈"), ("蛇", "它"),
    ("梢", "稍"), ("豨", "希"), ("莶", "签"), ("通", "同"),
    ("丁", "叮"), ("忍", "认"), ("楠", "南"), ("当", "档"),
    ("归", "旧"), ("草", "艸"), ("药", "葯"), ("芎", "穷"),
    ("蒡", "磅"), ("蒡", "旁"), ("杞", "岂"), (("萸", "臾") if True else ("萸", "臾")),
    ("萸", "臾"), ("萸", "庚"), ("脊", "背"), ("椎", "惟"),
    ("髎", "廖"), ("腧", "俞"), ("俞", "兪"), ("穴", "空"),
    ("经", "径"), ("络", "洛"), ("脉", "泳"), ("督", "都"),
    ("任", "住"), ("冲", "仲"), ("跷", "桥"), ("维", "唯"),
}

# ───────────────────────────────────────────────────────
# PatternCache 管理
# ───────────────────────────────────────────────────────

class PatternCache:
    """模式缓存：支持运行时动态扩展关键字段。

    Attributes:
        toxic_herbs: 有毒药材集合。
        meridian_points: 穴位集合。
        dosage_units: 剂量单位集合。
        negation_words: 否定词集合。
        confusable_pairs: 易混淆字形对集合。
        _cache_file: 缓存文件路径。
    """

    _instance: Optional["PatternCache"] = None

    def __new__(cls, cache_file: Optional[str] = None) -> "PatternCache":
        """单例模式。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, cache_file: Optional[str] = None) -> None:
        """初始化 PatternCache。

        Args:
            cache_file: 缓存文件路径，用于持久化扩展的字段。
        """
        if self._initialized:
            return

        self._cache_file: Optional[str] = cache_file
        self.toxic_herbs: Set[str] = set(_BASELINE_TOXIC_HERBS)
        self.meridian_points: Set[str] = set(_BASELINE_MERIDIAN_POINTS)
        self.dosage_units: Set[str] = set(_BASELINE_DOSAGE_UNITS)
        self.negation_words: Set[str] = set(_BASELINE_NEGATION_WORDS)
        self.confusable_pairs: Set[Tuple[str, str]] = set(_BASELINE_CONFUSABLE_PAIRS)

        # 加载持久化缓存
        if cache_file:
            self._load_cache()

        self._initialized = True
        logger.info("[PatternCache] 初始化完成 | 毒材=%d | 穴位=%d | 混淆对=%d",
                     len(self.toxic_herbs), len(self.meridian_points),
                     len(self.confusable_pairs))

    def _load_cache(self) -> None:
        """从文件加载扩展缓存。"""
        if not self._cache_file or not os.path.exists(self._cache_file):
            return

        try:
            import json
            with open(self._cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.toxic_herbs.update(data.get("toxic_herbs", []))
            self.meridian_points.update(data.get("meridian_points", []))
            self.dosage_units.update(data.get("dosage_units", []))
            self.negation_words.update(data.get("negation_words", []))

            # 混淆对需要特殊处理（从列表恢复为元组）
            pairs = data.get("confusable_pairs", [])
            for pair in pairs:
                if len(pair) == 2:
                    self.confusable_pairs.add((pair[0], pair[1]))

            logger.info("[PatternCache] 加载扩展缓存成功")
        except Exception as exc:
            logger.warning("[PatternCache] 缓存加载失败: %s", exc)

    def save_cache(self) -> None:
        """保存扩展缓存到文件。"""
        if not self._cache_file:
            return

        try:
            import json
            data = {
                "toxic_herbs": sorted(self.toxic_herbs - _BASELINE_TOXIC_HERBS),
                "meridian_points": sorted(self.meridian_points - _BASELINE_MERIDIAN_POINTS),
                "dosage_units": sorted(self.dosage_units - _BASELINE_DOSAGE_UNITS),
                "negation_words": sorted(self.negation_words - _BASELINE_NEGATION_WORDS),
                "confusable_pairs": [list(p) for p in (self.confusable_pairs - _BASELINE_CONFUSABLE_PAIRS)],
            }

            os.makedirs(os.path.dirname(self._cache_file), exist_ok=True)
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info("[PatternCache] 缓存已保存")
        except Exception as exc:
            logger.warning("[PatternCache] 缓存保存失败: %s", exc)

    def add_toxic_herb(self, herb: str) -> None:
        """添加有毒药材到缓存。

        Args:
            herb: 药材名称。
        """
        if herb not in self.toxic_herbs:
            self.toxic_herbs.add(herb)
            logger.info("[PatternCache] 添加有毒药材: %s", herb)

    def add_meridian_point(self, point: str) -> None:
        """添加穴位到缓存。

        Args:
            point: 穴位名称。
        """
        if point not in self.meridian_points:
            self.meridian_points.add(point)
            logger.info("[PatternCache] 添加穴位: %s", point)

    def add_confusable_pair(self, char_a: str, char_b: str) -> None:
        """添加易混淆字形对。

        Args:
            char_a: 第一个字符。
            char_b: 第二个字符。
        """
        pair = (char_a, char_b)
        if pair not in self.confusable_pairs and (char_b, char_a) not in self.confusable_pairs:
            self.confusable_pairs.add(pair)
            logger.info("[PatternCache] 添加混淆对: %s ↔ %s", char_a, char_b)


# ───────────────────────────────────────────────────────
# 公共函数
# ───────────────────────────────────────────────────────

# 全局 PatternCache 实例
_pattern_cache: Optional[PatternCache] = None


def _get_pattern_cache(cache_file: Optional[str] = None) -> PatternCache:
    """获取全局 PatternCache 实例（懒加载）。

    Args:
        cache_file: 缓存文件路径（首次初始化时使用）。

    Returns:
        PatternCache 实例。
    """
    global _pattern_cache
    if _pattern_cache is None:
        _pattern_cache = PatternCache(cache_file=cache_file)
    return _pattern_cache


def get_critical_fields(cache_file: Optional[str] = None) -> Set[str]:
    """获取所有灾难性字段集合。

    动态加载（基线 + PatternCache 扩展），冷启动时使用硬编码基线。

    灾难性字段包括：
    - 有毒/大毒药材名称
    - 常用穴位名称
    - 否定词（修改可能导致语义翻转）

    Args:
        cache_file: PatternCache 缓存文件路径（可选）。

    Returns:
        所有灾难性字段字符/词集合。
    """
    cache = _get_pattern_cache(cache_file=cache_file)

    # 合并所有关键字段
    critical: Set[str] = set()
    critical.update(cache.toxic_herbs)
    critical.update(cache.meridian_points)
    critical.update(cache.negation_words)

    return critical


def is_disaster_field(char: str, context: str = "", cache_file: Optional[str] = None) -> bool:
    """判断字符/文本是否涉及灾难性字段。

    灾难性字段判断逻辑：
    1. 字符本身是否是否定词
    2. 上下文中是否包含有毒药材
    3. 上下文中是否包含穴位名
    4. 上下文中是否包含剂量相关信息
    5. 字符是否在易混淆字形对中且上下文含否定词

    Args:
        char: 待判断字符。
        context: 上下文文本（可选，提供更精确的判断）。
        cache_file: PatternCache 缓存文件路径（可选）。

    Returns:
        是否为灾难性字段。
    """
    cache = _get_pattern_cache(cache_file=cache_file)

    # 1. 否定词检查
    if char in cache.negation_words:
        return True

    # 2. 有毒药材检查
    for herb in cache.toxic_herbs:
        if char in herb:
            # 如果提供了上下文，检查完整药材名是否在上下文中
            if context and herb in context:
                return True
            if not context:
                return True

    # 3. 穴位检查
    for point in cache.meridian_points:
        if char in point:
            if context and point in context:
                return True
            if not context:
                return True

    # 4. 上下文剂量检查
    if context:
        # 匹配数字 + 剂量单位
        dosage_pattern = re.compile(
            r"\d+\s*(?:" + "|".join(re.escape(u) for u in cache.dosage_units) + r")"
        )
        if dosage_pattern.search(context):
            # 如果字符是数字或剂量单位，标记为灾难性
            if char.isdigit() or char in cache.dosage_units:
                return True

    # 5. 易混淆字形对中的否定相关修改
    if context and any(nw in context for nw in cache.negation_words):
        for pair in cache.confusable_pairs:
            if char in pair:
                return True

    return False


def get_glyph_candidates_for_char(char: str, context: str = "", cache_file: Optional[str] = None) -> List[str]:
    """获取指定字符的字形候选列表。

    候选来源：
    1. 易混淆字形对
    2. 上下文相关字（药材/穴位中的字）

    Args:
        char: 目标字符。
        context: 上下文文本（用于筛选相关候选）。
        cache_file: PatternCache 缓存文件路径（可选）。

    Returns:
        候选字符列表。
    """
    cache = _get_pattern_cache(cache_file=cache_file)
    candidates: Set[str] = set()

    # 1. 从易混淆字形对获取候选
    for pair in cache.confusable_pairs:
        if char in pair:
            other = pair[1] if pair[0] == char else pair[0]
            if len(other) == 1:
                candidates.add(other)

    # 2. 从上下文相关术语获取候选
    if context:
        # 有毒药材相关字
        for herb in cache.toxic_herbs:
            if char in herb and herb in context:
                for c in herb:
                    if c != char and len(c) == 1:
                        candidates.add(c)

        # 穴位相关字
        for point in cache.meridian_points:
            if char in point and point in context:
                for c in point:
                    if c != char and len(c) == 1:
                        candidates.add(c)

    # 确保原始字符在候选中
    candidates.add(char)

    return sorted(candidates)


def get_field_weight(char: str, context: str = "", cache_file: Optional[str] = None) -> float:
    """获取字段修改的权重（用于决定是否拦截）。

    权重规则：
    - 否定词修改：1.0（硬拦截）
    - 有毒药材相关：1.0（硬拦截）
    - 穴位相关：0.8（高权重拦截）
    - 剂量相关：0.9（高权重拦截）
    - 普通字段：≤0.4（低权重，允许修改）

    Args:
        char: 修改的字符。
        context: 上下文文本。
        cache_file: PatternCache 缓存文件路径（可选）。

    Returns:
        权重值（0.0 ~ 1.0）。
    """
    cache = _get_pattern_cache(cache_file=cache_file)

    # 否定词：最高权重
    if char in cache.negation_words:
        return 1.0

    # 有毒药材
    for herb in cache.toxic_herbs:
        if char in herb and herb in context:
            return 1.0

    # 穴位
    for point in cache.meridian_points:
        if char in point and point in context:
            return 0.8

    # 剂量
    dosage_pattern = re.compile(
        r"\d+\s*(?:" + "|".join(re.escape(u) for u in cache.dosage_units) + r")"
    )
    if dosage_pattern.search(context):
        if char.isdigit() or char in cache.dosage_units:
            return 0.9

    # 易混淆字形对中的否定相关
    if any(nw in context for nw in cache.negation_words):
        for pair in cache.confusable_pairs:
            if char in pair:
                return 0.7

    # 普通字段
    return 0.4


def clear_pattern_cache() -> None:
    """清除 PatternCache 单例（主要用于测试）。"""
    global _pattern_cache
    _pattern_cache = None
    PatternCache._instance = None
    logger.info("[PatternCache] 已清除")
