"""
字形验证层 - Hu 矩 + 标准字体库。

为中医 OCR 校对系统提供字符级字形验证能力：
- 从标准字体库（宋体/仿宋/楷体/黑体 + 花园明朝）预计算 Hu 矩
- 对 LLM 修改过的字符进行字形比对
- 灾难性字段（否定词/剂量/有毒药材/穴位）硬拦截
- 支持精确裁剪（PP-OCR bbox）和滑动窗口 fallback

关键概念：
- Hu 矩：7 个旋转/平移/缩放不变矩，用于字形相似度计算
- 灾难性字段：修改可能导致语义翻转或安全风险的字段
- CONFUSABLE_PAIRS：中医领域易混淆字形对
"""

from __future__ import annotations

import logging
import os
import pickle
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ───────────────────────────────────────────────────────
# 常量定义
# ───────────────────────────────────────────────────────

# 有毒/大毒药材基线集合（修改可能导致安全风险）
TOXIC_HERBS_BASELINE: Set[str] = {
    "附子", "朱砂", "雄黄", "马钱子", "巴豆", "大戟", "甘遂", "芫花",
    "商陆", "牵牛子", "千金子", "乌头", "草乌", "川乌", "天南星", "半夏",
    "白附子", "斑蝥", "青娘虫", "红娘虫", "蟾酥", "轻粉", "砒石", "砒霜",
    "水银", "红粉", "藤黄", "闹羊花", "雪上一枝蒿", "白降丹", "红升丹",
    "洋金花", "天仙子", "曼陀罗", "两头尖", "狼毒", "罂粟壳", "硫磺",
}

# 常用穴位基线集合
MERIDIAN_POINTS_BASELINE: Set[str] = {
    "合谷", "足三里", "三阴交", "太冲", "太溪", "阳陵泉", "内关", "外关",
    "曲池", "百会", "风池", "大椎", "命门", "肾俞", "脾俞", "肝俞",
    "心俞", "肺俞", "膈俞", "关元", "气海", "神阙", "中脘", "膻中",
    "印堂", "人中", "涌泉", "劳宫", "少商", "商阳", "隐白", "厉兑",
    "至阴", "睛明", "攒竹", "四白", "颊车", "下关", "天枢", "丰隆",
    "支沟", "列缺", "照海", "申脉", "公孙", "商丘", "血海", "阴陵泉",
    "地机", "漏谷", "筑宾", "交信", "复溜", "跗阳", "飞扬", "承山",
    "委中", "承扶", "环跳", "风市", "悬钟", "丘墟", "足临泣", "侠溪",
    "行间", "中封", "蠡沟", "中都", "膝关", "曲泉", "阴包", "章门",
    "期门", "大敦", "行间", "太白", "大都", "商丘", "三阴交",
    "漏谷", "地机", "阴陵泉", "血海", "箕门", "冲门", "府舍", "腹结",
    "大横", "腹哀", "食窦", "天溪", "胸乡", "周荣", "大包", "极泉",
    "青灵", "少海", "灵道", "通里", "阴郄", "神门", "少府", "少冲",
    "天池", "天泉", "曲泽", "郄门", "间使", "内关", "大陵", "劳宫",
    "中冲", "关冲", "液门", "中渚", "阳池", "外关", "支沟", "会宗",
    "三阳络", "四渎", "天井", "清冷渊", "消泺", "臑会", "肩髎", "天髎",
    "天牖", "翳风", "瘈脉", "颅息", "角孙", "耳门", "耳和髎", "丝竹空",
    "瞳子髎", "听会", "上关", "颔厌", "悬颅", "悬厘", "曲鬓", "率谷",
    "天冲", "浮白", "头窍阴", "完骨", "本神", "阳白", "头临泣", "目窗",
    "正营", "承灵", "脑空", "风池", "肩井", "渊腋", "辄筋", "日月",
    "京门", "带脉", "五枢", "维道", "居髎", "中渎", "膝阳关", "阳陵泉",
    "阳交", "外丘", "光明", "阳辅", "悬钟", "丘墟", "足临泣", "地五会",
    "侠溪", "足窍阴", "长强", "腰俞", "腰阳关", "命门", "悬枢", "脊中",
    "中枢", "筋缩", "至阳", "灵台", "神道", "身柱", "陶道", "大椎",
    "哑门", "风府", "脑户", "强间", "后顶", "百会", "前顶", "囟会",
    "上星", "神庭", "素髎", "水沟", "兑端", "龈交", "会阴", "曲骨",
    "中极", "关元", "石门", "气海", "阴交", "神阙", "水分", "下脘",
    "建里", "中脘", "上脘", "巨阙", "鸠尾", "中庭", "膻中", "玉堂",
    "紫宫", "华盖", "璇玑", "天突", "廉泉", "承浆",
}

# 剂量单位
DOSAGE_UNITS: Set[str] = {"g", "mg", "克", "钱", "两", "分", "ml", "毫升", "kg", "千克", "斤"}

# 否定词（修改可能导致语义翻转）
NEGATION_WORDS: Set[str] = {"不", "无", "非", "忌", "禁", "勿", "慎", "莫", "毋", "未", "没", "别", "休"}

# 易混淆字形对（中医领域常见）
CONFUSABLE_PAIRS: Set[Tuple[str, str]] = {
    ("术", "木"), ("芩", "苓"), ("己", "已"), ("己", "巳"), ("已", "巳"),
    ("炙", "灸"), ("芍", "勺"), ("葯", "药"),
    ("酒", "洒"), ("桂枝", "桂技"), ("甘草", "甘革"), ("黄芪", "黄耆"),
    ("人參", "人参"), ("參", "参"), ("當歸", "当归"), ("黃", "黄"),
    ("連", "连"), ("車", "车"), ("澤", "泽"), ("麥", "麦"), ("門", "门"),
    ("東", "东"), ("貝", "贝"), ("細", "细"), ("荊", "荆"),
    ("苍", "仓"), ("薄", "簿"), ("荷", "菏"), ("藿", "霍"),
    ("蓬", "篷"), ("萎", "痿"), ("蔹", "敛"),
    ("龟", "龟"), ("鹿", "麂"), ("茸", "葺"), ("角", "脚"),
    ("麝", "射"), ("香", "杳"), ("砂", "沙"), ("仁", "人"),
    ("蔻", "寇"), ("苡", "以"), ("芡", "欠"),
    ("厚", "原"), ("朴", "扑"), ("槟", "宾"), ("榔", "郎"),
    ("楝", "练"), ("子", "了"), ("枳", "只"), ("壳", "克"),
    ("青", "清"), ("皮", "被"), ("陈", "阵"), ("乌", "鸟"),
    ("梅", "莓"),
    ("佛", "拂"), ("手", "毛"), ("五", "互"), ("味", "未"),
    ("附", "付"), ("子", "孑"), ("半", "羊"), ("夏", "复"),
    ("菖", "昌"), ("蒲", "浦"), ("远", "运"), ("志", "忘"),
    ("酸", "醋"), ("枣", "棘"), ("柏", "伯"), ("仁", "仕"),
    ("杏", "呆"), ("麻", "嘛"), ("决", "决"),
    ("明", "朋"), ("栀", "卮"), ("黄", "皇"), ("柏", "拍"),
    ("连", "联"), ("翘", "翘"), ("金", "全"), ("银", "艮"),
    ("花", "化"), ("桑", "桉"), ("叶", "计"),
    ("枇", "批"), ("杷", "把"), ("冬", "终"), ("麦", "来"),
    ("天", "夫"), ("瓜", "爪"), ("薏", "意"),
    ("山", "出"), ("楂", "查"), ("莱", "来"), ("菔", "服"),
    ("神", "袖"), ("曲", "典"), ("山", "汕"), ("药", "约"),
    ("党", "党"), ("山", "屾"), ("楂", "揸"), ("木", "术"),
    ("香", "杳"), ("附", "付"), ("白", "百"),
    ("芷", "止"), ("黄", "皇"), ("连", "联"),
    ("秦", "奏"), ("艽", "九"), ("独", "虫"), ("活", "话"),
    ("威", "戚"), ("灵", "录"), ("仙", "山"), ("防", "仿"),
    ("风", "凤"), ("羌", "姜"), ("活", "括"), ("藁", "稿"),
    ("本", "木"), ("苍", "仓"), ("术", "朮"), ("白", "日"),
    ("穿", "空"), ("山", "出"), ("龙", "尤"),
    ("海", "梅"), ("藤", "腾"), ("雷", "雪"),
    ("公", "功"), ("徐", "涂"), ("长", "常"), ("卿", "聊"),
    ("络", "各"), ("石", "右"), ("藤", "滕"), ("孔", "子"),
    ("雀", "鹊"), ("桑", "桉"), ("枝", "技"),
    ("桐", "铜"), ("皮", "被"), ("五", "互"), ("加", "如"),
    ("蕲", "祈"), ("蛇", "它"), ("乌", "鸟"), ("梢", "稍"),
    ("豨", "希"), ("莶", "签"), ("路", "格"),
    ("通", "同"), ("青", "清"), ("丁", "叮"),
    ("藤", "滕"), ("忍", "认"), ("冬", "终"),
    ("石", "右"), ("楠", "南"), ("白", "百"),
    ("术", "朮"), ("当", "档"),
}

# 字体文件搜索路径（系统字体目录）
FONT_SEARCH_PATHS: List[str] = [
    "/usr/share/fonts/truetype/",
    "/usr/share/fonts/",
    "/usr/local/share/fonts/",
    "~/.fonts/",
    "/System/Library/Fonts/",
    "/Library/Fonts/",
    "~/Library/Fonts/",
    "/mnt/c/Windows/Fonts/",
]

# 字体文件名映射
FONT_FILE_MAP: Dict[str, List[str]] = {
    "songti": ["simsun.ttc", "SimSun.ttf", "NotoSerifCJK-Regular.ttc",
               "SourceHanSerifSC-Regular.otf", "wqy-zenhei.ttc"],
    "fangsong": ["simfang.ttf", "FangSong.ttf", "FangSong_GB2312.ttf",
                 "STFangsong.ttf", "NotoSerifCJK-Regular.ttc"],
    "kaiti": ["simkai.ttf", "KaiTi.ttf", "KaiTi_GB2312.ttf",
              "STKaiti.ttf", "NotoSansCJK-Regular.ttc"],
    "heiti": ["simhei.ttf", "SimHei.ttf", "NotoSansCJK-Bold.ttc",
              "SourceHanSansSC-Regular.otf", "wqy-zenhei.ttc"],
    "hanazono": ["HanaMinA.ttf", "HanaMinB.ttf", "HanaMinPlus.ttf"],
}


def _normalize_confusable_pairs() -> Dict[str, Set[str]]:
    """将 CONFUSABLE_PAIRS 规范化为一维映射字典。

    Returns:
        字符到其易混淆字符集合的映射字典。
    """
    mapping: Dict[str, Set[str]] = {}
    for pair in CONFUSABLE_PAIRS:
        a, b = pair
        if len(a) == 1 and len(b) == 1:
            mapping.setdefault(a, set()).add(b)
            mapping.setdefault(b, set()).add(a)
    return mapping


CONFUSABLE_MAP: Dict[str, Set[str]] = _normalize_confusable_pairs()


def is_dosage_related(text: str) -> bool:
    """判断文本是否包含剂量相关信息。

    Args:
        text: 待判断文本。

    Returns:
        是否包含剂量单位或数字+剂量模式。
    """
    import re
    pattern = r"\d+\s*(?:" + "|".join(re.escape(u) for u in DOSAGE_UNITS) + r")"
    return bool(re.search(pattern, text))


def is_negation_related(text: str) -> bool:
    """判断文本是否包含否定词。

    Args:
        text: 待判断文本。

    Returns:
        是否包含否定词。
    """
    return any(nw in text for nw in NEGATION_WORDS)


def is_toxic_herb(text: str) -> bool:
    """判断文本是否涉及有毒药材。

    Args:
        text: 待判断文本。

    Returns:
        是否包含有毒药材名。
    """
    return any(herb in text for herb in TOXIC_HERBS_BASELINE)


def is_meridian_point(text: str) -> bool:
    """判断文本是否涉及穴位。

    Args:
        text: 待判断文本。

    Returns:
        是否包含穴位名。
    """
    return any(point in text for point in MERIDIAN_POINTS_BASELINE)


# ───────────────────────────────────────────────────────
# GlyphVerifier 类
# ───────────────────────────────────────────────────────

class GlyphVerifier:
    """字形验证器：Hu 矩 + 标准字体库。

    对 LLM 输出的修改进行字符级字形验证：
    1. 逐字比对 LLM 输出与原始共识
    2. 对每个修改过的字符提取图像并计算 Hu 矩
    3. 与标准字体库中的 Hu 矩比对
    4. 灾难性字段硬拦截，普通字段软拦截

    Attributes:
        font_cache_dir: 字体缓存目录。
        hu_cache: 预计算的 Hu 矩缓存 {char: {font_type: hu_moments}}。
        fonts: 加载的字体字典 {font_type: ImageFont}。
        standard_size: 标准字形渲染尺寸。
    """

    def __init__(self, font_cache_dir: str) -> None:
        """初始化字形验证器。

        Args:
            font_cache_dir: 字体缓存目录，用于存储预计算的 Hu 矩缓存。
        """
        self.font_cache_dir: str = font_cache_dir
        self.standard_size: int = 64
        self.hu_cache: Dict[str, Dict[str, np.ndarray]] = {}
        self.fonts: Dict[str, ImageFont.FreeTypeFont] = {}

        os.makedirs(font_cache_dir, exist_ok=True)

        # 加载字体
        self._load_fonts()

        # 加载/预计算 Hu 矩缓存
        self._init_hu_cache()

        logger.info(
            "[GlyphVerifier] 初始化完成 | 字体=%s | Hu 缓存=%d 字",
            list(self.fonts.keys()),
            len(self.hu_cache),
        )

    def _load_fonts(self) -> None:
        """加载系统中可用的标准字体。"""
        for font_type, filenames in FONT_FILE_MAP.items():
            for filename in filenames:
                font_path = self._find_font_file(filename)
                if font_path:
                    try:
                        self.fonts[font_type] = ImageFont.truetype(
                            font_path, self.standard_size
                        )
                        logger.debug("[GlyphVerifier] 加载字体 %s: %s", font_type, font_path)
                        break
                    except Exception as exc:
                        logger.debug("[GlyphVerifier] 字体加载失败 %s: %s", font_path, exc)

        if not self.fonts:
            logger.warning("[GlyphVerifier] 未找到任何系统字体，使用默认字体")
            self.fonts["default"] = ImageFont.load_default()

    def _find_font_file(self, filename: str) -> Optional[str]:
        """在系统字体目录中查找字体文件。

        Args:
            filename: 字体文件名。

        Returns:
            字体文件完整路径，未找到返回 None。
        """
        for base_path in FONT_SEARCH_PATHS:
            expanded = os.path.expanduser(base_path)
            if not os.path.exists(expanded):
                continue
            for root, _dirs, files in os.walk(expanded):
                if filename in files:
                    return os.path.join(root, filename)
        return None

    def _init_hu_cache(self) -> None:
        """初始化 Hu 矩缓存（加载已有缓存或预计算常用字）。"""
        cache_file = os.path.join(self.font_cache_dir, "hu_moments_cache.pkl")

        # 尝试加载已有缓存
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "rb") as f:
                    loaded = pickle.load(f)
                    self.hu_cache = {
                        k: {ft: np.array(v2) for ft, v2 in v.items()}
                        for k, v in loaded.items()
                    }
                logger.info("[GlyphVerifier] 加载 Hu 矩缓存: %d 字", len(self.hu_cache))
                return
            except Exception as exc:
                logger.warning("[GlyphVerifier] 缓存加载失败: %s", exc)

        # 预计算常用字 Hu 矩
        common_chars = self._get_common_chars()
        logger.info("[GlyphVerifier] 预计算 %d 常用字 Hu 矩...", len(common_chars))

        for char in common_chars:
            self.hu_cache[char] = {}
            for font_type in self.fonts:
                hu = self._compute_hu_for_char(char, font_type)
                if hu is not None:
                    self.hu_cache[char][font_type] = hu

        # 保存缓存
        try:
            serializable = {
                k: {ft: v.tolist() for ft, v in ch.items()}
                for k, ch in self.hu_cache.items()
            }
            with open(cache_file, "wb") as f:
                pickle.dump(serializable, f)
            logger.info("[GlyphVerifier] Hu 矩缓存已保存")
        except Exception as exc:
            logger.warning("[GlyphVerifier] 缓存保存失败: %s", exc)

    def _get_common_chars(self) -> Set[str]:
        """获取需要预计算的常用字集合。

        Returns:
            常用字符集合（包含 ASCII、常用汉字、中医领域常用字）。
        """
        chars: Set[str] = set()

        # ASCII 字符
        for c in range(32, 127):
            chars.add(chr(c))

        # 常用一级汉字高频字 + 中医领域常用字
        frequent_chars = (
            "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成"
            "会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之进着"
            "等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业本去把"
            "性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与关各重新"
            "线内数正心反你明看原又么利比或但质气第向道命此变条只没结解问意建月公"
            "无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总次品式活"
            "设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运"
            "农指几九区强放决西被干做必战先回则任取完举色"
            "附术芩苓己已炙灸芍葯药酒洒桂枝甘草黄芪人參当归黄連車澤麥門東貝細荊"
            "防苍薄荷荷藿霍蓬萎蔹麝龟鹿茸角香砂仁蔻苡芡实厚朴槟榔楝子枳壳青皮陈"
            "乌梅佛手五味附子半夏菖蒲远志酸枣柏仁杏麻决明栀黄连翘金银花菊桑枇冬"
            "天瓜薏苡山楂莱菔神曲山药党秦艽独活威灵仙防风羌活藁本苍术穿山龙海风"
            "藤雷公徐长卿络石藤孔雀桑枝海桐皮五加蕲蛇乌梢豨莶路路通海风藤青风藤"
            "丁公藤穿龙忍冬络石楠藤白忍龙石楠白蔹"
        )
        chars.update(frequent_chars)

        # 有毒药材中的字
        for herb in TOXIC_HERBS_BASELINE:
            chars.update(herb)

        # 穴位名中的字
        for point in MERIDIAN_POINTS_BASELINE:
            chars.update(point)

        # 否定词
        chars.update(NEGATION_WORDS)

        # 易混淆字对中的单字
        for a, b in CONFUSABLE_PAIRS:
            if len(a) == 1:
                chars.add(a)
            if len(b) == 1:
                chars.add(b)

        # 剂量单位
        chars.update(DOSAGE_UNITS)

        return chars

    def _render_char_image(self, char: str, font_type: str) -> Optional[np.ndarray]:
        """使用指定字体渲染字符为灰度图像。

        Args:
            char: 待渲染字符。
            font_type: 字体类型。

        Returns:
            渲染后的灰度图像 (H, W)，失败返回 None。
        """
        font = self.fonts.get(font_type)
        if font is None:
            return None

        try:
            img_size = self.standard_size * 2
            img = Image.new("L", (img_size, img_size), color=255)
            draw = ImageDraw.Draw(img)

            bbox = draw.textbbox((0, 0), char, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            x = (img_size - text_w) // 2 - bbox[0]
            y = (img_size - text_h) // 2 - bbox[1]
            draw.text((x, y), char, fill=0, font=font)

            return np.array(img)
        except Exception as exc:
            logger.debug("[GlyphVerifier] 渲染字符 '%s' 失败: %s", char, exc)
            return None

    def _compute_hu_for_char(self, char: str, font_type: str) -> Optional[np.ndarray]:
        """计算字符的 Hu 矩。

        Args:
            char: 待计算字符。
            font_type: 字体类型。

        Returns:
            Hu 矩向量 (7,)，失败返回 None。
        """
        char_img = self._render_char_image(char, font_type)
        if char_img is None:
            return None
        return self.extract_hu_moments(char_img)

    # ═══════════════════════════════════════════════════
    # 公共接口
    # ═══════════════════════════════════════════════════

    def verify_llm_output(
        self,
        llm_output: Dict[str, Any],
        original_consensus: str,
        para_img: np.ndarray,
        line_records: List[Dict[str, Any]],
        term_kb: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """逐字比对 LLM 输出与原始共识，进行字形验证。

        流程：
        1. 逐行比对 LLM corrected_lines 与原始 consensus_text
        2. 对每个修改过的字符：
           a. extract_char_image_precise 精确裁剪
           b. get_glyph_candidates 获取候选字
           c. glyph_verify_hu Hu矩比对
        3. 灾难性字段 → 硬拦截（权重=1.0）
        4. 普通字段 → 权重 ≤0.4

        Args:
            llm_output: LLM 输出字典，包含 corrected_lines 和 changes。
            original_consensus: 原始共识文本（多行）。
            para_img: 段落图像。
            line_records: 行级 OCR 结果记录列表。
            term_kb: 术语知识库（可选）。

        Returns:
            验证结果字典：
            {
                "all_verified": bool,
                "verified_lines": List[str],
                "failed_lines": List[Dict],
                "critical_intercept": List[Dict],
            }
        """
        corrected_lines: List[str] = llm_output.get("corrected_lines", [])
        changes: List[Dict[str, Any]] = llm_output.get("changes", [])
        original_lines = original_consensus.split("\n")

        verified_lines: List[str] = []
        failed_lines: List[Dict[str, Any]] = []
        critical_intercept: List[Dict[str, Any]] = []

        # 构建变更索引
        change_map: Dict[int, List[Tuple[int, str, str]]] = {}
        for change in changes:
            line_idx = change.get("line_index", 0)
            char_idx = change.get("char_index", -1)
            old_char = change.get("original", "")
            new_char = change.get("corrected", "")
            change_map.setdefault(line_idx, []).append((char_idx, old_char, new_char))

        for line_idx, corrected_line in enumerate(corrected_lines):
            original_line = original_lines[line_idx] if line_idx < len(original_lines) else ""
            line_changes = change_map.get(line_idx, [])
            line_record = line_records[line_idx] if line_idx < len(line_records) else {}

            line_failed = False
            line_failures: List[Dict[str, Any]] = []

            for char_idx, old_char, new_char in line_changes:
                if old_char == new_char:
                    continue

                # 判断字段类型
                is_critical = self._is_critical_field(old_char, new_char, original_line)

                # 精确裁剪字符图像
                char_img = self.extract_char_image_precise(
                    para_img, line_record, char_idx, original_line
                )

                # 获取候选字
                candidates = self.get_glyph_candidates(
                    disputed_char=old_char,
                    original_text=original_line,
                    llm_text=corrected_line,
                    term_kb=term_kb,
                )
                if new_char not in candidates:
                    candidates.append(new_char)

                # 滑动窗口 fallback
                sliding_imgs: List[np.ndarray] = []
                if char_img is None:
                    sliding_imgs = self.extract_char_image_sliding(
                        para_img, line_record, char_idx
                    )

                # Hu 矩比对
                if char_img is not None:
                    verify_result = self.glyph_verify_hu(
                        disputed_char=old_char,
                        candidate_chars=candidates,
                        char_images=[char_img],
                    )
                elif sliding_imgs:
                    verify_result = self.glyph_verify_hu(
                        disputed_char=old_char,
                        candidate_chars=candidates,
                        char_images=sliding_imgs,
                    )
                else:
                    verify_result = {
                        "decision": "uncertain",
                        "confidence": 0.0,
                        "keep_original": True,
                        "reason": "无法提取字符图像",
                    }

                decision = verify_result.get("decision", "uncertain")
                confidence = verify_result.get("confidence", 0.0)

                # 决策逻辑
                if is_critical:
                    # 灾难性字段：必须高置信度才允许修改
                    if decision != "accept_llm" or confidence < 0.85:
                        line_failed = True
                        critical_intercept.append({
                            "line_index": line_idx,
                            "char_index": char_idx,
                            "original": old_char,
                            "llm_suggested": new_char,
                            "field_type": "critical",
                            "confidence": confidence,
                            "decision": "intercepted",
                            "reason": "灾难性字段修改被硬拦截",
                        })
                        line_failures.append({
                            "char_index": char_idx,
                            "original": old_char,
                            "llm_suggested": new_char,
                            "decision": "intercepted",
                            "confidence": confidence,
                            "is_critical": True,
                        })
                    else:
                        line_failures.append({
                            "char_index": char_idx,
                            "original": old_char,
                            "llm_suggested": new_char,
                            "decision": "accepted",
                            "confidence": confidence,
                            "is_critical": True,
                        })
                else:
                    # 普通字段：置信度 <= 0.4 时保留原文
                    if decision == "uncertain" or confidence < 0.4:
                        line_failed = True
                        line_failures.append({
                            "char_index": char_idx,
                            "original": old_char,
                            "llm_suggested": new_char,
                            "decision": "keep_original",
                            "confidence": confidence,
                            "is_critical": False,
                            "reason": "置信度过低",
                        })
                    else:
                        line_failures.append({
                            "char_index": char_idx,
                            "original": old_char,
                            "llm_suggested": new_char,
                            "decision": "accepted",
                            "confidence": confidence,
                            "is_critical": False,
                        })

            if line_failed:
                failed_lines.append({
                    "line_index": line_idx,
                    "original": original_line,
                    "llm_corrected": corrected_line,
                    "failures": line_failures,
                })
                verified_lines.append(original_line)
            else:
                verified_lines.append(corrected_line)

        return {
            "all_verified": len(failed_lines) == 0 and len(critical_intercept) == 0,
            "verified_lines": verified_lines,
            "failed_lines": failed_lines,
            "critical_intercept": critical_intercept,
        }

    def _is_critical_field(
        self, old_char: str, new_char: str, context: str
    ) -> bool:
        """判断字符修改是否涉及灾难性字段。

        Args:
            old_char: 原始字符。
            new_char: LLM 建议字符。
            context: 上下文文本。

        Returns:
            是否为灾难性字段修改。
        """
        # 检查是否为否定词修改
        if old_char in NEGATION_WORDS or new_char in NEGATION_WORDS:
            return True

        # 检查是否涉及有毒药材
        modified_context = context.replace(old_char, new_char, 1)
        if is_toxic_herb(modified_context) and not is_toxic_herb(context):
            return True

        # 检查是否涉及穴位名修改
        if is_meridian_point(modified_context) and not is_meridian_point(context):
            return True

        # 检查剂量相关修改
        if is_dosage_related(modified_context) and old_char.isdigit() != new_char.isdigit():
            return True

        # 检查易混淆字形对中的否定相关修改
        if (old_char, new_char) in CONFUSABLE_PAIRS or (new_char, old_char) in CONFUSABLE_PAIRS:
            if any(n in context for n in NEGATION_WORDS):
                return True

        return False

    # ═══════════════════════════════════════════════════
    # 字符图像提取
    # ═══════════════════════════════════════════════════

    def extract_char_image_precise(
        self,
        para_img: np.ndarray,
        line_record: Dict[str, Any],
        consensus_pos: int,
        consensus_text: str,
    ) -> Optional[np.ndarray]:
        """精确裁剪字符图像（使用 PP-OCR 字符级 bbox）。

        Args:
            para_img: 段落图像 (H, W, C) 或 (H, W)。
            line_record: 行级 OCR 结果，包含 chars 字段（字符级 bbox 列表）。
            consensus_pos: 字符在行中的位置索引。
            consensus_text: 行文本内容（用于校验位置）。

        Returns:
            裁剪后的字符图像 (h, w)，失败返回 None。
        """
        if para_img is None or para_img.size == 0:
            return None

        chars_info = line_record.get("chars", [])
        if not chars_info or consensus_pos >= len(chars_info):
            return None

        try:
            char_info = chars_info[consensus_pos]
            bbox = char_info.get("bbox")
            if bbox is None or len(bbox) != 4:
                return None

            x1, y1, x2, y2 = map(int, bbox)

            # ±2px 补偿
            margin = 2
            x1 = max(0, x1 - margin)
            y1 = max(0, y1 - margin)
            x2 = min(para_img.shape[1], x2 + margin)
            y2 = min(para_img.shape[0], y2 + margin)

            if x2 <= x1 or y2 <= y1:
                return None

            char_img = para_img[y1:y2, x1:x2]
            if char_img.ndim == 3:
                char_img = cv2.cvtColor(char_img, cv2.COLOR_BGR2GRAY)

            return char_img

        except Exception as exc:
            logger.debug("[GlyphVerifier] 精确裁剪失败: %s", exc)
            return None

    def extract_char_image_sliding(
        self,
        para_img: np.ndarray,
        line_record: Dict[str, Any],
        char_pos: int,
    ) -> List[np.ndarray]:
        """滑动窗口策略提取字符图像（Fallback）。

        当精确裁剪失败时，使用行级 bbox 和字符位置进行滑动窗口采样。

        Args:
            para_img: 段落图像。
            line_record: 行级 OCR 结果。
            char_pos: 字符在行中的位置索引。

        Returns:
            裁剪得到的字符图像列表（可能为空）。
        """
        if para_img is None or para_img.size == 0:
            return []

        results: List[np.ndarray] = []

        try:
            line_bbox = line_record.get("bbox")
            if line_bbox is None or len(line_bbox) != 4:
                return results

            lx1, ly1, lx2, ly2 = map(int, line_bbox)
            lx1 = max(0, lx1)
            ly1 = max(0, ly1)
            lx2 = min(para_img.shape[1], lx2)
            ly2 = min(para_img.shape[0], ly2)

            line_img = para_img[ly1:ly2, lx1:lx2]
            if line_img.ndim == 3:
                line_img = cv2.cvtColor(line_img, cv2.COLOR_BGR2GRAY)

            line_h, line_w = line_img.shape[:2]
            chars_count = max(len(line_record.get("chars", [])), char_pos + 1, 1)
            avg_char_w = line_w // chars_count

            # 滑动窗口：多个偏移采样
            offsets = [-3, 0, 3]
            for offset in offsets:
                cx1 = char_pos * avg_char_w + offset
                cx2 = cx1 + avg_char_w
                cx1 = max(0, cx1)
                cx2 = min(line_w, cx2)

                if cx2 > cx1:
                    char_img = line_img[:, cx1:cx2]
                    if char_img.size > 0:
                        results.append(char_img)

        except Exception as exc:
            logger.debug("[GlyphVerifier] 滑动窗口裁剪失败: %s", exc)

        return results

    # ═══════════════════════════════════════════════════
    # Hu 矩计算与比对
    # ═══════════════════════════════════════════════════

    def extract_hu_moments(self, char_img: np.ndarray) -> Optional[np.ndarray]:
        """提取图像的 Hu 矩特征。

        Hu 矩是 7 个旋转、平移、缩放不变的矩特征，
        用于比较字形相似度。

        Args:
            char_img: 字符图像 (H, W)，单通道灰度。

        Returns:
            Hu 矩向量 (7,)，失败返回 None。
        """
        if char_img is None or char_img.size == 0:
            return None

        try:
            if char_img.dtype != np.uint8:
                char_img = cv2.normalize(char_img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

            # 二值化
            _, binary = cv2.threshold(char_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

            # 计算矩
            moments = cv2.moments(binary)

            # 计算 Hu 矩
            hu = cv2.HuMoments(moments).flatten()

            # 取 log 处理（压缩动态范围）
            hu_log = np.sign(hu) * np.log10(np.abs(hu) + 1e-10)

            return hu_log.astype(np.float32)

        except Exception as exc:
            logger.debug("[GlyphVerifier] Hu 矩提取失败: %s", exc)
            return None

    def load_standard_hu_moments(
        self,
        char: str,
        font_type: str = "songti",
    ) -> Optional[np.ndarray]:
        """从缓存或字体渲染获取标准字形 Hu 矩。

        Args:
            char: 字符。
            font_type: 字体类型，默认 'songti'。

        Returns:
            Hu 矩向量 (7,)，失败返回 None。
        """
        # 从缓存查找
        if char in self.hu_cache and font_type in self.hu_cache[char]:
            return self.hu_cache[char][font_type]

        # 动态计算
        hu = self._compute_hu_for_char(char, font_type)
        if hu is not None:
            if char not in self.hu_cache:
                self.hu_cache[char] = {}
            self.hu_cache[char][font_type] = hu
        return hu

    def glyph_verify_hu(
        self,
        disputed_char: str,
        candidate_chars: List[str],
        char_images: List[np.ndarray],
        font_type: str = "songti",
    ) -> Dict[str, Any]:
        """Hu 矩字形比对验证。

        计算待验证字符图像与各候选字标准字形的 Hu 矩欧氏距离。

        决策规则：
        - 距离最小的候选与次小的差距 >= 20% → 接受最小距离的候选
        - 最高/次高差距 < 20% → uncertain
        - disputed_char 距离显著小于所有候选 → 保留原文

        Args:
            disputed_char: 原始字符（待验证）。
            candidate_chars: 候选字符列表。
            char_images: 待验证字符图像列表（取平均 Hu 矩）。
            font_type: 字体类型。

        Returns:
            验证结果字典：
            {
                "decision": "accept_llm" | "keep_original" | "uncertain",
                "confidence": float,
                "keep_original": bool,
                "distances": {char: distance},
                "best_match": str,
            }
        """
        if not char_images:
            return {
                "decision": "uncertain",
                "confidence": 0.0,
                "keep_original": True,
                "distances": {},
                "best_match": "",
                "reason": "无字符图像",
            }

        # 计算待验证字符的平均 Hu 矩
        hu_vectors: List[np.ndarray] = []
        for img in char_images:
            hu = self.extract_hu_moments(img)
            if hu is not None:
                hu_vectors.append(hu)

        if not hu_vectors:
            return {
                "decision": "uncertain",
                "confidence": 0.0,
                "keep_original": True,
                "distances": {},
                "best_match": "",
                "reason": "无法计算 Hu 矩",
            }

        disputed_hu = np.mean(hu_vectors, axis=0)

        # 计算与各候选的标准 Hu 矩距离
        distances: Dict[str, float] = {}

        # 原始字符的距离
        original_hu = self.load_standard_hu_moments(disputed_char, font_type)
        if original_hu is not None:
            distances[disputed_char] = float(np.linalg.norm(disputed_hu - original_hu))

        # 候选字符的距离
        for candidate in candidate_chars:
            if candidate == disputed_char:
                continue
            std_hu = self.load_standard_hu_moments(candidate, font_type)
            if std_hu is not None:
                distances[candidate] = float(np.linalg.norm(disputed_hu - std_hu))

        if not distances:
            return {
                "decision": "uncertain",
                "confidence": 0.0,
                "keep_original": True,
                "distances": {},
                "best_match": "",
                "reason": "无标准字形可用于比对",
            }

        # 排序距离
        sorted_items = sorted(distances.items(), key=lambda x: x[1])
        best_char, best_dist = sorted_items[0]

        # 计算置信度
        if len(sorted_items) >= 2:
            second_dist = sorted_items[1][1]
            gap_ratio = (second_dist - best_dist) / (best_dist + 1e-6)

            # 最高/次高差距 < 20% → uncertain
            if gap_ratio < 0.2:
                return {
                    "decision": "uncertain",
                    "confidence": 0.3,
                    "keep_original": True,
                    "distances": distances,
                    "best_match": best_char,
                    "reason": "最高/次高差距<20%，无法确定",
                }
        else:
            gap_ratio = 1.0

        # 将距离转换为置信度
        max_dist = max(distances.values()) if distances else 1.0
        confidence = 1.0 - min(best_dist / (max_dist + 1e-6), 1.0)

        # 决策
        if best_char == disputed_char:
            return {
                "decision": "keep_original",
                "confidence": confidence,
                "keep_original": True,
                "distances": distances,
                "best_match": best_char,
            }

        if confidence >= 0.5:
            return {
                "decision": "accept_llm",
                "confidence": confidence,
                "keep_original": False,
                "distances": distances,
                "best_match": best_char,
            }

        return {
            "decision": "uncertain",
            "confidence": confidence,
            "keep_original": True,
            "distances": distances,
            "best_match": best_char,
            "reason": "置信度过低",
        }

    def get_glyph_candidates(
        self,
        disputed_char: str,
        original_text: str,
        llm_text: str,
        term_kb: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """获取字形候选字列表。

        候选来源：
        1. CONFUSABLE_PAIRS 中的易混淆字
        2. LLM 修改建议中的字
        3. term_kb 中的相关术语

        Args:
            disputed_char: 原始字符。
            original_text: 原始文本行。
            llm_text: LLM 校正后的文本行。
            term_kb: 术语知识库（可选）。

        Returns:
            候选字符列表（去重）。
        """
        candidates: Set[str] = set()

        # 1. 易混淆字对
        if disputed_char in CONFUSABLE_MAP:
            candidates.update(CONFUSABLE_MAP[disputed_char])

        # 2. LLM 修改的字
        min_len = min(len(original_text), len(llm_text))
        for i in range(min_len):
            if original_text[i] != llm_text[i]:
                candidates.add(llm_text[i])

        # 3. 术语知识库中的相关字
        if term_kb is not None:
            herbs = term_kb.get("herbs", [])
            points = term_kb.get("points", [])
            for herb in herbs:
                if disputed_char in herb:
                    for c in herb:
                        if c != disputed_char and len(c) == 1:
                            candidates.add(c)
            for point in points:
                if disputed_char in point:
                    for c in point:
                        if c != disputed_char and len(c) == 1:
                            candidates.add(c)

        # 确保 disputed_char 本身在候选中
        candidates.add(disputed_char)

        return list(candidates)
