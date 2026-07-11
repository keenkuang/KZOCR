"""
中药名 OCR 范式自动发现模块

通过分析人工校对记录（ProofreadRecord），自动发现中药名 OCR 错误模式，
生成候选 HerbOCRPattern 插入数据库待审核。

核心流程：
1. 从 ProofreadRecord 查询人工校对记录
2. 提取原文和修正文中的药材名
3. Needleman-Wunsch 对齐药名序列
4. 对替换位置提取候选范式
5. 排除同一字形变体（如「甘草」vs「甘草」无实质差异）
6. 插入 HerbOCRPattern 表（review_status='pending'）
7. 返回发现的范式列表
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB
from kzocr.tcm_ocr.database.sqlite.book_db import BookDB

logger = logging.getLogger(__name__)


# 常见中药名列表（用于匹配）
# 实际生产环境应从数据库加载完整中药词典
COMMON_HERB_NAMES = {
    '人参', '党参', '西洋参', '太子参', '黄芪', '白术', '山药', '甘草',
    '当归', '熟地黄', '生地黄', '白芍', '阿胶', '何首乌', '龙眼肉',
    '北沙参', '南沙参', '麦冬', '天冬', '石斛', '玉竹', '黄精',
    '鹿茸', '淫羊藿', '巴戟天', '仙茅', '杜仲', '续断', '肉苁蓉',
    '补骨脂', '菟丝子', '沙苑子', '锁阳', '韭菜子', '阳起石',
    '当归', '熟地黄', '白芍', '阿胶', '何首乌', '龙眼肉', '楮实子',
    '北沙参', '南沙参', '百合', '麦冬', '天冬', '石斛', '玉竹',
    '黄精', '枸杞子', '墨旱莲', '女贞子', '桑椹', '黑芝麻',
    '龟甲', '鳖甲', '五味子', '乌梅', '诃子', '肉豆蔻',
    '山茱萸', '桑螵蛸', '海螵蛸', '莲子', '芡实', '覆盆子',
    '麻黄', '桂枝', '紫苏', '生姜', '香薷', '荆芥', '防风',
    '羌活', '白芷', '细辛', '苍耳子', '辛夷', '葱白',
    '薄荷', '牛蒡子', '蝉蜕', '桑叶', '菊花', '葛根',
    '柴胡', '升麻', '蔓荆子', '淡豆豉', '浮萍', '木贼',
    '石膏', '知母', '芦根', '天花粉', '竹叶', '栀子',
    '夏枯草', '决明子', '谷精草', '密蒙花', '青葙子',
    '黄芩', '黄连', '黄柏', '龙胆', '苦参', '白鲜皮',
    '金银花', '连翘', '蒲公英', '紫花地丁', '野菊花',
    '板蓝根', '大青叶', '青黛', '贯众', '鱼腥草',
    '射干', '山豆根', '马勃', '白头翁', '马齿苋',
    '生地黄', '玄参', '牡丹皮', '赤芍', '紫草', '水牛角',
    '青蒿', '白薇', '地骨皮', '银柴胡', '胡黄连',
    '大黄', '芒硝', '番泻叶', '芦荟', '火麻仁', '郁李仁',
    '独活', '威灵仙', '川乌', '草乌', '乌梢蛇', '蕲蛇',
    '木瓜', '蚕沙', '伸筋草', '寻骨风', '松节', '海风藤',
    '茯苓', '薏苡仁', '猪苓', '泽泻', '冬瓜皮', '玉米须',
    '车前子', '滑石', '木通', '通草', '瞿麦', '萹蓄',
    '茵陈', '金钱草', '虎杖', '垂盆草', '鸡骨草',
    '附子', '干姜', '肉桂', '吴茱萸', '小茴香', '丁香',
    '川芎', '延胡索', '郁金', '姜黄', '乳香', '没药',
    '丹参', '红花', '桃仁', '益母草', '牛膝', '鸡血藤',
    '半夏', '天南星', '白附子', '白芥子', '皂荚', '旋覆花',
    '川贝母', '浙贝母', '瓜蒌', '竹茹', '竹沥', '天竺黄',
    '苦杏仁', '紫苏子', '百部', '紫菀', '款冬花', '马兜铃',
    '朱砂', '磁石', '龙骨', '酸枣仁', '柏子仁', '远志',
    '石决明', '牡蛎', '代赭石', '羚羊角', '牛黄', '钩藤',
    '天麻', '地龙', '全蝎', '蜈蚣', '僵蚕',
    '麝香', '冰片', '苏合香', '石菖蒲', '蟾酥',
    '五味子', '诃子', '乌梅', '五倍子', '罂粟壳',
    '山茱萸', '桑螵蛸', '海螵蛸', '莲子', '芡实',
    '常山', '瓜蒂', '胆矾', '藜芦',
}

# 毒性等级映射
HERB_TOXICITY_MAP = {
    '附子': 'high', '川乌': 'high', '草乌': 'high', '马钱子': 'high',
    '斑蝥': 'high', '蟾酥': 'high', '雄黄': 'high', '朱砂': 'high',
    '信石': 'high', '甘遂': 'high', '大戟': 'high', '芫花': 'high',
    '商陆': 'high', '牵牛子': 'moderate', '巴豆': 'high',
    '水蛭': 'moderate', '虻虫': 'moderate', '三棱': 'low',
    '莪术': 'low', '穿山甲': 'low',
}

# 炮制方法关键词
PROCESSING_KEYWORDS = {
    '炙', '炒', '煅', '淬', '蒸', '煮', '炖', '煨',
    '制', '炮', '焙', '烘', '晒', '阴干', '酒洗',
    '姜汁', '醋', '盐', '蜜', '酒', '醋炙', '盐炙',
    '蜜炙', '酒炙', '姜汁炙', '炒黄', '炒焦', '炒炭',
}


def extract_herb_names(text: str) -> List[str]:
    """
    从文本提取所有药材名

    基于内置中药词典进行正向最大匹配提取。

    Args:
        text: 输入文本

    Returns:
        提取到的药材名列表（按出现顺序，可能重复）
    """
    if not text:
        return []

    results: List[str] = []
    i = 0
    text_len = len(text)

    while i < text_len:
        matched = False
        # 从长到短尝试匹配
        for length in range(min(8, text_len - i), 0, -1):
            candidate = text[i:i + length]
            if candidate in COMMON_HERB_NAMES:
                results.append(candidate)
                i += length
                matched = True
                break
        if not matched:
            i += 1

    return results


def is_valid_herb_name(name: str) -> bool:
    """
    验证合法药材名

    检查名称是否在中药词典中，且符合药材名基本格式。

    Args:
        name: 待验证的药材名

    Returns:
        True 如果是合法药材名
    """
    if not name or len(name) < 2:
        return False
    # 基本格式：2-6 个汉字
    if not re.match(r'^[\u4e00-\u9fff]{2,6}$', name):
        return False
    return name in COMMON_HERB_NAMES


def get_herb_toxicity(herb_name: str) -> str:
    """
    获取药材毒性等级

    Args:
        herb_name: 药材名

    Returns:
        毒性等级字符串：'high' | 'moderate' | 'low' | 'none'
    """
    return HERB_TOXICITY_MAP.get(herb_name, 'none')


def infer_error_type(original: str, corrected: str) -> str:
    """
    推断 OCR 错误类型

    通过比较原始文本和修正文本的差异，推断错误类型。

    Args:
        original: 原始 OCR 文本
        corrected: 修正后文本

    Returns:
        错误类型字符串：
            - 'similar_glyph': 形似字错误
            - 'stroke_error': 笔画错误
            - 'component_swap': 部件替换
            - 'split_merge': 拆/合字错误
            - 'other': 其他
    """
    if not original or not corrected:
        return 'other'

    # 使用简化差异分析
    diff_positions = []
    max(len(original), len(corrected))
    min_len = min(len(original), len(corrected))

    for i in range(min_len):
        if original[i] != corrected[i]:
            diff_positions.append((i, original[i], corrected[i]))

    # 长度差异分析
    len_diff = abs(len(original) - len(corrected))

    if len_diff > 0:
        return 'split_merge'

    if not diff_positions:
        return 'other'

    # 分析差异字符的特征
    similar_glyph_pairs = {
        ('黄', '黃'), ('芩', '苓'), ('连', '連'), ('术', '朮'),
        ('党', '黨'), ('参', '參'), ('麦', '麥'), ('龙', '龍'),
        ('龟', '龜'), ('鱼腥', '魚腥'), ('甘草', '甘革'),
        ('桂枝', '柱枝'), ('白芍', '白勺'), ('熟地', '熟池'),
        ('当归', '当旧'), ('川芎', '川莒'), ('茯苓', '伏苓'),
        ('白术', '白木'), ('陈皮', '阵皮'), ('半夏', '半厦'),
        ('柴胡', '紫胡'), ('葛根', '葛极'), ('黄芩', '黄苓'),
        ('黄连', '黄莲'), ('黄芪', '黄蔑'), ('人参', '人叁'),
        ('丹参', '单参'), ('麦冬', '麦东'), ('五味子', '五味于'),
    }

    for _, orig_char, corr_char in diff_positions:
        if (orig_char, corr_char) in similar_glyph_pairs or \
           (corr_char, orig_char) in similar_glyph_pairs:
            return 'similar_glyph'

    # 笔画数差异判断
    def stroke_count_diff(c1: str, c2: str) -> int:
        """简单估算笔画差异（基于 Unicode CJK 笔画特征）"""
        # 简化的笔画估算：通过字符结构粗略判断
        simple_strokes = {
            '一': 1, '二': 2, '三': 3, '十': 2, '口': 3, '日': 4,
            '田': 5, '目': 5, '木': 4, '本': 5, '未': 5, '末': 5,
        }
        s1 = simple_strokes.get(c1, 8)  # 默认值 8（平均笔画数）
        s2 = simple_strokes.get(c2, 8)
        return abs(s1 - s2)

    total_stroke_diff = sum(
        stroke_count_diff(orig, corr)
        for _, orig, corr in diff_positions
    )

    if total_stroke_diff <= 2 and len(diff_positions) <= 2:
        return 'stroke_error'
    elif len(diff_positions) <= 2:
        return 'component_swap'

    return 'other'


def calculate_confidence(corr_record: dict) -> float:
    """
    计算置信度分数

    基于校对记录的特征计算候选范式的置信度。

    Args:
        corr_record: 校对记录字典，包含：
            - correction_stage: 校正阶段
            - reviewer_accuracy: 审校者准确率
            - evidence_count: 证据次数

    Returns:
        置信度分数 (0.0 ~ 1.0)
    """
    base_confidence = 0.5

    # 根据校正阶段调整
    stage_weights = {
        'golden': 1.0,
        'human_final': 0.9,
        'human_level2': 0.85,
        'human_level1': 0.8,
        'reviewer': 0.75,
        'llm': 0.6,
        'auto': 0.5,
    }
    stage = corr_record.get('correction_stage', 'auto')
    base_confidence = stage_weights.get(stage, 0.5)

    # 审校者准确率加成
    reviewer_accuracy = corr_record.get('reviewer_accuracy')
    if reviewer_accuracy is not None:
        try:
            acc = float(reviewer_accuracy)
            base_confidence = base_confidence * 0.7 + acc * 0.3
        except (ValueError, TypeError):
            pass

    # 证据次数加成
    evidence_count = corr_record.get('evidence_count', 1)
    evidence_bonus = min(0.1, (evidence_count - 1) * 0.02)
    base_confidence += evidence_bonus

    return round(min(1.0, max(0.0, base_confidence)), 4)


def align_sequences(seq_a: List[str], seq_b: List[str]) -> List[Tuple[Optional[str], Optional[str]]]:
    """
    简化版 Needleman-Wunsch 序列对齐

    对齐两个药材名序列，返回对齐结果。
    匹配得分 +1，不匹配 -1，gap -2。

    Args:
        seq_a: 第一个序列（原文药材名列表）
        seq_b: 第二个序列（修正文药材名列表）

    Returns:
        对齐结果列表，每个元素为 (seq_a_item, seq_b_item)，
        None 表示 gap。

    Example:
        >>> align_sequences(['甘草', '黄芩', '黄连'], ['甘草', '黄苓', '黄连'])
        [('甘草', '甘草'), ('黄芩', '黄苓'), ('黄连', '黄连')]
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

    # 初始化得分矩阵
    score_matrix = [[0] * (len_b + 1) for _ in range(len_a + 1)]

    # 初始化边界
    for i in range(len_a + 1):
        score_matrix[i][0] = gap_score * i
    for j in range(len_b + 1):
        score_matrix[0][j] = gap_score * j

    # 填充矩阵
    for i in range(1, len_a + 1):
        for j in range(1, len_b + 1):
            match = score_matrix[i - 1][j - 1] + (
                match_score if seq_a[i - 1] == seq_b[j - 1] else mismatch_score
            )
            delete = score_matrix[i - 1][j] + gap_score
            insert = score_matrix[i][j - 1] + gap_score
            score_matrix[i][j] = max(match, delete, insert)

    # 回溯
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


def auto_discover_herb_ocr_patterns(
    book_id: str,
    db_book: BookDB,
    db_pg: RuntimeDB,
) -> List[dict]:
    """
    自动发现中药名 OCR 范式

    核心流程：
        1. 从 ProofreadRecord 查询人工校对记录
        2. 提取原文和修正文中的药材名
        3. Needleman-Wunsch 对齐药名序列
        4. 对替换位置提取候选范式
        5. 排除同一字形变体
        6. 插入 HerbOCRPattern 表（review_status='pending'）
        7. 返回发现的范式列表

    Args:
        book_id: 书籍 ID
        db_book: BookDB 实例（SQLite 书籍库）
        db_pg: RuntimeDB 实例（PostgreSQL 运行库）

    Returns:
        发现的范式列表，每个元素为 dict::

            [
                {
                    'id': int,                  # 插入后的范式 ID
                    'correct_herb': str,
                    'ocr_error_pattern': str,
                    'error_type': str,
                    'confidence_score': float,
                    'toxicity_level': str,
                    'review_status': 'pending',
                    # ...
                },
                ...
            ]
    """
    discovered: List[dict] = []

    try:
        # 1. 从 BookDB 查询人工校对记录
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
            records = [dict(row) for row in cursor.fetchall()]

        if not records:
            logger.info("No proofread records found for book %s", book_id)
            return discovered

        logger.info("Processing %d proofread records for herb pattern discovery", len(records))

        # 2-4. 逐条分析校对记录
        candidate_patterns: Dict[str, dict] = {}  # 去重用 key: "corrected|original"

        for record in records:
            original_text = record.get('original_text', '') or ''
            corrected_text = record.get('corrected_text', '') or ''

            if not original_text or not corrected_text:
                continue

            # 提取药材名
            original_herbs = extract_herb_names(original_text)
            corrected_herbs = extract_herb_names(corrected_text)

            if not original_herbs or not corrected_herbs:
                continue

            # Needleman-Wunsch 对齐
            alignment = align_sequences(original_herbs, corrected_herbs)

            # 分析对齐结果中的替换
            for orig_herb, corr_herb in alignment:
                if orig_herb is None or corr_herb is None:
                    continue  # skip gaps
                if orig_herb == corr_herb:
                    continue  # 无变化

                # 5. 排除同一字形变体
                if _is_same_glyph_variant(orig_herb, corr_herb):
                    continue

                # 确保 corrected 是合法药材名
                if not is_valid_herb_name(corr_herb):
                    continue

                # 构建候选范式
                cache_key = f"{corr_herb}|{orig_herb}"

                error_type = infer_error_type(orig_herb, corr_herb)
                confidence = calculate_confidence(record)
                toxicity = get_herb_toxicity(corr_herb)

                if cache_key not in candidate_patterns:
                    candidate_patterns[cache_key] = {
                        'correct_herb': corr_herb,
                        'ocr_error_pattern': orig_herb,
                        'error_type': error_type,
                        'confidence_score': confidence,
                        'toxicity_level': toxicity if toxicity != 'none' else None,
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

        # 6. 插入 HerbOCRPattern 表
        for pattern in candidate_patterns.values():
            try:
                pattern_id = db_pg.create_herb_ocr_pattern(
                    correct_herb=pattern['correct_herb'],
                    ocr_error_pattern=pattern['ocr_error_pattern'],
                    error_type=pattern['error_type'],
                    toxicity_level=pattern.get('toxicity_level'),
                    source_books=pattern['source_books'],
                    evidence_count=pattern['evidence_count'],
                    auto_discovered=True,
                    confidence_score=pattern['confidence_score'],
                    review_status='pending',
                )
                pattern['id'] = pattern_id
                discovered.append(pattern)
                logger.debug(
                    "Discovered herb pattern: %s -> %s (type=%s, conf=%.2f)",
                    pattern['ocr_error_pattern'],
                    pattern['correct_herb'],
                    pattern['error_type'],
                    pattern['confidence_score'],
                )
            except Exception as e:
                logger.error(
                    "Failed to insert herb pattern %s -> %s: %s",
                    pattern['ocr_error_pattern'], pattern['correct_herb'], e,
                )

        logger.info(
            "Auto-discovered %d herb OCR patterns from book %s",
            len(discovered), book_id,
        )

    except Exception as e:
        logger.error("Error in auto_discover_herb_ocr_patterns: %s", e)

    return discovered


def _is_same_glyph_variant(text_a: str, text_b: str) -> bool:
    """
    检查两个文本是否为同一字形变体（无实质差异）

    例如：「黃芩」vs「黄芩」是繁简变体，不算 OCR 错误。

    Args:
        text_a: 第一个文本
        text_b: 第二个文本

    Returns:
        True 如果是同一字形变体
    """
    if text_a == text_b:
        return True

    # 繁简对照表（常见中药材）
    traditional_to_simplified = {
        '黨': '党', '參': '参', '麥': '麦', '龍': '龙',
        '龜': '龟', '魚': '鱼', '連': '连', '術': '术',
        '黃': '黄', '蓮': '莲',
        '當': '当', '歸': '归', '藥': '药', '車': '车',
        '蔭': '荫', '頭': '头', '門': '门', '貝': '贝',
        '膽': '胆', '礬': '矾', '蘇': '苏', '葉': '叶',
        '陳': '陈', '東': '东', '極': '极', '來': '来',
        '馬': '马', '鳥': '鸟',
    }

    # 将 text_a 转换为简化形式后比较
    simplified_a = ''.join(traditional_to_simplified.get(c, c) for c in text_a)
    simplified_b = ''.join(traditional_to_simplified.get(c, c) for c in text_b)

    return simplified_a == simplified_b
