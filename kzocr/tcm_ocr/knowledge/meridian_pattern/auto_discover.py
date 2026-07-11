"""
经络穴位 OCR 范式自动发现模块

通过分析针灸相关段落的校对记录，自动发现经络穴位名 OCR 错误模式，
生成候选 MeridianPointOCRPattern 插入数据库待审核。

核心流程：
1. 查询针灸相关段落的校对记录
2. 提取原文和修正文中的穴位名/经络名
3. Needleman-Wunsch 对齐
4. 对差异位置推断错误类型和实体类型
5. 插入 MeridianPointOCRPattern 表（review_status='pending'）
6. 返回发现的范式列表
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB
from kzocr.tcm_ocr.database.sqlite.book_db import BookDB

logger = logging.getLogger(__name__)


# 十二正经 + 奇经八脉 + 常用经外奇穴
MERIDIAN_NAMES = {
    '手太阴肺经', '手阳明大肠经', '足阳明胃经', '足太阴脾经',
    '手少阴心经', '手太阳小肠经', '足太阳膀胱经', '足少阴肾经',
    '手厥阴心包经', '手少阳三焦经', '足少阳胆经', '足厥阴肝经',
    '督脉', '任脉', '冲脉', '带脉', '阴跷脉', '阳跷脉', '阴维脉', '阳维脉',
    '肺经', '大肠经', '胃经', '脾经', '心经', '小肠经',
    '膀胱经', '肾经', '心包经', '三焦经', '胆经', '肝经',
}

# 常用穴位名（部分高频穴位）
COMMON_ACUPOINTS = {
    # 肺经
    '中府', '云门', '天府', '侠白', '尺泽', '孔最', '列缺', '经渠',
    '太渊', '鱼际', '少商',
    # 大肠经
    '商阳', '二间', '三间', '合谷', '阳溪', '偏历', '温溜', '下廉',
    '上廉', '手三里', '曲池', '肘髎', '手五里', '臂臑', '肩髃',
    '巨骨', '天鼎', '扶突', '口禾髎', '迎香',
    # 胃经
    '承泣', '四白', '巨髎', '地仓', '大迎', '颊车', '下关', '头维',
    '人迎', '水突', '气舍', '缺盆', '气户', '库房', '屋翳', '膺窗',
    '乳中', '乳根', '不容', '承满', '梁门', '关门', '太乙', '滑肉门',
    '天枢', '外陵', '大巨', '水道', '归来', '气冲', '髀关', '伏兔',
    '阴市', '梁丘', '犊鼻', '足三里', '上巨虚', '条口', '下巨虚',
    '丰隆', '解溪', '冲阳', '陷谷', '内庭', '厉兑',
    # 脾经
    '隐白', '大都', '太白', '公孙', '商丘', '三阴交', '漏谷', '地机',
    '阴陵泉', '血海', '箕门', '冲门', '府舍', '腹结', '大横', '腹哀',
    '食窦', '天溪', '胸乡', '周荣', '大包',
    # 心经
    '极泉', '青灵', '少海', '灵道', '通里', '阴郄', '神门', '少府', '少冲',
    # 小肠经
    '少泽', '前谷', '后溪', '腕骨', '阳谷', '养老', '支正', '小海',
    '肩贞', '臑俞', '天宗', '秉风', '曲垣', '肩外俞', '肩中俞',
    '天窗', '天容', '颧髎', '听宫',
    # 膀胱经
    '睛明', '攒竹', '眉冲', '曲差', '五处', '承光', '通天', '络却',
    '玉枕', '天柱', '大杼', '风门', '肺俞', '厥阴俞', '心俞', '督俞',
    '膈俞', '肝俞', '胆俞', '脾俞', '胃俞', '三焦俞', '肾俞', '气海俞',
    '大肠俞', '关元俞', '小肠俞', '膀胱俞', '中膂俞', '白环俞',
    '上髎', '次髎', '中髎', '下髎', '会阳', '承扶', '殷门', '浮郄',
    '委阳', '委中', '附分', '魄户', '膏肓', '神堂', '譩譆', '膈关',
    '魂门', '阳纲', '意舍', '胃仓', '肓门', '志室', '胞肓', '秩边',
    '合阳', '承筋', '承山', '飞扬', '跗阳', '昆仑', '仆参', '申脉',
    '金门', '京骨', '束骨', '足通谷', '至阴',
    # 肾经
    '涌泉', '然谷', '太溪', '大钟', '水泉', '照海', '复溜', '交信',
    '筑宾', '阴谷', '横骨', '大赫', '气穴', '四满', '中注', '肓俞',
    '商曲', '石关', '阴都', '腹通谷', '幽门', '步廊', '神封', '灵墟',
    '神藏', '彧中', '俞府',
    # 心包经
    '天池', '天泉', '曲泽', '郄门', '间使', '内关', '大陵', '劳宫', '中冲',
    # 三焦经
    '关冲', '液门', '中渚', '阳池', '外关', '支沟', '会宗', '三阳络',
    '四渎', '天井', '清冷渊', '消泺', '臑会', '肩髎', '天髎', '天牖',
    '翳风', '瘈脉', '颅息', '角孙', '耳门', '耳和髎', '丝竹空',
    # 胆经
    '瞳子髎', '听会', '上关', '颔厌', '悬颅', '悬厘', '曲鬓', '率谷',
    '天冲', '浮白', '头窍阴', '完骨', '本神', '阳白', '头临泣', '目窗',
    '正营', '承灵', '脑空', '风池', '肩井', '渊腋', '辄筋', '日月',
    '京门', '带脉', '五枢', '维道', '居髎', '环跳', '风市', '中渎',
    '膝阳关', '阳陵泉', '阳交', '外丘', '光明', '阳辅', '悬钟', '丘墟',
    '足临泣', '地五会', '侠溪', '足窍阴',
    # 肝经
    '大敦', '行间', '太冲', '中封', '蠡沟', '中都', '膝关', '曲泉',
    '阴包', '足五里', '阴廉', '急脉', '章门', '期门',
    # 督脉
    '长强', '腰俞', '腰阳关', '命门', '悬枢', '脊中', '中枢', '筋缩',
    '至阳', '灵台', '神道', '身柱', '陶道', '大椎', '哑门', '风府',
    '脑户', '强间', '后顶', '百会', '前顶', '囟会', '上星', '神庭',
    '素髎', '水沟', '兑端', '龈交',
    # 任脉
    '会阴', '曲骨', '中极', '关元', '石门', '气海', '阴交', '神阙',
    '水分', '下脘', '建里', '中脘', '上脘', '巨阙', '鸠尾', '中庭',
    '膻中', '玉堂', '紫宫', '华盖', '璇玑', '天突', '廉泉', '承浆',
    # 常用经外奇穴
    '四神聪', '印堂', '鱼腰', '上明', '太阳', '球后', '耳尖', '牵正',
    '翳明', '安眠', '颈百劳', '定喘', '夹脊', '胃脘下俞', '痞根',
    '腰眼', '十七椎', '腰奇', '肩前', '肘尖', '二白', '中泉', '中魁',
    '腰痛点', '外劳宫', '八邪', '四缝', '十宣', '髋骨', '鹤顶',
    '百虫窝', '内膝眼', '胆囊', '阑尾', '八风', '独阴', '气端',
}

# 穴位名 → 所属经络映射
ACUPOINT_TO_MERIDIAN: Dict[str, str] = {
    # 肺经
    '中府': '手太阴肺经', '云门': '手太阴肺经', '天府': '手太阴肺经',
    '侠白': '手太阴肺经', '尺泽': '手太阴肺经', '孔最': '手太阴肺经',
    '列缺': '手太阴肺经', '经渠': '手太阴肺经', '太渊': '手太阴肺经',
    '鱼际': '手太阴肺经', '少商': '手太阴肺经',
    # 大肠经
    '商阳': '手阳明大肠经', '合谷': '手阳明大肠经', '曲池': '手阳明大肠经',
    '迎香': '手阳明大肠经', '手三里': '手阳明大肠经', '偏历': '手阳明大肠经',
    '阳溪': '手阳明大肠经', '下廉': '手阳明大肠经', '上廉': '手阳明大肠经',
    '肩髃': '手阳明大肠经', '臂臑': '手阳明大肠经',
    # 胃经
    '承泣': '足阳明胃经', '四白': '足阳明胃经', '地仓': '足阳明胃经',
    '颊车': '足阳明胃经', '下关': '足阳明胃经', '头维': '足阳明胃经',
    '人迎': '足阳明胃经', '乳根': '足阳明胃经', '不容': '足阳明胃经',
    '承满': '足阳明胃经', '梁门': '足阳明胃经', '天枢': '足阳明胃经',
    '归来': '足阳明胃经', '气冲': '足阳明胃经', '伏兔': '足阳明胃经',
    '阴市': '足阳明胃经', '梁丘': '足阳明胃经', '犊鼻': '足阳明胃经',
    '足三里': '足阳明胃经', '上巨虚': '足阳明胃经', '丰隆': '足阳明胃经',
    '解溪': '足阳明胃经', '内庭': '足阳明胃经', '厉兑': '足阳明胃经',
    '水道': '足阳明胃经', '条口': '足阳明胃经', '下巨虚': '足阳明胃经',
    # 脾经
    '隐白': '足太阴脾经', '太白': '足太阴脾经', '公孙': '足太阴脾经',
    '商丘': '足太阴脾经', '三阴交': '足太阴脾经', '地机': '足太阴脾经',
    '阴陵泉': '足太阴脾经', '血海': '足太阴脾经', '大包': '足太阴脾经',
    # 心经
    '极泉': '手少阴心经', '少海': '手少阴心经', '通里': '手少阴心经',
    '阴郄': '手少阴心经', '神门': '手少阴心经', '少府': '手少阴心经',
    '少冲': '手少阴心经', '灵道': '手少阴心经',
    # 小肠经
    '少泽': '手太阳小肠经', '后溪': '手太阳小肠经', '腕骨': '手太阳小肠经',
    '养老': '手太阳小肠经', '小海': '手太阳小肠经', '肩贞': '手太阳小肠经',
    '天宗': '手太阳小肠经', '颧髎': '手太阳小肠经', '听宫': '手太阳小肠经',
    # 膀胱经
    '睛明': '足太阳膀胱经', '攒竹': '足太阳膀胱经', '天柱': '足太阳膀胱经',
    '大杼': '足太阳膀胱经', '风门': '足太阳膀胱经', '肺俞': '足太阳膀胱经',
    '厥阴俞': '足太阳膀胱经', '心俞': '足太阳膀胱经', '督俞': '足太阳膀胱经',
    '膈俞': '足太阳膀胱经', '肝俞': '足太阳膀胱经', '胆俞': '足太阳膀胱经',
    '脾俞': '足太阳膀胱经', '胃俞': '足太阳膀胱经', '三焦俞': '足太阳膀胱经',
    '肾俞': '足太阳膀胱经', '大肠俞': '足太阳膀胱经', '关元俞': '足太阳膀胱经',
    '小肠俞': '足太阳膀胱经', '膀胱俞': '足太阳膀胱经',
    '次髎': '足太阳膀胱经', '承扶': '足太阳膀胱经', '殷门': '足太阳膀胱经',
    '委阳': '足太阳膀胱经', '委中': '足太阳膀胱经', '膏肓': '足太阳膀胱经',
    '志室': '足太阳膀胱经', '秩边': '足太阳膀胱经', '承山': '足太阳膀胱经',
    '飞扬': '足太阳膀胱经', '昆仑': '足太阳膀胱经', '申脉': '足太阳膀胱经',
    '金门': '足太阳膀胱经', '京骨': '足太阳膀胱经', '至阴': '足太阳膀胱经',
    # 肾经
    '涌泉': '足少阴肾经', '然谷': '足少阴肾经', '太溪': '足少阴肾经',
    '大钟': '足少阴肾经', '照海': '足少阴肾经', '复溜': '足少阴肾经',
    '交信': '足少阴肾经', '筑宾': '足少阴肾经', '阴谷': '足少阴肾经',
    '肓俞': '足少阴肾经', '俞府': '足少阴肾经',
    # 心包经
    '天池': '手厥阴心包经', '天泉': '手厥阴心包经', '曲泽': '手厥阴心包经',
    '郄门': '手厥阴心包经', '间使': '手厥阴心包经', '内关': '手厥阴心包经',
    '大陵': '手厥阴心包经', '劳宫': '手厥阴心包经', '中冲': '手厥阴心包经',
    # 三焦经
    '关冲': '手少阳三焦经', '中渚': '手少阳三焦经', '阳池': '手少阳三焦经',
    '外关': '手少阳三焦经', '支沟': '手少阳三焦经', '天井': '手少阳三焦经',
    '肩髎': '手少阳三焦经', '翳风': '手少阳三焦经', '角孙': '手少阳三焦经',
    '耳门': '手少阳三焦经', '丝竹空': '手少阳三焦经',
    # 胆经
    '瞳子髎': '足少阳胆经', '听会': '足少阳胆经', '率谷': '足少阳胆经',
    '阳白': '足少阳胆经', '头临泣': '足少阳胆经', '风池': '足少阳胆经',
    '肩井': '足少阳胆经', '日月': '足少阳胆经', '带脉': '足少阳胆经',
    '环跳': '足少阳胆经', '风市': '足少阳胆经', '中渎': '足少阳胆经',
    '膝阳关': '足少阳胆经', '阳陵泉': '足少阳胆经', '光明': '足少阳胆经',
    '悬钟': '足少阳胆经', '丘墟': '足少阳胆经', '足临泣': '足少阳胆经',
    '侠溪': '足少阳胆经', '足窍阴': '足少阳胆经',
    # 肝经
    '大敦': '足厥阴肝经', '行间': '足厥阴肝经', '太冲': '足厥阴肝经',
    '中封': '足厥阴肝经', '蠡沟': '足厥阴肝经', '中都': '足厥阴肝经',
    '曲泉': '足厥阴肝经', '章门': '足厥阴肝经', '期门': '足厥阴肝经',
    # 督脉
    '长强': '督脉', '腰俞': '督脉', '腰阳关': '督脉', '命门': '督脉',
    '至阳': '督脉', '身柱': '督脉', '大椎': '督脉', '哑门': '督脉',
    '风府': '督脉', '百会': '督脉', '上星': '督脉', '神庭': '督脉',
    '素髎': '督脉', '水沟': '督脉',
    # 任脉
    '会阴': '任脉', '中极': '任脉', '关元': '任脉', '石门': '任脉',
    '气海': '任脉', '神阙': '任脉', '下脘': '任脉', '中脘': '任脉',
    '上脘': '任脉', '巨阙': '任脉', '膻中': '任脉', '天突': '任脉',
    '廉泉': '任脉', '承浆': '任脉',
}


def extract_meridian_point_names(text: str) -> List[str]:
    """
    提取文本中的经络穴位名

    基于内置经络穴位词典进行匹配提取。

    Args:
        text: 输入文本

    Returns:
        提取到的穴位名/经络名列表（按出现顺序）
    """
    if not text:
        return []

    results: List[str] = []
    i = 0
    text_len = len(text)

    while i < text_len:
        matched = False
        # 从长到短尝试匹配（穴位名最长 4 字）
        for length in range(min(6, text_len - i), 0, -1):
            candidate = text[i:i + length]
            if candidate in COMMON_ACUPOINTS or candidate in MERIDIAN_NAMES:
                results.append(candidate)
                i += length
                matched = True
                break
        if not matched:
            i += 1

    return results


def is_valid_meridian_point(name: str) -> bool:
    """
    验证合法穴位名/经络名

    Args:
        name: 待验证的名称

    Returns:
        True 如果是合法穴位名或经络名
    """
    if not name or len(name) < 2:
        return False
    if name in COMMON_ACUPOINTS:
        return True
    if name in MERIDIAN_NAMES:
        return True
    # 基本格式：2-6 个汉字
    if not re.match(r'^[\u4e00-\u9fff]{2,6}$', name):
        return False
    return False


def infer_entity_type(point_name: str) -> str:
    """
    推断实体类型

    Args:
        point_name: 穴位/经络名称

    Returns:
        实体类型：'acupoint' | 'meridian' | 'extra_point' | 'other'
    """
    if point_name in MERIDIAN_NAMES:
        return 'meridian'
    if point_name in COMMON_ACUPOINTS:
        if '奇穴' in point_name or point_name in {
            '四神聪', '印堂', '太阳', '耳尖', '定喘', '夹脊',
            '腰眼', '十宣', '八邪', '四缝', '八风',
        }:
            return 'extra_point'
        return 'acupoint'
    return 'other'


def get_meridian_belonging(point_name: str) -> str:
    """
    获取穴位所属经络

    Args:
        point_name: 穴位名称

    Returns:
        所属经络名称，未知返回空字符串
    """
    return ACUPOINT_TO_MERIDIAN.get(point_name, '')


def _align_point_sequences(
    seq_a: List[str], seq_b: List[str]
) -> List[Tuple[Optional[str], Optional[str]]]:
    """
    简化版 Needleman-Wunsch 对齐穴位名序列

    Args:
        seq_a: 第一个序列
        seq_b: 第二个序列

    Returns:
        对齐结果
    """
    if not seq_a and not seq_b:
        return []
    if not seq_a:
        return [(None, b) for b in seq_b]
    if not seq_b:
        return [(a, None) for a in seq_a]

    match_score = 1
    mismatch_score = -1
    gap_score = -2

    len_a = len(seq_a)
    len_b = len(seq_b)

    score_matrix = [[0] * (len_b + 1) for _ in range(len_a + 1)]

    for i in range(len_a + 1):
        score_matrix[i][0] = gap_score * i
    for j in range(len_b + 1):
        score_matrix[0][j] = gap_score * j

    for i in range(1, len_a + 1):
        for j in range(1, len_b + 1):
            match = score_matrix[i - 1][j - 1] + (
                match_score if seq_a[i - 1] == seq_b[j - 1] else mismatch_score
            )
            delete = score_matrix[i - 1][j] + gap_score
            insert = score_matrix[i][j - 1] + gap_score
            score_matrix[i][j] = max(match, delete, insert)

    alignment: List[Tuple[Optional[str], Optional[str]]] = []
    i, j = len_a, len_b
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            match = score_matrix[i - 1][j - 1] + (
                match_score if seq_a[i - 1] == seq_b[j - 1] else mismatch_score
            )
            if score_matrix[i][j] == match:
                alignment.append((seq_a[i - 1], seq_b[j - 1]))
                i -= 1
                j -= 1
                continue

        if i > 0 and score_matrix[i][j] == score_matrix[i - 1][j] + gap_score:
            alignment.append((seq_a[i - 1], None))
            i -= 1
        elif j > 0:
            alignment.append((None, seq_b[j - 1]))
            j -= 1
        else:
            break

    alignment.reverse()
    return alignment


def _infer_meridian_error_type(original: str, corrected: str) -> str:
    """
    推断经络穴位 OCR 错误类型

    Args:
        original: 原始文本
        corrected: 修正文本

    Returns:
        错误类型：'similar_glyph' | 'stroke_error' | 'component_swap' | 'split_merge' | 'other'
    """
    if not original or not corrected:
        return 'other'

    len_diff = abs(len(original) - len(corrected))
    if len_diff > 0:
        return 'split_merge'

    diff_positions = []
    for idx in range(min(len(original), len(corrected))):
        if original[idx] != corrected[idx]:
            diff_positions.append(idx)

    if not diff_positions:
        return 'other'

    if len(diff_positions) <= 2:
        return 'similar_glyph'

    return 'component_swap'


def _is_acupuncture_related(text: str) -> bool:
    """
    检查文本是否与针灸相关

    Args:
        text: 输入文本

    Returns:
        True 如果包含针灸相关关键词
    """
    keywords = {
        '针', '灸', '针刺', '艾灸', '针灸', '穴位', '经络', '经脉',
        '取穴', '配穴', '主穴', '辅穴', '刺法', '灸法', '得气',
        '补泻', '提插', '捻转', '留针', ' acupuncture', ' meridian',
    }
    text.lower()
    return any(kw in text for kw in keywords)


def auto_discover_meridian_patterns(
    book_id: str,
    db_book: BookDB,
    db_pg: RuntimeDB,
) -> List[dict]:
    """
    自动发现经络穴位 OCR 范式

    核心流程：
        1. 查询针灸相关段落的校对记录
        2. 提取原文和修正文中的穴位名/经络名
        3. Needleman-Wunsch 对齐
        4. 对差异位置推断错误类型和实体类型
        5. 插入 MeridianPointOCRPattern 表（review_status='pending'）
        6. 返回发现的范式列表

    Args:
        book_id: 书籍 ID
        db_book: BookDB 实例（SQLite 书籍库）
        db_pg: RuntimeDB 实例（PostgreSQL 运行库）

    Returns:
        发现的范式列表，每个元素为 dict::

            [
                {
                    'id': int,
                    'correct_name': str,
                    'ocr_error_pattern': str,
                    'entity_type': str,
                    'meridian_belonging': str,
                    'confidence_score': float,
                    'review_status': 'pending',
                    # ...
                },
                ...
            ]
    """
    discovered: List[dict] = []

    try:
        # 1. 查询针灸相关段落的校对记录
        with db_book.get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, line_id, original_text, corrected_text,
                       correction_stage, reviewer_accuracy, paragraph_id
                FROM ProofreadRecord
                WHERE corrected_by IN ('human_level1', 'human_level2', 'human_final', 'reviewer')
                ORDER BY created_at DESC
                LIMIT 5000
                """,
            )
            all_records = [dict(row) for row in cursor.fetchall()]

        # 过滤针灸相关记录
        records = [r for r in all_records
                   if _is_acupuncture_related(r.get('original_text', '') or '')
                   or _is_acupuncture_related(r.get('corrected_text', '') or '')]

        if not records:
            logger.info("No acupuncture-related proofread records found for book %s", book_id)
            return discovered

        logger.info(
            "Processing %d acupuncture-related records out of %d total",
            len(records), len(all_records),
        )

        # 2-4. 逐条分析
        candidate_patterns: Dict[str, dict] = {}

        for record in records:
            original_text = record.get('original_text', '') or ''
            corrected_text = record.get('corrected_text', '') or ''

            if not original_text or not corrected_text:
                continue

            # 提取穴位名
            original_points = extract_meridian_point_names(original_text)
            corrected_points = extract_meridian_point_names(corrected_text)

            if not original_points or not corrected_points:
                continue

            # Needleman-Wunsch 对齐
            alignment = _align_point_sequences(original_points, corrected_points)

            # 分析对齐结果
            for orig_point, corr_point in alignment:
                if orig_point is None or corr_point is None:
                    continue
                if orig_point == corr_point:
                    continue

                # 排除同一字形变体
                if _is_same_glyph_variant(orig_point, corr_point):
                    continue

                # 确保 corrected 是合法穴位名
                if not is_valid_meridian_point(corr_point):
                    continue

                cache_key = f"{corr_point}|{orig_point}"

                _infer_meridian_error_type(orig_point, corr_point)
                entity_type = infer_entity_type(corr_point)
                meridian_belonging = get_meridian_belonging(corr_point)

                # 计算置信度
                confidence = 0.6  # 基础置信度
                stage = record.get('correction_stage', 'auto')
                stage_weights = {
                    'golden': 1.0, 'human_final': 0.9, 'human_level2': 0.85,
                    'human_level1': 0.8, 'reviewer': 0.75,
                }
                confidence = stage_weights.get(stage, 0.6)

                if cache_key not in candidate_patterns:
                    candidate_patterns[cache_key] = {
                        'correct_name': corr_point,
                        'ocr_error_pattern': orig_point,
                        'entity_type': entity_type,
                        'meridian_belonging': meridian_belonging,
                        'confidence_score': round(confidence, 4),
                        'evidence_count': 1,
                        'source_books': [str(book_id)],
                        'auto_discovered': True,
                        'review_status': 'pending',
                        'status': 'active',
                    }
                else:
                    candidate_patterns[cache_key]['evidence_count'] += 1
                    if str(book_id) not in candidate_patterns[cache_key]['source_books']:
                        candidate_patterns[cache_key]['source_books'].append(str(book_id))

        # 5. 插入 MeridianPointOCRPattern 表
        for pattern in candidate_patterns.values():
            try:
                pattern_id = db_pg.create_meridian_point_pattern(
                    correct_name=pattern['correct_name'],
                    ocr_error_pattern=pattern['ocr_error_pattern'],
                    entity_type=pattern['entity_type'],
                    meridian_belonging=pattern.get('meridian_belonging'),
                    source_books=pattern['source_books'],
                    evidence_count=pattern['evidence_count'],
                    auto_discovered=True,
                    confidence_score=pattern['confidence_score'],
                    review_status='pending',
                )
                pattern['id'] = pattern_id
                discovered.append(pattern)
                logger.debug(
                    "Discovered meridian pattern: %s -> %s (type=%s, entity=%s)",
                    pattern['ocr_error_pattern'],
                    pattern['correct_name'],
                    pattern.get('error_type', ''),
                    pattern['entity_type'],
                )
            except Exception as e:
                logger.error(
                    "Failed to insert meridian pattern %s -> %s: %s",
                    pattern['ocr_error_pattern'], pattern['correct_name'], e,
                )

        logger.info(
            "Auto-discovered %d meridian OCR patterns from book %s",
            len(discovered), book_id,
        )

    except Exception as e:
        logger.error("Error in auto_discover_meridian_patterns: %s", e)

    return discovered


def _is_same_glyph_variant(text_a: str, text_b: str) -> bool:
    """
    检查两个文本是否为同一字形变体

    Args:
        text_a: 第一个文本
        text_b: 第二个文本

    Returns:
        True 如果是同一字形变体
    """
    if text_a == text_b:
        return True

    traditional_to_simplified = {
        '黨': '党', '參': '参', '麥': '麦', '龍': '龙', '龜': '龟',
        '魚': '鱼', '連': '连', '術': '术', '黃': '黄', '蓮': '莲',
        '當': '当', '歸': '归', '藥': '药', '車': '车', '陽': '阳',
        '陰': '阴', '經': '经', '門': '门', '東': '东', '風': '风',
        '會': '会', '髎': '髎', '谿': '溪', '穀': '谷', '卻': '郄',
    }

    simplified_a = ''.join(traditional_to_simplified.get(c, c) for c in text_a)
    simplified_b = ''.join(traditional_to_simplified.get(c, c) for c in text_b)

    return simplified_a == simplified_b
