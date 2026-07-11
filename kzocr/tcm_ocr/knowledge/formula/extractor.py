"""
方剂组成提取模块

提供中医方剂（处方）的自动识别、解析、验证和存储功能。
支持：
- 方剂段落识别
- 方剂名提取与别名识别
- 上下文引用解析（同前/上方加味/去...）
- 药材明细提取（药名、剂量、单位、炮制）
- 引用链回溯与基础方复制
- 整方级验证（药材校验、剂量校验）

常量：
    REFERENCE_PATTERNS: 引用类型正则表达式
    FORMULA_MARKERS: 方剂标记关键词
    NON_HERB_MULTI_CHAR_WORDS: 非药材多字词集合
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB
from kzocr.tcm_ocr.database.sqlite.book_db import BookDB

logger = logging.getLogger(__name__)

# 引用类型正则表达式
REFERENCE_PATTERNS = {
    'same_as_above': r'同前|同上|如前|如上方',
    'add_to_above': r'上方加味|即前方加|于上方内加|加(?=\w+)',
    'subtract_from': r'去\w+|减\w+|除\w+',
    'cross_page_continued': r'方见上页|方见前页|续前',
}

# 方剂标记关键词（表示文本包含方剂组成）
FORMULA_MARKERS = ['组成', '方药', '处方', '用药', '方剂', '药味']

# 非药材多字词（提取药材时需要排除的干扰词）
NON_HERB_MULTI_CHAR_WORDS = {
    '组成', '方药', '处方', '主治', '功效', '用法', '用量',
    '水煎服', '口服', '外用', '研末', '冲服', '煎汤',
    '每日', '一日', '二次', '三次', '早晚', '饭前', '饭后',
    '温服', '冷服', '空腹', '睡前', '适量', '少许',
    '上药', '诸药', '各药', '此方', '本方', '前方', '上方',
    '加减', '不拘', '时候', '临证', '辨证', '随证',
    '毫升', '克每', '每次', '一剂', '一付', '一帖',
}

# 剂量单位正则
DOSAGE_UNIT_PATTERN = re.compile(
    r'(?P<value>\d+\.?\d*)\s*(?P<unit>g|克|钱|两|分|斤|毫升|ml|mg|μg|片|枚|个|条|只|滴|匙)',
    re.UNICODE,
)

# 中文数字剂量
CHINESE_DOSAGE_PATTERN = re.compile(
    r'(?P<num>[一二三四五六七八九十百千万两半]+)\s*(?P<unit>g|克|钱|两|分|斤|毫升|ml|片|枚|个|条|只)',
    re.UNICODE,
)

# 炮制方法关键词
PROCESSING_KEYWORDS = {
    '炙', '炒', '煅', '淬', '蒸', '煮', '炖', '煨',
    '制', '炮', '焙', '烘', '晒', '阴干', '酒洗',
    '姜汁', '醋', '盐', '蜜', '酒', '醋炙', '盐炙',
    '蜜炙', '酒炙', '姜汁炙', '炒黄', '炒焦', '炒炭',
    '研末', '打碎', '切片', '切段', '切丝',
}

# 常见中药名词典（用于匹配）
# 实际生产环境应从数据库加载
COMMON_HERB_NAMES = {
    '人参', '党参', '西洋参', '太子参', '黄芪', '白术', '山药', '甘草',
    '当归', '熟地黄', '生地黄', '白芍', '阿胶', '何首乌', '龙眼肉',
    '北沙参', '南沙参', '麦冬', '天冬', '石斛', '玉竹', '黄精',
    '鹿茸', '淫羊藿', '巴戟天', '仙茅', '杜仲', '续断', '肉苁蓉',
    '补骨脂', '菟丝子', '沙苑子', '锁阳', '韭菜子',
    '枸杞子', '墨旱莲', '女贞子', '桑椹', '黑芝麻',
    '龟甲', '鳖甲', '五味子', '乌梅', '诃子', '肉豆蔻',
    '山茱萸', '桑螵蛸', '海螵蛸', '莲子', '芡实', '覆盆子',
    '麻黄', '桂枝', '紫苏', '生姜', '香薷', '荆芥', '防风',
    '羌活', '白芷', '细辛', '苍耳子', '辛夷',
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
    '独活', '威灵仙', '川乌', '草乌', '木瓜', '蚕沙',
    '伸筋草', '寻骨风', '松节', '海风藤',
    '茯苓', '薏苡仁', '猪苓', '泽泻', '冬瓜皮', '玉米须',
    '车前子', '滑石', '木通', '通草', '瞿麦', '萹蓄',
    '茵陈', '金钱草', '虎杖', '垂盆草', '鸡骨草',
    '附子', '干姜', '肉桂', '吴茱萸', '小茴香', '丁香',
    '高良姜', '花椒', '胡椒', '荜茇', '荜澄茄',
    '川芎', '延胡索', '郁金', '姜黄', '乳香', '没药',
    '丹参', '红花', '桃仁', '益母草', '牛膝', '鸡血藤',
    '王不留行', '血竭', '土鳖虫', '自然铜', '骨碎补',
    '半夏', '天南星', '白附子', '白芥子', '皂荚', '旋覆花',
    '川贝母', '浙贝母', '瓜蒌', '竹茹', '竹沥', '天竺黄',
    '苦杏仁', '紫苏子', '百部', '紫菀', '款冬花', '马兜铃',
    '桑白皮', '葶苈子', '白果', '矮地茶', '洋金花',
    '朱砂', '磁石', '龙骨', '琥珀', '酸枣仁', '柏子仁', '远志',
    '合欢皮', '首乌藤', '灵芝', '缬草',
    '石决明', '牡蛎', '代赭石', '珍珠', '珍珠母', '钩藤',
    '天麻', '地龙', '全蝎', '蜈蚣', '僵蚕',
    '麝香', '冰片', '苏合香', '石菖蒲', '蟾酥',
    '常山', '瓜蒂', '胆矾',
    '麻黄根', '浮小麦', '糯稻根',
    '五味子', '乌梅', '五倍子', '罂粟壳', '诃子', '石榴皮',
    '肉豆蔻', '赤石脂', '禹余粮', '山茱萸', '覆盆子',
    '桑螵蛸', '海螵蛸', '金樱子', '莲子', '芡实',
    '刺猬皮', '椿皮', '鸡冠花',
    '硫黄', '雄黄', '蛇床子', '土荆皮', '白矾', '炉甘石',
    '硼砂', '斑蝥', '蟾酥', '马钱子', '儿茶', '大蒜',
    '穿山甲', '刺蒺藜', '罗布麻叶',
    '金钱草', '虎杖', '鸡骨草',
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

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
        for length in range(min(8, text_len - i), 1, -1):
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

    Args:
        name: 待验证的药材名

    Returns:
        True 如果是合法药材名
    """
    if not name or len(name) < 2:
        return False
    if not re.match(r'^[\u4e00-\u9fff]{2,8}$', name):
        return False
    return name in COMMON_HERB_NAMES


# ---------------------------------------------------------------------------
# 模块级判定函数（供聚合层等无 BookDB/TermKB 的场景直接复用，避免复制逻辑）
# ---------------------------------------------------------------------------

def is_formula_paragraph(text: str) -> bool:
    """判断文本是否为方剂段落（模块级）。

    与 FormulaExtractor.is_formula_paragraph 逻辑一致，移出类以便
    无实例依赖地调用（聚合层只需判边界，不必实例化需要 BookDB 的提取器）。
    """
    if not text:
        return False

    has_marker = any(marker in text for marker in FORMULA_MARKERS)
    herb_names = extract_herb_names(text)
    has_multiple_herbs = len(herb_names) >= 2
    has_dosage = bool(
        DOSAGE_UNIT_PATTERN.search(text) or CHINESE_DOSAGE_PATTERN.search(text)
    )
    has_reference = any(
        re.search(pat, text) for pat in REFERENCE_PATTERNS.values()
    )

    if has_marker and has_dosage:
        # 有方剂标记且有剂量即可判为方剂，避免依赖药材词典覆盖
        # （真实古籍大量方剂含未收录药材，仅认出 1 味也会漏切）
        return True
    if has_marker and has_multiple_herbs:
        return True
    if has_reference and has_multiple_herbs:
        return True
    if has_multiple_herbs and has_dosage:
        return True
    return False


def detect_reference_type(text: str) -> Optional[str]:
    """检测上下文引用类型（模块级）。

    与 FormulaExtractor._detect_reference_type 逻辑一致。
    返回 REFERENCE_PATTERNS 的键名，无引用返回 None。
    """
    if not text:
        return None
    for ref_type, pattern in REFERENCE_PATTERNS.items():
        if re.search(pattern, text):
            return ref_type
    return None


# ---------------------------------------------------------------------------
# FormulaExtractor 主类
# ---------------------------------------------------------------------------

class FormulaExtractor:
    """
    方剂组成提取器

    负责从段落文本中自动提取方剂名称、组成药材、剂量信息，
    处理上下文引用关系，并保存到数据库。

    Attributes:
        _db_book: BookDB 实例
        _term_kb: TermKB 实例
    """

    def __init__(self, db_book: BookDB, term_kb: Any) -> None:
        """
        初始化 FormulaExtractor

        Args:
            db_book: BookDB 实例（SQLite 书籍库）
            term_kb: TermKB 实例（术语知识库）
        """
        self._db_book = db_book
        self._term_kb = term_kb

    # ------------------------------------------------------------------
    # 主提取方法
    # ------------------------------------------------------------------

    def extract_from_paragraph(
        self,
        paragraph_id: int,
        corrected_text: str,
        page_id: int,
        prev_formulas: List[dict],
        book_id: str,
    ) -> dict:
        """
        从段落中提取方剂组成

        完整提取流程：
            1. 提取方剂名（通过 FORMULA_MARKERS 后的文本）
            2. 识别上下文引用类型（同前/上方加味/去...）
            3. 引用链解析：回溯到最近的独立方剂
            4. 提取药材明细（extract_herb_lines）
            5. 复制基础方组成（如为引用型）
            6. 保存 FormulaComposition + FormulaIngredient
            7. 返回 composition dict

        Args:
            paragraph_id: 段落 ID
            corrected_text: 校正后的段落文本
            page_id: 页面 ID
            prev_formulas: 前文已提取的方剂列表（用于引用解析）
            book_id: 书籍 ID

        Returns:
            方剂组成字典::

                {
                    'formula_id': int,
                    'formula_uuid': str,
                    'formula_name': str,
                    'context_reference_type': Optional[str],
                    'referenced_formula_id': Optional[int],
                    'ingredients': List[dict],
                    'is_cross_page': bool,
                    'extraction_status': str,
                }
        """
        if not corrected_text or not self.is_formula_paragraph(corrected_text):
            return {
                'formula_id': 0,
                'formula_uuid': '',
                'formula_name': '',
                'context_reference_type': None,
                'referenced_formula_id': None,
                'ingredients': [],
                'is_cross_page': False,
                'extraction_status': 'not_formula',
            }

        # 1. 提取方剂名
        formula_name = self.extract_formula_name(corrected_text)

        # 2. 识别上下文引用类型
        ref_type = self._detect_reference_type(corrected_text)

        # 3. 提取别名
        variants = self.extract_formula_variants(corrected_text)

        # 生成 UUID
        formula_uuid = str(uuid.uuid4())

        # 获取下一个方剂顺序号
        formula_sequence = self.get_next_formula_sequence(book_id)

        # 解析引用链
        referenced_formula_id: Optional[int] = None
        root_formula_id: Optional[int] = None
        base_ingredients: List[dict] = []

        if ref_type and prev_formulas:
            # 回溯到最近的独立方剂
            for prev in reversed(prev_formulas):
                if prev.get('context_reference_type') is None:
                    referenced_formula_id = prev.get('formula_id')
                    root_formula_id = prev.get('root_formula_id') or prev.get('formula_id')
                    # 5. 复制基础方组成
                    if referenced_formula_id:
                        base_ingredients = self.copy_base_ingredients(referenced_formula_id)
                    break

        # 4. 提取药材明细
        herb_lines = self.extract_herb_lines(corrected_text)

        # 合并基础方 + 当前提取的药材
        all_ingredients = list(base_ingredients)

        # 解析加味/减味
        added_herbs: List[dict] = []
        removed_herbs: Set[str] = set()

        if ref_type in ('add_to_above', 'subtract_from'):
            if ref_type == 'add_to_above':
                added_herbs = self.extract_added_herbs(corrected_text)
            elif ref_type == 'subtract_from':
                removed_herbs = self._extract_removed_herbs(corrected_text)

        # 从 herb_lines 构建 ingredients
        position = 0
        for line_info in herb_lines:
            herb_name = line_info.get('herb_name', '')
            if not herb_name:
                continue

            # 如果是减去的药材，跳过
            if herb_name in removed_herbs:
                continue

            ingredient = {
                'herb_name': herb_name,
                'herb_name_standard': herb_name,
                'dosage_value': line_info.get('dosage_value'),
                'dosage_value_numeric': line_info.get('dosage_value_numeric'),
                'dosage_unit': line_info.get('dosage_unit'),
                'processing_method': line_info.get('processing_method'),
                'position_in_paragraph': position,
                'is_added': herb_name in [a.get('herb_name') for a in added_herbs],
                'is_copied_from_base': False,
                'validation_status': 'pending',
            }
            all_ingredients.append(ingredient)
            position += 1

        # 添加加味药材（如果不在列表中）
        existing_names = {ing['herb_name'] for ing in all_ingredients}
        for added in added_herbs:
            if added.get('herb_name') and added['herb_name'] not in existing_names:
                added_ing = {
                    'herb_name': added['herb_name'],
                    'herb_name_standard': added['herb_name'],
                    'dosage_value': added.get('dosage_value'),
                    'dosage_value_numeric': added.get('dosage_value_numeric'),
                    'dosage_unit': added.get('dosage_unit'),
                    'processing_method': added.get('processing_method'),
                    'position_in_paragraph': position,
                    'is_added': True,
                    'is_copied_from_base': False,
                    'validation_status': 'pending',
                }
                all_ingredients.append(added_ing)
                position += 1

        # 6. 保存到数据库
        try:
            # 保存 FormulaComposition
            composition_data = {
                'book_registry_id': int(book_id) if isinstance(book_id, str) and book_id.isdigit() else book_id,
                'formula_uuid': formula_uuid,
                'formula_name': formula_name or '未命名方剂',
                'formula_name_variants': variants,
                'page_id': page_id,
                'paragraph_id': paragraph_id,
                'formula_sequence': formula_sequence,
                'context_reference_type': ref_type,
                'referenced_formula_id': referenced_formula_id,
                'root_formula_id': root_formula_id,
                'context_description': corrected_text[:500] if corrected_text else None,
                'extraction_status': 'extracted',
                'cross_page_group_id': None,
            }

            formula_id = self._db_book.create_formula_composition(**composition_data)

            # 保存 FormulaIngredient
            saved_ingredients: List[dict] = []
            for ing in all_ingredients:
                ing_data = {
                    'formula_composition_id': formula_id,
                    'herb_name': ing['herb_name'],
                    'herb_name_standard': ing.get('herb_name_standard'),
                    'dosage_value': ing.get('dosage_value'),
                    'dosage_value_numeric': ing.get('dosage_value_numeric'),
                    'dosage_unit': ing.get('dosage_unit'),
                    'processing_method': ing.get('processing_method'),
                    'position_in_paragraph': ing.get('position_in_paragraph'),
                    'is_added': 1 if ing.get('is_added') else 0,
                    'is_copied_from_base': 1 if ing.get('is_copied_from_base') else 0,
                    'base_formula_id': referenced_formula_id if ing.get('is_copied_from_base') else None,
                    'validation_status': ing.get('validation_status', 'pending'),
                }
                ingredient_id = self._db_book.create_formula_ingredient(**ing_data)
                ing['id'] = ingredient_id
                saved_ingredients.append(ing)

            # 标记段落为方剂段落
            self._db_book.mark_formula_paragraph(paragraph_id, True)

            result = {
                'formula_id': formula_id,
                'formula_uuid': formula_uuid,
                'formula_name': formula_name or '未命名方剂',
                'context_reference_type': ref_type,
                'referenced_formula_id': referenced_formula_id,
                'root_formula_id': root_formula_id,
                'ingredients': saved_ingredients,
                'is_cross_page': ref_type == 'cross_page_continued',
                'extraction_status': 'extracted',
                'formula_sequence': formula_sequence,
            }

            logger.info(
                "Extracted formula '%s' (id=%d) with %d ingredients from paragraph %d",
                formula_name, formula_id, len(saved_ingredients), paragraph_id,
            )

            return result

        except Exception as e:
            logger.error(
                "Failed to save formula from paragraph %d: %s",
                paragraph_id, e,
            )
            return {
                'formula_id': 0,
                'formula_uuid': formula_uuid,
                'formula_name': formula_name or '',
                'context_reference_type': ref_type,
                'referenced_formula_id': referenced_formula_id,
                'ingredients': all_ingredients,
                'is_cross_page': False,
                'extraction_status': f'error: {str(e)}',
            }

    # ------------------------------------------------------------------
    # 验证
    # ------------------------------------------------------------------

    def validate_formula_ingredients(
        self,
        formula_id: int,
        ingredients: List[dict],
        term_kb: Any,
    ) -> Tuple[List[dict], List[dict]]:
        """
        验证方剂组成

        对每味药材进行 HerbOCRPattern 校验，并进行整方级剂量校验。

        Args:
            formula_id: 方剂 ID
            ingredients: 药材列表
            term_kb: TermKB 实例

        Returns:
            (ingredients_with_alerts, alerts)

            ingredients_with_alerts 为添加了 alert 字段的 ingredient 列表::

                [
                    {
                        ...original fields...
                        'alerts': List[str],  # 该药材的警告列表
                    },
                    ...
                ]

            alerts 为整方级警告列表::

                [
                    {
                        'level': str,      # 'error' | 'warning'
                        'message': str,
                        'ingredient_idx': Optional[int],
                    },
                    ...
                ]
        """
        ingredients_with_alerts: List[dict] = []
        alerts: List[dict] = []
        total_dosage = 0.0
        has_toxic_herb = False

        for idx, ing in enumerate(ingredients):
            herb_name = ing.get('herb_name', '')
            ing_with_alert = dict(ing)
            ing_alerts: List[str] = []

            if not herb_name:
                ing_alerts.append('药材名称为空')
                alerts.append({
                    'level': 'error',
                    'message': f'第 {idx + 1} 味药材名称为空',
                    'ingredient_idx': idx,
                })
            else:
                # 1. 校验药材名合法性
                if not is_valid_herb_name(herb_name):
                    ing_alerts.append(f'未知药材: {herb_name}')
                    alerts.append({
                        'level': 'warning',
                        'message': f'未知药材: {herb_name}',
                        'ingredient_idx': idx,
                    })

                # 2. 通过 TermKB 查询 OCR 错误模式
                if term_kb and hasattr(term_kb, 'match_error_pattern_in_context'):
                    match = term_kb.match_error_pattern_in_context(herb_name, 0)
                    if match:
                        ing_alerts.append(
                            f"可能的 OCR 错误: {match.get('error_pattern')} -> "
                            f"{match.get('corrected_text')} "
                            f"(confidence: {match.get('confidence', 0):.2f})"
                        )

            # 3. 剂量校验
            dosage_value = ing.get('dosage_value_numeric')
            dosage_unit = ing.get('dosage_unit', '')
            if dosage_value is not None:
                try:
                    dose = float(dosage_value)
                    if dose <= 0:
                        ing_alerts.append(f'剂量无效: {dose}')
                        alerts.append({
                            'level': 'error',
                            'message': f'{herb_name} 剂量无效: {dose}',
                            'ingredient_idx': idx,
                        })
                    elif dose > 1000:
                        ing_alerts.append(f'剂量过大: {dose}{dosage_unit}')
                        alerts.append({
                            'level': 'warning',
                            'message': f'{herb_name} 剂量过大: {dose}{dosage_unit}',
                            'ingredient_idx': idx,
                        })
                    else:
                        # 累加总剂量（统一转换为克）
                        total_dosage += self._convert_to_grams(dose, dosage_unit)
                except (ValueError, TypeError):
                    ing_alerts.append(f'剂量格式错误: {dosage_value}')

            # 4. 毒性检查
            from kzocr.tcm_ocr.knowledge.herb_pattern.auto_discover import HERB_TOXICITY_MAP
            toxicity = HERB_TOXICITY_MAP.get(herb_name)
            if toxicity in ('high', 'severe'):
                has_toxic_herb = True
                ing_alerts.append(f'⚠️ 高毒性药材: {herb_name}')
                alerts.append({
                    'level': 'error',
                    'message': f'方剂含高毒性药材: {herb_name}',
                    'ingredient_idx': idx,
                })
            elif toxicity == 'moderate':
                ing_alerts.append(f'中等毒性药材: {herb_name}')

            ing_with_alert['alerts'] = ing_alerts
            ingredients_with_alerts.append(ing_with_alert)

        # 整方级剂量校验
        if total_dosage > 500:
            alerts.append({
                'level': 'warning',
                'message': f'方剂总剂量过大: {total_dosage:.1f}g',
                'ingredient_idx': None,
            })

        if has_toxic_herb and total_dosage > 100:
            alerts.append({
                'level': 'error',
                'message': '含毒性药材且总剂量偏大，需人工审核',
                'ingredient_idx': None,
            })

        return ingredients_with_alerts, alerts

    # ------------------------------------------------------------------
    # 识别方法
    # ------------------------------------------------------------------

    def is_formula_paragraph(self, text: str) -> bool:
        """
        检查是否为方剂段落（委托模块级 is_formula_paragraph）。

        Args:
            text: 段落文本

        Returns:
            True 如果是方剂段落
        """
        return is_formula_paragraph(text)

    def extract_formula_name(self, text: str) -> Optional[str]:
        """
        提取方剂名

        从文本中提取方剂名称。通常位于 FORMULA_MARKERS 之前，
        或者作为段落的开头部分。

        Args:
            text: 段落文本

        Returns:
            方剂名称，如果无法提取返回 None
        """
        if not text:
            return None

        # 策略 1: 方剂标记前的文本
        for marker in FORMULA_MARKERS:
            if marker in text:
                parts = text.split(marker, 1)
                if parts[0].strip():
                    name = parts[0].strip().rstrip('：:）)\n ')
                    if len(name) >= 2 and len(name) <= 20:
                        # 清理常见的非名称字符
                        name = re.sub(r'^[\d\.\s]+', '', name)
                        if name:
                            return name

        # 策略 2: 段落开头的第一行/第一句
        first_line = text.strip().split('\n')[0].strip()
        if first_line and len(first_line) <= 20:
            # 排除纯数字开头
            if not re.match(r'^\d', first_line):
                # 去掉末尾标点
                name = first_line.rstrip('：:）)（(,， ')
                if len(name) >= 2:
                    return name

        # 策略 3: 在 "方" 字结尾的短语
        match = re.search(r'([\u4e00-\u9fff]{2,10}方)', text)
        if match:
            return match.group(1)

        return None

    def extract_formula_variants(self, text: str) -> List[str]:
        """
        提取方剂别名

        识别文本中 "又名 XXX"、"亦称 XXX" 等别名表达。

        Args:
            text: 段落文本

        Returns:
            别名列表
        """
        variants: List[str] = []
        if not text:
            return variants

        # 又名 / 亦称 / 亦称 / 一曰 / 古名
        patterns = [
            r'又名[「『""（\\(]?(.*?)[」』""）\\)]?',
            r'亦称[「『""（\\(]?(.*?)[」』""）\\)]?',
            r'亦称[「『""（\\(]?(.*?)[」』""）\\)]?',
            r'一曰[「『""（\\(]?(.*?)[」』""）\\)]?',
            r'古名[「『""（\\(]?(.*?)[」』""）\\)]?',
        ]

        for pat in patterns:
            for match in re.finditer(pat, text):
                variant = match.group(1).strip()
                if variant and len(variant) >= 2:
                    variants.append(variant)

        return variants

    def extract_herb_lines(self, text: str) -> List[dict]:
        """
        逐行提取药材名、剂量、单位、炮制

        解析文本中的每一行，提取：
        - 药材名
        - 剂量值（数值）
        - 剂量单位
        - 炮制方法

        Args:
            text: 段落文本

        Returns:
            每行提取结果列表::

                [
                    {
                        'herb_name': str,
                        'dosage_value': str,        # 原始剂量文本
                        'dosage_value_numeric': float,  # 数值
                        'dosage_unit': str,
                        'processing_method': str,
                        'original_text': str,       # 原始行文本
                    },
                    ...
                ]
        """
        lines = text.split('\n') if text else []
        results: List[dict] = []

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 排除干扰行
            if self._is_noise_line(line):
                continue

            result = self._parse_single_herb_line(line)
            if result and result.get('herb_name'):
                results.append(result)

        return results

    def _parse_single_herb_line(self, line: str) -> Optional[dict]:
        """
        解析单行文本中的药材信息

        Args:
            line: 单行文本

        Returns:
            解析结果字典，未解析到返回 None
        """
        result: Dict[str, Any] = {
            'herb_name': '',
            'dosage_value': '',
            'dosage_value_numeric': None,
            'dosage_unit': '',
            'processing_method': '',
            'original_text': line,
        }

        working_line = line

        # 1. 提取炮制方法
        for proc_kw in sorted(PROCESSING_KEYWORDS, key=len, reverse=True):
            if proc_kw in working_line:
                result['processing_method'] = proc_kw
                working_line = working_line.replace(proc_kw, ' ', 1)
                break

        # 2. 提取剂量（阿拉伯数字）
        m = DOSAGE_UNIT_PATTERN.search(working_line)
        if m:
            result['dosage_value'] = m.group(0)
            result['dosage_value_numeric'] = float(m.group('value'))
            result['dosage_unit'] = m.group('unit')
            # 移除剂量部分
            working_line = working_line[:m.start()] + working_line[m.end():]

        # 3. 提取剂量（中文数字）
        if not result['dosage_value']:
            m2 = CHINESE_DOSAGE_PATTERN.search(working_line)
            if m2:
                result['dosage_value'] = m2.group(0)
                result['dosage_unit'] = m2.group('unit')
                # 转换中文数字为阿拉伯数字
                cn_num = m2.group('num')
                result['dosage_value_numeric'] = self._chinese_to_number(cn_num)
                working_line = working_line[:m2.start()] + working_line[m2.end():]

        # 4. 提取药材名（从剩余文本中）
        herbs_in_line = extract_herb_names(working_line)
        if herbs_in_line:
            # 取第一个匹配到的药材名
            result['herb_name'] = herbs_in_line[0]
        else:
            # 如果没有匹配到已知药材，尝试提取 2-8 字的汉字序列
            match = re.search(r'[\u4e00-\u9fff]{2,8}', working_line)
            if match:
                candidate = match.group(0)
                if candidate not in NON_HERB_MULTI_CHAR_WORDS:
                    result['herb_name'] = candidate

        if not result['herb_name']:
            return None

        return result

    def _is_noise_line(self, line: str) -> bool:
        """
        判断是否为干扰行（不含药材信息的行）

        Args:
            line: 单行文本

        Returns:
            True 如果是干扰行
        """
        # 纯标点/数字/空行
        if re.match(r'^[\s\d.,，。、；：:!！?？""''（）()\x5b\x5d]*$', line):
            return True

        # 完全由 NON_HERB_MULTI_CHAR_WORDS 中的词组成
        all_noise = True
        remaining = line
        for word in sorted(NON_HERB_MULTI_CHAR_WORDS, key=len, reverse=True):
            remaining = remaining.replace(word, '')
        remaining = re.sub(r'[\s\d.,，。、；：:!！?？""''（）()\x5b\x5d]', '', remaining)
        if remaining and len(re.sub(r'[\u4e00-\u9fff]', '', remaining)) < len(remaining):
            # 还有剩余汉字，可能包含药材
            all_noise = False

        # 检查是否包含任何已知药材名
        herbs = extract_herb_names(line)
        if herbs:
            return False

        return all_noise

    # ------------------------------------------------------------------
    # 引用相关
    # ------------------------------------------------------------------

    def _detect_reference_type(self, text: str) -> Optional[str]:
        """
        检测上下文引用类型（委托模块级 detect_reference_type）。

        Args:
            text: 段落文本

        Returns:
            引用类型键名，无引用返回 None
        """
        return detect_reference_type(text)

    def copy_base_ingredients(self, base_formula_id: int) -> List[dict]:
        """
        复制基础方的组成药材

        从数据库中读取指定方剂的所有成分，复制为新的 ingredient 字典列表。
        标记 is_copied_from_base=True。

        Args:
            base_formula_id: 基础方 ID

        Returns:
            复制的成分列表
        """
        try:
            base_ingredients = self._db_book.get_formula_ingredients(base_formula_id)
            copied: List[dict] = []
            for ing in base_ingredients:
                copied_ing = {
                    'herb_name': ing.get('herb_name', ''),
                    'herb_name_standard': ing.get('herb_name_standard'),
                    'dosage_value': ing.get('dosage_value'),
                    'dosage_value_numeric': ing.get('dosage_value_numeric'),
                    'dosage_unit': ing.get('dosage_unit'),
                    'processing_method': ing.get('processing_method'),
                    'is_added': False,
                    'is_copied_from_base': True,
                    'base_formula_id': base_formula_id,
                    'validation_status': 'pending',
                }
                copied.append(copied_ing)
            return copied
        except Exception as e:
            logger.error("Failed to copy base ingredients from formula %d: %s", base_formula_id, e)
            return []

    def extract_added_herbs(self, text: str) -> List[dict]:
        """
        提取加味药材

        从 "加 XXX"、"加味 XXX" 等表达中提取加味的药材。

        Args:
            text: 段落文本

        Returns:
            加味药材列表::

                [
                    {
                        'herb_name': str,
                        'dosage_value': Optional[str],
                        'dosage_value_numeric': Optional[float],
                        'dosage_unit': Optional[str],
                        'processing_method': Optional[str],
                    },
                    ...
                ]
        """
        added: List[dict] = []
        if not text:
            return added

        # 匹配 "加 XXX" 模式
        add_patterns = [
            r'加([\u4e00-\u9fff]{2,8}(?:\d+\.?\d*\s*[g克钱两])?)',
            r'加味([\u4e00-\u9fff]{2,8}(?:\d+\.?\d*\s*[g克钱两])?)',
            r'再加([\u4e00-\u9fff]{2,8}(?:\d+\.?\d*\s*[g克钱两])?)',
        ]

        for pat in add_patterns:
            for match in re.finditer(pat, text):
                content = match.group(1).strip()
                if not content:
                    continue

                # 解析药材名和剂量
                herb_name = ''
                dosage_value = None
                dosage_unit = ''

                # 提取剂量
                dm = DOSAGE_UNIT_PATTERN.search(content)
                if dm:
                    dosage_value = float(dm.group('value'))
                    dosage_unit = dm.group('unit')
                    herb_part = content[:dm.start()].strip()
                else:
                    herb_part = content

                # 提取药材名
                herbs = extract_herb_names(herb_part)
                if herbs:
                    herb_name = herbs[0]
                else:
                    # 尝试直接取前几个汉字
                    m = re.match(r'([\u4e00-\u9fff]{2,8})', herb_part)
                    if m:
                        herb_name = m.group(1)

                if herb_name:
                    added.append({
                        'herb_name': herb_name,
                        'dosage_value': str(dosage_value) if dosage_value else None,
                        'dosage_value_numeric': dosage_value,
                        'dosage_unit': dosage_unit,
                        'processing_method': '',
                    })

        return added

    def _extract_removed_herbs(self, text: str) -> set:
        """
        提取减去的药材名

        从 "去 XXX"、"减 XXX" 等表达中提取。

        Args:
            text: 段落文本

        Returns:
            减去的药材名集合
        """
        removed: set = set()
        if not text:
            return removed

        remove_patterns = [
            r'去([\u4e00-\u9fff]{2,8})',
            r'减([\u4e00-\u9fff]{2,8})',
            r'除([\u4e00-\u9fff]{2,8})',
        ]

        for pat in remove_patterns:
            for match in re.finditer(pat, text):
                herb_name = match.group(1).strip()
                if herb_name and herb_name not in NON_HERB_MULTI_CHAR_WORDS:
                    removed.add(herb_name)

        return removed

    def validate_formula_reference(
        self,
        formula: dict,
        prev_formula: dict,
    ) -> Optional[dict]:
        """
        引用一致性校验

        验证当前方剂的引用关系是否与基础方一致。

        Args:
            formula: 当前方剂
            prev_formula: 前序方剂（基础方）

        Returns:
            校验结果字典，一致返回 None::

                {
                    'valid': bool,
                    'issues': List[str],
                    'formula_id': int,
                    'referenced_formula_id': int,
                }
        """
        issues: List[str] = []

        ref_type = formula.get('context_reference_type')
        ref_id = formula.get('referenced_formula_id')
        prev_id = prev_formula.get('formula_id')

        if ref_id != prev_id:
            issues.append(f'引用 ID 不匹配: {ref_id} vs {prev_id}')

        # 校验加味合理性
        if ref_type == 'add_to_above':
            current_herbs = {ing.get('herb_name') for ing in formula.get('ingredients', [])}
            base_herbs = {ing.get('herb_name') for ing in prev_formula.get('ingredients', [])}

            # 加味方应包含基础方的所有药材
            missing = base_herbs - current_herbs
            if missing:
                issues.append(f'加味方缺少基础方药材: {missing}')

        if issues:
            return {
                'valid': False,
                'issues': issues,
                'formula_id': formula.get('formula_id'),
                'referenced_formula_id': ref_id,
            }

        return None

    def get_next_formula_sequence(self, book_id: str) -> int:
        """
        获取下一个方剂顺序号

        查询当前书籍已提取的方剂数量，返回下一个顺序号。

        Args:
            book_id: 书籍 ID

        Returns:
            下一个顺序号（从 1 开始）
        """
        try:
            with self._db_book.get_cursor() as cursor:
                cursor.execute(
                    "SELECT MAX(formula_sequence) as max_seq FROM FormulaComposition",
                )
                result = cursor.fetchone()
                max_seq = result['max_seq'] if result and result['max_seq'] else 0
                return (max_seq or 0) + 1
        except Exception as e:
            logger.error("Failed to get next formula sequence: %s", e)
            return 1

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _chinese_to_number(cn: str) -> Optional[float]:
        """
        中文数字转阿拉伯数字

        支持：零一二三四五六七八九十百千万两半

        Args:
            cn: 中文数字字符串

        Returns:
            阿拉伯数字，转换失败返回 None
        """
        if not cn:
            return None

        # 直接映射
        direct_map = {
            '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
            '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
            '零': 0, '半': 0.5, '两': 2,
        }

        if len(cn) == 1 and cn in direct_map:
            return float(direct_map[cn])

        # 处理组合数字
        result = 0
        temp = 0
        for char in cn:
            if char in direct_map:
                num = direct_map[char]
                if num >= 10:
                    if temp == 0:
                        temp = 1
                    result += temp * num
                    temp = 0
                else:
                    temp = temp * 10 + num if temp > 0 else num
            elif char == '百':
                if temp == 0:
                    temp = 1
                result += temp * 100
                temp = 0
            elif char == '千':
                if temp == 0:
                    temp = 1
                result += temp * 1000
                temp = 0
            elif char == '万':
                if temp == 0:
                    temp = 1
                result += temp * 10000
                temp = 0

        result += temp
        return float(result) if result > 0 else None

    @staticmethod
    def _convert_to_grams(value: float, unit: str) -> float:
        """
        将剂量统一转换为克

        Args:
            value: 剂量值
            unit: 原始单位

        Returns:
            克数
        """
        unit_lower = unit.lower().strip()
        conversion = {
            'g': 1.0,
            '克': 1.0,
            '钱': 3.0,       # 1 钱 ≈ 3 克
            '两': 30.0,      # 1 两 ≈ 30 克（汉代）
            '分': 0.3,       # 1 分 ≈ 0.3 克
            '斤': 500.0,     # 1 斤 = 500 克
            'mg': 0.001,
            'μg': 0.000001,
            '毫升': 1.0,      # 水当量
            'ml': 1.0,
        }
        factor = conversion.get(unit_lower, 1.0)
        return value * factor
