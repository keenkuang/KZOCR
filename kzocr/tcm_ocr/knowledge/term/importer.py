"""
中医术语辞典导入器 - TermImporter 类

负责从11个辞典文件解析、分类、导入术语到数据库。
支持中药名辞典的多别名解析、自动分类引擎、有毒药材安全标记、
经络名变体生成、方剂后缀识别等功能。

辞典文件格式支持：
- 中药名辞典：每行以正名开头，逗号分隔别名（如"白术, 于术, 冬术"）
- 单术语辞典：每行一个术语（如"气虚证"）
- 逗号分隔辞典：每行以逗号分隔多个相关术语
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Set

from kzocr.tcm_ocr.knowledge.term.auto_classifier import AutoClassifier

logger = logging.getLogger(__name__)


class TermImporter:
    """中医术语辞典导入器

    从11个辞典文件解析、分类、导入术语到 PostgreSQL 数据库。
    支持多种辞典格式，自动分类混杂辞典，标记有毒药材安全级别。

    Attributes:
        SUBLIB_CONFIG: 11个辞典文件的配置映射
        TOXIC_HERBS_CRITICAL: 有毒药材名单（critical 级别）
        PROCESSING_PREFIXES: 炮制方法前缀列表
        FORMULA_SUFFIXES: 方剂后缀集合
        FORMULA_EXCEPTIONS: 方剂后缀排除集合
        MERIDIAN_VARIANTS: 53个经络名变体列表
    """

    # 11个辞典文件的映射配置
    SUBLIB_CONFIG: Dict[str, Dict[str, Any]] = {
        'HERB_DICT': {
            'file': '中药名辞典-DS.md',
            'base_category': 'herb',
        },
        'SYMPTOM_DICT': {
            'file': '疾病症状辞典.md',
            'base_category': 'symptom',
        },
        'SYNDROME_DICT': {
            'file': '中医证型辞典.md',
            'base_category': 'tcm_syndrome',
        },
        'TCM_TERM_DICT': {
            'file': '中医名词辞典DS.md',
            'base_category': 'tcm_term',
        },
        'WM_DISEASE_DICT': {
            'file': '西医名词辞典-DS.md',
            'base_category': 'wm_disease',
        },
        'TCM_DISEASE_DICT': {
            'file': '中医病名辞典-DS.md',
            'base_category': 'tcm_disease',
        },
        'DIAGNOSIS_DICT': {
            'file': '中医诊断学名词辞典.md',
            'base_category': 'diagnosis_method',
        },
        'INTERNAL_DICT': {
            'file': '中医内科名词辞典.md',
            'base_category': None,  # 需规则分类
        },
        'BASIC_DICT': {
            'file': '中医基础名词辞典.md',
            'base_category': 'tcm_term',
        },
        'GYNECOLOGY_DICT': {
            'file': '中医妇科名词辞典.md',
            'base_category': None,  # 需规则分类
        },
        'PEDIATRICS_DICT': {
            'file': '中医儿科名词辞典.md',
            'base_category': None,  # 需规则分类
        },
    }

    # 有毒药材名单（critical 安全级别）
    TOXIC_HERBS_CRITICAL: Set[str] = {
        '附子', '川乌', '草乌', '马钱子', '斑蝥', '蟾酥',
        '朱砂', '雄黄', '轻粉', '红粉', '白降丹',
        '千金子', '甘遂', '大戟', '芫花', '商陆',
        '牵牛子', '巴豆', '巴豆霜', '丽江山慈菇',
        '天南星', '半夏', '白附子', '皂荚',
        '常山', '瓜蒂', '胆矾', '硫磺',
        '水银', '砒石', '砒霜', '红娘虫', '青娘虫',
        '蜈蚣', '全蝎', '蕲蛇', '金钱白花蛇',
        '两头尖', '关木通', '广防己', '青木香',
        '天仙藤', '寻骨风', '细辛', '雷公藤',
    }

    # 炮制方法前缀（按长度降序，避免"炒"匹配"麸炒"）
    PROCESSING_PREFIXES: List[str] = [
        '麸炒', '麸煨', '土炒', '蜜炙', '姜炙', '盐炙', '酒炙', '醋炙',
        '炒', '炙', '煅', '醋', '盐', '蜜', '酒', '炭', '焦',
        '蒸', '煮', '炖', '煨', '焙', '制', '生', '鲜',
    ]

    # 17种方剂后缀
    FORMULA_SUFFIXES: Set[str] = {
        '汤', '散', '丸', '丹', '膏', '饮', '饮子', '方', '煎',
        '露', '汁', '胶', '洗', '锭', '栓', '线', '油',
    }

    # 方剂后缀排除列表
    FORMULA_EXCEPTIONS: Set[str] = {
        '方法', '处方', '药方', '汤剂', '丸剂', '散剂', '膏剂', '丹剂',
        '饮片', '汤药', '丸药', '膏药', '药膏', '药散', '药汤',
        '方便', '方向', '方位', '方才', '煎药', '煎熬', '煎蛋', '煎饼',
        '露水', '露珠', '露天', '果汁', '菜汁', '肉汁', '墨汁',
        '阿胶', '牛胶', '鱼胶', '发胶', '洗手', '洗脸', '洗澡', '洗衣',
        '油画', '油漆', '油条', '油灯',
    }

    # 53个经络名变体（需补充到术语库）
    MERIDIAN_VARIANTS: List[str] = [
        # 手三阴经 (9)
        '手太阴经', '太阴经', '肺经',
        '手少阴经', '少阴经', '心经',
        '手厥阴经', '厥阴经', '心包经',
        # 手三阳经 (9)
        '手阳明经', '阳明经', '大肠经',
        '手太阳经', '太阳经', '小肠经',
        '手少阳经', '少阳经', '三焦经',
        # 足三阳经 (6)
        '足阳明经', '胃经',
        '足太阳经', '膀胱经',
        '足少阳经', '胆经',
        # 足三阴经 (6)
        '足太阴经', '脾经',
        '足少阴经', '肾经',
        '足厥阴经', '肝经',
        # 奇经八脉标准名 (8)
        '任脉', '督脉', '冲脉', '带脉',
        '阴跷脉', '阳跷脉', '阴维脉', '阳维脉',
        # 奇经八脉别名 (3)
        '阴脉之海', '阳脉之海', '血海',
        # 集合名 (2)
        '十二经脉', '奇经八脉',
        # 手足组合经 (6)
        '手足太阴经', '手足少阴经', '手足厥阴经',
        '手足阳明经', '手足太阳经', '手足少阳经',
        # 三阴三阳总称 (4)
        '手三阴经', '手三阳经', '足三阴经', '足三阳经',
    ]

    # 经络名变体到标准名的映射
    MERIDIAN_VARIANT_TO_STANDARD: Dict[str, str] = {
        # 手三阴经
        '手太阴经': '手太阴肺经',
        '太阴经': '手太阴肺经',
        '肺经': '手太阴肺经',
        '手少阴经': '手少阴心经',
        '少阴经': '手少阴心经',
        '心经': '手少阴心经',
        '手厥阴经': '手厥阴心包经',
        '厥阴经': '手厥阴心包经',
        '心包经': '手厥阴心包经',
        # 手三阳经
        '手阳明经': '手阳明大肠经',
        '阳明经': '手阳明大肠经',
        '大肠经': '手阳明大肠经',
        '手太阳经': '手太阳小肠经',
        '太阳经': '手太阳小肠经',
        '小肠经': '手太阳小肠经',
        '手少阳经': '手少阳三焦经',
        '少阳经': '手少阳三焦经',
        '三焦经': '手少阳三焦经',
        # 足三阳经
        '足阳明经': '足阳明胃经',
        '胃经': '足阳明胃经',
        '足太阳经': '足太阳膀胱经',
        '膀胱经': '足太阳膀胱经',
        '足少阳经': '足少阳胆经',
        '胆经': '足少阳胆经',
        # 足三阴经
        '足太阴经': '足太阴脾经',
        '脾经': '足太阴脾经',
        '足少阴经': '足少阴肾经',
        '肾经': '足少阴肾经',
        '足厥阴经': '足厥阴肝经',
        '肝经': '足厥阴肝经',
        # 奇经八脉标准名
        '任脉': '任脉',
        '督脉': '督脉',
        '冲脉': '冲脉',
        '带脉': '带脉',
        '阴跷脉': '阴跷脉',
        '阳跷脉': '阳跷脉',
        '阴维脉': '阴维脉',
        '阳维脉': '阳维脉',
        # 奇经八脉别名
        '阴脉之海': '任脉',
        '阳脉之海': '督脉',
        '血海': '冲脉',
        # 集合名
        '十二经脉': '十二经脉',
        '奇经八脉': '奇经八脉',
        # 手足组合经
        '手足太阴经': '手太阴肺经、足太阴脾经',
        '手足少阴经': '手少阴心经、足少阴肾经',
        '手足厥阴经': '手厥阴心包经、足厥阴肝经',
        '手足阳明经': '手阳明大肠经、足阳明胃经',
        '手足太阳经': '手太阳小肠经、足太阳膀胱经',
        '手足少阳经': '手少阳三焦经、足少阳胆经',
        # 三阴三阳总称
        '手三阴经': '手太阴肺经、手少阴心经、手厥阴心包经',
        '手三阳经': '手阳明大肠经、手太阳小肠经、手少阳三焦经',
        '足三阴经': '足太阴脾经、足少阴肾经、足厥阴肝经',
        '足三阳经': '足阳明胃经、足太阳膀胱经、足少阳胆经',
    }

    def __init__(self, runtime_db: Any, dict_dir: str = '/mnt/agents/upload/') -> None:
        """初始化 TermImporter

        Args:
            runtime_db: RuntimeDB 实例，用于数据库操作
            dict_dir: 辞典文件所在目录路径
        """
        self._runtime_db = runtime_db
        self._dict_dir = dict_dir
        self._classifier = AutoClassifier()
        self._sublib_ids: Dict[str, int] = {}
        self._herb_aliases: List[Dict[str, str]] = []
        self._primary_terms: List[str] = []
        self._meridian_variants_inserted: List[Dict[str, str]] = []

        # 确保子库存在
        self._ensure_sublibs()

    def _ensure_sublibs(self) -> None:
        """确保所有子库记录在数据库中存在

        检查 SUBLIB_CONFIG 中定义的每个子库，如果不存在则创建。
        将子库名称到 ID 的映射缓存到 _sublib_ids 中。
        """
        for sublib_id, config in self.SUBLIB_CONFIG.items():
            db_id = self._get_or_create_sublib(sublib_id, config.get('base_category', ''))
            self._sublib_ids[sublib_id] = db_id

    def _get_or_create_sublib(self, name: str, description: str = '') -> int:
        """获取或创建子库记录

        Args:
            name: 子库名称
            description: 子库描述

        Returns:
            子库 ID
        """
        with self._runtime_db.get_cursor() as cursor:
            # 先查询是否存在
            cursor.execute("SELECT id FROM Sublib WHERE name = %s", (name,))
            result = cursor.fetchone()
            if result:
                return result['id']

            # 创建新子库
            cursor.execute(
                "INSERT INTO Sublib (name, description) VALUES (%s, %s) RETURNING id",
                (name, description),
            )
            new_id = cursor.fetchone()['id']
            logger.info("Created Sublib '%s' with id=%d", name, new_id)
            return new_id

    def import_all(self) -> dict:
        """导入所有 11 个辞典文件

        按依赖顺序导入所有辞典：
        1. 先导入有明确 base_category 的辞典
        2. 再导入需要规则分类的混杂辞典
        3. 最后生成经络名变体

        Returns:
            导入结果统计字典，格式为::

                {
                    'sublib': {sublib_id: {'inserted': int, 'duplicates': int}},
                    'total_inserted': int,
                    'total_duplicates': int,
                    'herb_aliases': [{'alias': str, 'primary': str, 'alias_type': str}],
                    'meridian_variants': [{'variant': str, 'standard': str}],
                    'primary_terms': [str],
                    'errors': [str],
                }
        """
        results: Dict[str, Any] = {
            'sublib': {},
            'total_inserted': 0,
            'total_duplicates': 0,
            'herb_aliases': [],
            'meridian_variants': [],
            'primary_terms': [],
            'errors': [],
        }

        # 第一步：导入中药名辞典（特殊格式，需要解析别名）
        try:
            herb_result = self.import_herb_dict()
            results['sublib']['HERB_DICT'] = herb_result
            results['total_inserted'] += herb_result.get('inserted', 0)
            results['total_duplicates'] += herb_result.get('duplicates', 0)
            results['herb_aliases'].extend(herb_result.get('aliases', []))
            results['primary_terms'].extend(herb_result.get('primaries', []))
        except Exception as e:
            err_msg = f"HERB_DICT import failed: {e}"
            logger.error(err_msg)
            results['errors'].append(err_msg)

        # 第二步：导入单术语辞典（每行一个术语）
        single_line_sublibs = [
            'SYMPTOM_DICT', 'SYNDROME_DICT', 'TCM_DISEASE_DICT',
            'BASIC_DICT', 'DIAGNOSIS_DICT',
        ]
        for sublib_id in single_line_sublibs:
            try:
                result = self.import_single_line_dict(sublib_id)
                results['sublib'][sublib_id] = result
                results['total_inserted'] += result.get('inserted', 0)
                results['total_duplicates'] += result.get('duplicates', 0)
            except Exception as e:
                err_msg = f"{sublib_id} import failed: {e}"
                logger.error(err_msg)
                results['errors'].append(err_msg)

        # 第三步：导入逗号分隔辞典（每行多个术语）
        comma_separated_sublibs = [
            'TCM_TERM_DICT', 'WM_DISEASE_DICT',
        ]
        for sublib_id in comma_separated_sublibs:
            try:
                result = self.import_comma_separated_dict(sublib_id)
                results['sublib'][sublib_id] = result
                results['total_inserted'] += result.get('inserted', 0)
                results['total_duplicates'] += result.get('duplicates', 0)
            except Exception as e:
                err_msg = f"{sublib_id} import failed: {e}"
                logger.error(err_msg)
                results['errors'].append(err_msg)

        # 第四步：导入需要自动分类的混杂辞典
        auto_classify_sublibs = [
            'INTERNAL_DICT', 'GYNECOLOGY_DICT', 'PEDIATRICS_DICT',
        ]
        for sublib_id in auto_classify_sublibs:
            try:
                result = self.import_auto_classify_dict(sublib_id)
                results['sublib'][sublib_id] = result
                results['total_inserted'] += result.get('inserted', 0)
                results['total_duplicates'] += result.get('duplicates', 0)
            except Exception as e:
                err_msg = f"{sublib_id} import failed: {e}"
                logger.error(err_msg)
                results['errors'].append(err_msg)

        # 第五步：生成并插入经络名变体
        try:
            meridian_result = self.generate_meridian_variants()
            results['meridian_variants'] = meridian_result
        except Exception as e:
            err_msg = f"Meridian variant generation failed: {e}"
            logger.error(err_msg)
            results['errors'].append(err_msg)

        logger.info(
            "Import complete: %d inserted, %d duplicates, %d errors",
            results['total_inserted'],
            results['total_duplicates'],
            len(results['errors']),
        )
        return results

    def import_herb_dict(self) -> dict:
        """导入中药名辞典

        解析中药名辞典文件，每行格式为::

            正名, 别名1, 别名2, 炮制名1, ...

        使用 classify_herb_alias 方法对每个别名进行分类，
        识别炮制变体、道地变体和通用别名。

        Returns:
            导入结果字典，格式为::

                {
                    'inserted': int,       # 成功插入的术语数
                    'duplicates': int,     # 重复的术语数
                    'aliases': [{'alias': str, 'primary': str, 'alias_type': str}],
                    'primaries': [str],    # 正名列表
                }
        """
        config = self.SUBLIB_CONFIG['HERB_DICT']
        file_path = os.path.join(self._dict_dir, config['file'])
        sublib_db_id = self._sublib_ids['HERB_DICT']

        result = {
            'inserted': 0,
            'duplicates': 0,
            'aliases': [],
            'primaries': [],
        }

        if not os.path.exists(file_path):
            logger.warning("Herb dict file not found: %s", file_path)
            return result

        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                parsed = self.parse_herb_line(line)
                if not parsed:
                    continue

                primary = parsed['primary']
                aliases = parsed['aliases']

                # 确定安全级别
                safety = self.auto_assign_safety(primary, 'herb')

                # 插入正名
                term_data = {
                    'term_text': primary,
                    'sublib_id': sublib_db_id,
                    'semantic_category': 'herb',
                    'source_authority': '中药名辞典-DS',
                    'safety_level': safety,
                    'term_role': 'primary',
                    'confidence': 1.0,
                }
                primary_id = self._insert_term(term_data)

                if primary_id > 0:
                    result['inserted'] += 1
                    result['primaries'].append(primary)
                    self._primary_terms.append(primary)
                elif primary_id == 0:
                    result['duplicates'] += 1

                # 处理别名
                for alias in aliases:
                    alias_type = self.classify_herb_alias(alias, primary)

                    # 确定别名安全级别
                    alias_safety = self.auto_assign_safety(alias, 'herb')

                    alias_term_data = {
                        'term_text': alias,
                        'sublib_id': sublib_db_id,
                        'semantic_category': 'herb',
                        'source_authority': '中药名辞典-DS',
                        'safety_level': alias_safety,
                        'term_role': alias_type,
                        'confidence': 0.95,
                    }
                    alias_id = self._insert_term(alias_term_data)

                    if alias_id > 0:
                        result['inserted'] += 1
                        result['aliases'].append({
                            'alias': alias,
                            'primary': primary,
                            'alias_type': alias_type,
                        })
                        self._herb_aliases.append({
                            'alias': alias,
                            'primary': primary,
                            'alias_type': alias_type,
                        })
                    elif alias_id == 0:
                        result['duplicates'] += 1

                    # 插入关系（如果正名已插入）
                    if primary_id > 0 and alias_id > 0:
                        rel_type = alias_type if alias_type in ('processing', 'regional') else 'alias'
                        self._insert_relation(alias_id, primary_id, rel_type)

        logger.info(
            "HERB_DICT: %d inserted, %d duplicates, %d aliases",
            result['inserted'],
            result['duplicates'],
            len(result['aliases']),
        )
        return result

    def import_single_line_dict(self, sublib_id: str) -> dict:
        """导入单术语辞典（每行一个术语）

        适用于格式为每行一个独立术语的辞典文件。

        Args:
            sublib_id: 子库 ID 键（如 'SYMPTOM_DICT'）

        Returns:
            导入结果字典，格式为::

                {
                    'inserted': int,
                    'duplicates': int,
                }
        """
        config = self.SUBLIB_CONFIG[sublib_id]
        file_path = os.path.join(self._dict_dir, config['file'])
        sublib_db_id = self._sublib_ids[sublib_id]
        base_category = config['base_category'] or 'unknown'

        result = {'inserted': 0, 'duplicates': 0}

        if not os.path.exists(file_path):
            logger.warning("Dict file not found: %s", file_path)
            return result

        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('>'):
                    continue
                # 跳过元数据行和空行
                if not line or len(line) < 1:
                    continue
                # 跳过 markdown 标题行
                if line.startswith('---') or line.startswith('*'):
                    continue
                # 跳过包含英文标点的行（通常是元数据）
                if line.startswith('来源：') or line.startswith('词条总数'):
                    continue

                term_text = line
                safety = self.auto_assign_safety(term_text, base_category)

                term_data = {
                    'term_text': term_text,
                    'sublib_id': sublib_db_id,
                    'semantic_category': base_category,
                    'source_authority': config['file'],
                    'safety_level': safety,
                    'term_role': 'primary',
                    'confidence': 1.0,
                }
                term_id = self._insert_term(term_data)
                if term_id > 0:
                    result['inserted'] += 1
                elif term_id == 0:
                    result['duplicates'] += 1

        logger.info(
            "%s: %d inserted, %d duplicates",
            sublib_id,
            result['inserted'],
            result['duplicates'],
        )
        return result

    def import_comma_separated_dict(self, sublib_id: str) -> dict:
        """导入逗号分隔辞典（每行多个术语）

        适用于每行以逗号分隔多个相关术语的辞典文件。
        如::

            阴阳, 阳, 阴, 阴中之阳, 阳中之阴

        Args:
            sublib_id: 子库 ID 键（如 'TCM_TERM_DICT'）

        Returns:
            导入结果字典，格式为::

                {
                    'inserted': int,
                    'duplicates': int,
                }
        """
        config = self.SUBLIB_CONFIG[sublib_id]
        file_path = os.path.join(self._dict_dir, config['file'])
        sublib_db_id = self._sublib_ids[sublib_id]
        base_category = config['base_category'] or 'unknown'

        result = {'inserted': 0, 'duplicates': 0}

        if not os.path.exists(file_path):
            logger.warning("Dict file not found: %s", file_path)
            return result

        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                # 跳过 markdown 标题和空行
                if line.startswith('---') or line.startswith('**'):
                    continue
                if line.startswith('>') or line.startswith('##') or line.startswith('###'):
                    continue

                # 按逗号分割
                terms = [t.strip() for t in line.split('，') if t.strip()]
                if not terms:
                    # 尝试英文逗号
                    terms = [t.strip() for t in line.split(',') if t.strip()]

                for term_text in terms:
                    # 跳过过短的条目
                    if len(term_text) < 1:
                        continue
                    # 跳过 markdown 标记
                    if term_text.startswith('**') and term_text.endswith('**'):
                        term_text = term_text.strip('*')

                    safety = self.auto_assign_safety(term_text, base_category)

                    term_data = {
                        'term_text': term_text,
                        'sublib_id': sublib_db_id,
                        'semantic_category': base_category,
                        'source_authority': config['file'],
                        'safety_level': safety,
                        'term_role': 'primary',
                        'confidence': 1.0,
                    }
                    term_id = self._insert_term(term_data)
                    if term_id > 0:
                        result['inserted'] += 1
                    elif term_id == 0:
                        result['duplicates'] += 1

        logger.info(
            "%s: %d inserted, %d duplicates",
            sublib_id,
            result['inserted'],
            result['duplicates'],
        )
        return result

    def import_auto_classify_dict(self, sublib_id: str) -> dict:
        """导入需要自动分类的混杂辞典

        使用 AutoClassifier 对每条术语进行分类，根据分类结果
        确定 semantic_category。

        Args:
            sublib_id: 子库 ID 键（如 'INTERNAL_DICT'）

        Returns:
            导入结果字典，格式为::

                {
                    'inserted': int,
                    'duplicates': int,
                    'classifications': {category: count},
                }
        """
        config = self.SUBLIB_CONFIG[sublib_id]
        file_path = os.path.join(self._dict_dir, config['file'])
        sublib_db_id = self._sublib_ids[sublib_id]

        result = {
            'inserted': 0,
            'duplicates': 0,
            'classifications': {},
        }

        if not os.path.exists(file_path):
            logger.warning("Dict file not found: %s", file_path)
            return result

        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('>'):
                    continue
                # 跳过元数据行
                if line.startswith('---') or line.startswith('*'):
                    continue
                if line.startswith('来源：') or line.startswith('词条总数'):
                    continue

                term_text = line

                # 使用自动分类引擎
                classification = self._classifier.classify(term_text)
                category = classification['category']
                safety = classification['safety']

                # 统计分类结果
                result['classifications'][category] = result['classifications'].get(category, 0) + 1

                # 如果分类器未匹配，使用子库特定的默认分类
                if not classification['matched']:
                    if sublib_id == 'INTERNAL_DICT':
                        category = 'tcm_internal'
                    elif sublib_id == 'GYNECOLOGY_DICT':
                        category = 'tcm_gynecology'
                    elif sublib_id == 'PEDIATRICS_DICT':
                        category = 'tcm_pediatrics'
                    safety = 'low'

                # 对有毒药材进行安全检查
                safety = self.auto_assign_safety(term_text, category)

                term_data = {
                    'term_text': term_text,
                    'sublib_id': sublib_db_id,
                    'semantic_category': category,
                    'source_authority': config['file'],
                    'safety_level': safety,
                    'term_role': 'primary',
                    'confidence': 0.9 if classification['matched'] else 0.7,
                }
                term_id = self._insert_term(term_data)
                if term_id > 0:
                    result['inserted'] += 1
                elif term_id == 0:
                    result['duplicates'] += 1

        logger.info(
            "%s: %d inserted, %d duplicates, classifications=%s",
            sublib_id,
            result['inserted'],
            result['duplicates'],
            result['classifications'],
        )
        return result

    def parse_herb_line(self, line: str) -> Optional[dict]:
        """解析中药名辞典的一行

        每行格式为正名后跟逗号分隔的别名::

            白术, 于术, 冬术, 浙术, 生白术, 炒白术

        第一个元素为正名，其余为别名。

        Args:
            line: 辞典文件中的一行文本

        Returns:
            解析结果字典，格式为::

                {
                    'primary': str,   # 正名
                    'aliases': [str], # 别名列表
                }

            如果无法解析则返回 None。
        """
        if not line:
            return None

        # 按逗号分割（支持中英文逗号）
        parts = [p.strip() for p in re.split(r'[，,]', line) if p.strip()]
        if not parts:
            return None

        primary = parts[0]
        aliases = parts[1:] if len(parts) > 1 else []

        # 过滤空别名
        aliases = [a for a in aliases if a and a != primary]

        return {
            'primary': primary,
            'aliases': aliases,
        }

    def classify_herb_alias(self, alias: str, primary: str) -> str:
        """分类药材别名的类型

        按以下顺序检查：
        1. 炮制变体：是否以炮制前缀开头（如"炒白术"）
        2. 道地变体：是否包含产地标识（如"杭白芍"）
        3. 通用别名：其他别名

        Args:
            alias: 别名字符串
            primary: 正名字符串

        Returns:
            别名类型字符串：'processing' | 'regional' | 'common'

        Example:
            >>> importer.classify_herb_alias('炒白术', '白术')
            'processing'
            >>> importer.classify_herb_alias('杭白芍', '白芍')
            'regional'
            >>> importer.classify_herb_alias('于术', '白术')
            'common'
        """
        if not alias or not primary:
            return 'common'

        # 检查炮制前缀（按长度降序，避免"炒"匹配"麸炒"）
        for prefix in self.PROCESSING_PREFIXES:
            if alias == prefix + primary or alias.startswith(prefix + primary):
                return 'processing'
            # 处理如 "白术片" 的情况（后缀式炮制名）
            if alias == primary + prefix or alias.endswith(prefix):
                # 确保这是炮制变体而非通用别名
                if len(alias) <= len(primary) + len(prefix) + 1:
                    return 'processing'

        # 检查道地/产地变体
        regional_markers = [
            '杭', '亳', '川', '广', '怀', '浙', '云', '贵',
            '祁', '禹', '关', '北', '南', '辽', '蒙', '藏',
            '西', '淮', '岷', '温', '福', '建', '湘', '赣',
        ]
        for marker in regional_markers:
            if alias.startswith(marker) and primary in alias:
                return 'regional'
            if alias.endswith(marker) and primary in alias:
                return 'regional'

        # 道地简称（如"怀山药" -> "山药"）
        if any(alias.startswith(m) for m in regional_markers):
            # 去掉产地前缀后是否与正名相似
            for m in regional_markers:
                if alias.startswith(m):
                    remainder = alias[len(m):]
                    if remainder == primary or (len(remainder) > 1 and remainder in primary):
                        return 'regional'

        # 默认为通用别名
        return 'common'

    def generate_meridian_variants(self) -> list:
        """生成并插入 53 个经络名变体

        将经络名变体作为术语插入数据库，并与标准经络名建立关系。

        Returns:
            插入的经络名变体列表，格式为::

                [
                    {'variant': str, 'standard': str, 'term_id': int},
                    ...
                ]
        """
        inserted = []

        for variant in self.MERIDIAN_VARIANTS:
            standard = self.MERIDIAN_VARIANT_TO_STANDARD.get(variant, variant)

            # 检查是否已存在
            existing_id = self._get_term_id_by_text(variant)
            if existing_id:
                inserted.append({
                    'variant': variant,
                    'standard': standard,
                    'term_id': existing_id,
                    'note': 'already_exists',
                })
                continue

            # 查找标准名的术语 ID
            standard_id = self._get_term_id_by_text(standard)

            # 插入经络名变体
            term_data = {
                'term_text': variant,
                'sublib_id': None,  # 经络名不归属特定子库
                'semantic_category': 'meridian',
                'source_authority': '经络名变体标准库',
                'safety_level': 'low',
                'term_role': 'variant',
                'confidence': 1.0,
            }
            variant_id = self._insert_term(term_data)

            if variant_id > 0:
                inserted.append({
                    'variant': variant,
                    'standard': standard,
                    'term_id': variant_id,
                })

                # 与标准名建立关系
                if standard_id and standard_id != variant_id:
                    self._insert_relation(variant_id, standard_id, 'variant_of')

        self._meridian_variants_inserted = inserted
        logger.info("Generated %d meridian variants", len(inserted))
        return inserted

    def is_formula_name(self, text: str) -> bool:
        """判断文本是否为方剂名

        基于方剂后缀集合和例外集合进行判断。
        先检查后缀匹配，再排除例外情况。

        Args:
            text: 待判断的文本

        Returns:
            True 如果文本符合方剂名特征

        Example:
            >>> importer.is_formula_name('四物汤')
            True
            >>> importer.is_formula_name('方法')
            False
        """
        if not text or len(text) < 2:
            return False

        # 先检查例外
        if text in self.FORMULA_EXCEPTIONS:
            return False

        # 检查后缀
        for suffix in self.FORMULA_SUFFIXES:
            if text.endswith(suffix):
                # 确保后缀前至少有一个字
                prefix = text[:-len(suffix)]
                if len(prefix) >= 1:
                    return True

        return False

    def auto_assign_safety(self, term_text: str, category: str) -> str:
        """自动分配安全级别

        安全级别说明::

            critical - 有毒药材、否定词、剂量单位
            high     - 穴位名
            medium   - 经络名、方剂名、证型、病名
            low      - 其他普通术语

        Args:
            term_text: 术语文本
            category: 语义类别

        Returns:
            安全级别字符串：'critical' | 'high' | 'medium' | 'low'

        Example:
            >>> importer.auto_assign_safety('附子', 'herb')
            'critical'
            >>> importer.auto_assign_safety('百会穴', 'acupoint')
            'high'
        """
        if not term_text:
            return 'low'

        # 有毒药材检查（critical）
        if term_text in self.TOXIC_HERBS_CRITICAL:
            return 'critical'

        # 炮制后的有毒药材也需要标记
        for toxic in self.TOXIC_HERBS_CRITICAL:
            if toxic in term_text and term_text != toxic:
                # 是毒药的炮制变体
                if any(term_text.startswith(p) for p in self.PROCESSING_PREFIXES):
                    return 'critical'
                if term_text.endswith(toxic):
                    return 'critical'

        # 按类别分配
        category_safety = {
            'negation': 'critical',
            'dosage_unit': 'critical',
            'acupoint': 'high',
            'meridian': 'medium',
            'formula': 'medium',
            'tcm_syndrome': 'medium',
            'tcm_disease': 'medium',
            'wm_disease': 'medium',
            'herb': 'medium',
            'symptom': 'medium',
            'diagnosis_method': 'low',
            'tcm_term': 'low',
            'tcm_internal': 'low',
            'tcm_gynecology': 'low',
            'tcm_pediatrics': 'low',
        }

        return category_safety.get(category, 'low')

    def _insert_term(self, term_data: dict) -> int:
        """插入术语到数据库

        如果术语已存在（基于 term_text 的唯一性），则跳过。

        Args:
            term_data: 术语数据字典，格式为::

                {
                    'term_text': str,          # 必需
                    'sublib_id': Optional[int], # 子库 ID
                    'semantic_category': str,   # 语义类别
                    'source_authority': str,    # 来源
                    'safety_level': str,        # 安全级别
                    'term_role': str,           # 术语角色
                    'confidence': float,        # 置信度
                }

        Returns:
            新插入术语的 ID，如果术语已存在返回 0，插入失败返回 -1
        """
        try:
            with self._runtime_db.get_cursor() as cursor:
                # 检查是否已存在
                cursor.execute(
                    "SELECT id FROM Term WHERE term_text = %s",
                    (term_data['term_text'],),
                )
                if cursor.fetchone():
                    return 0

                # 动态构建插入语句（处理可选字段）
                fields = ['term_text', 'confidence']
                values = [term_data['term_text'], term_data.get('confidence', 1.0)]
                placeholders = ['%s', '%s']

                if term_data.get('sublib_id') is not None:
                    fields.append('sublib_id')
                    values.append(term_data['sublib_id'])
                    placeholders.append('%s')

                for field in ['semantic_category', 'source_authority',
                              'safety_level', 'term_role']:
                    if term_data.get(field) is not None:
                        fields.append(field)
                        values.append(term_data[field])
                        placeholders.append('%s')

                sql = (
                    f"INSERT INTO Term ({', '.join(fields)}) "
                    f"VALUES ({', '.join(placeholders)}) "
                    f"RETURNING id"
                )
                cursor.execute(sql, values)
                result = cursor.fetchone()
                return result['id'] if result else -1

        except Exception as e:
            logger.error("Failed to insert term '%s': %s", term_data.get('term_text', ''), e)
            return -1

    def _insert_relation(self, from_id: int, to_id: int, rel_type: str) -> None:
        """插入术语关系到数据库

        Args:
            from_id: 源术语 ID
            to_id: 目标术语 ID
            rel_type: 关系类型（'alias' | 'processing' | 'regional' | 'variant_of'）
        """
        if from_id <= 0 or to_id <= 0:
            return

        try:
            with self._runtime_db.get_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO TermRelation (from_term_id, to_term_id, rel_type)
                    VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (from_id, to_id, rel_type),
                )
        except Exception as e:
            logger.error(
                "Failed to insert relation %d -> %d (%s): %s",
                from_id, to_id, rel_type, e,
            )

    def _get_term_id_by_text(self, term_text: str) -> Optional[int]:
        """根据术语文本查询 ID

        Args:
            term_text: 术语文本

        Returns:
            术语 ID，如果不存在则返回 None
        """
        try:
            with self._runtime_db.get_cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM Term WHERE term_text = %s",
                    (term_text,),
                )
                result = cursor.fetchone()
                return result['id'] if result else None
        except Exception as e:
            logger.error("Failed to query term '%s': %s", term_text, e)
            return None

    def build_normalized_maps(self) -> dict:
        """构建反规范化字典

        收集导入过程中的所有别名和正名关系，构建用于
        运行时 O(1) 查询的反规范化字典。

        Returns:
            导入结果字典，格式与 import_all 返回相同
        """
        from kzocr.tcm_ocr.knowledge.term.normalized_maps import HerbNormalizedMaps

        import_results = {
            'herb_aliases': self._herb_aliases,
            'primary_terms': self._primary_terms,
            'meridian_variants': self._meridian_variants_inserted,
        }

        HerbNormalizedMaps.build_from_import(import_results)

        memory = HerbNormalizedMaps.get_memory_size()
        logger.info("Normalized maps built: %s", memory)

        return import_results
