"""
自动分类引擎 - AutoClassifier 类

覆盖内科/妇科/儿科混杂辞典的内容规则引擎。
基于正则表达式模式匹配，对术语进行自动分类和安全级别赋值。

优先级规则（数值越大越优先）：
    - 否定词: 100
    - 剂量单位: 99
    - 穴位名: 95
    - 经络名: 93
    - 方剂名: 90
    - 证型: 85
    - 中医病名: 80
"""

from __future__ import annotations

import logging
import re
from typing import List

logger = logging.getLogger(__name__)


class AutoClassifier:
    """自动分类引擎 - 覆盖内科/妇科/儿科混杂辞典

    基于内容规则对术语进行自动分类，每条规则包含正则模式、
    目标类别、安全级别和优先级。分类时按优先级降序匹配，
    第一条匹配的规则决定分类结果。

    Attributes:
        CONTENT_RULES: 分类规则列表，每条规则为字典格式
    """

    CONTENT_RULES: List[dict] = [
        # 否定词（最高优先级）
        {
            'pattern': r'^(不|无|非|忌|禁|勿|慎)$',
            'category': 'negation',
            'safety': 'critical',
            'priority': 100,
        },
        # 剂量单位
        {
            'pattern': r'^(克|钱|两|分|斤|毫升|升|钱匕|方寸匕|丸|片|粒|支|瓶|帖|包)$',
            'category': 'dosage_unit',
            'safety': 'critical',
            'priority': 99,
        },
        # 穴位名
        {
            'pattern': r'.{1,3}穴$',
            'category': 'acupoint',
            'safety': 'high',
            'priority': 95,
            'exceptions': {
                '分穴', '穴位', '穴道', '取穴', '穴注', '背穴', '压穴',
                '穴贴', '耳穴', '体穴', '穴疗', '穴数', '穴图', '穴名',
                '穴方', '穴法', '穴灸', '埋穴', '点穴', '刺穴', '针穴',
                '按穴', '摩穴', '刮穴', '拔穴', '温穴', '电穴',
            },
        },
        # 经络名 - 手足三阴三阳经
        {
            'pattern': r'(手|足)(太阴|阳明|少阴|太阳|厥阴|少阳)(肺经|大肠经|胃经|脾经|心经|小肠经|膀胱经|肾经|心包经|三焦经|胆经|肝经)',
            'category': 'meridian',
            'safety': 'medium',
            'priority': 93,
        },
        # 经络名 - 奇经八脉
        {
            'pattern': r'^(任脉|督脉|冲脉|带脉|阴跷脉|阳跷脉|阴维脉|阳维脉)$',
            'category': 'meridian',
            'safety': 'medium',
            'priority': 93,
        },
        # 方剂名
        {
            'pattern': r'.+(汤|散|丸|丹|膏|饮|饮子|方|煎|露|汁|胶|洗|锭|栓|线|油)$',
            'category': 'formula',
            'safety': 'medium',
            'priority': 90,
            'min_len': 2,
        },
        # 证型
        {
            'pattern': r'.+(证|候)$',
            'category': 'tcm_syndrome',
            'safety': 'medium',
            'priority': 85,
        },
        # 中医病名（在 TCM_DISEASE_DICT 中已覆盖，此处兜底）
        {
            'pattern': r'.+(病|症)$',
            'category': 'tcm_disease',
            'safety': 'medium',
            'priority': 80,
        },
    ]

    def classify(self, text: str) -> dict:
        """对单个术语进行分类

        按照优先级从高到低的顺序匹配规则，返回第一条匹配规则的
        分类结果。如果没有规则匹配，返回未知分类。

        Args:
            text: 待分类的术语文本

        Returns:
            分类结果字典，格式为::

                {
                    'text': str,          # 原始文本
                    'category': str,      # 分类类别
                    'safety': str,        # 安全级别
                    'priority': int,      # 匹配规则的优先级
                    'matched': bool,      # 是否匹配到规则
                }

        Example:
            >>> classifier = AutoClassifier()
            >>> result = classifier.classify('百会穴')
            >>> result['category']
            'acupoint'
        """
        if not text or not isinstance(text, str):
            return {
                'text': text or '',
                'category': 'unknown',
                'safety': 'low',
                'priority': 0,
                'matched': False,
            }

        # 按优先级降序排序规则
        sorted_rules = sorted(
            self.CONTENT_RULES,
            key=lambda r: r.get('priority', 0),
            reverse=True,
        )

        for rule in sorted_rules:
            if self._match_rule(text, rule):
                return {
                    'text': text,
                    'category': rule['category'],
                    'safety': rule.get('safety', 'low'),
                    'priority': rule.get('priority', 0),
                    'matched': True,
                }

        # 无规则匹配
        return {
            'text': text,
            'category': 'unknown',
            'safety': 'low',
            'priority': 0,
            'matched': False,
        }

    def classify_batch(self, texts: List[str]) -> List[dict]:
        """批量分类术语

        Args:
            texts: 待分类的术语文本列表

        Returns:
            分类结果字典列表，每个元素与输入文本一一对应

        Example:
            >>> classifier = AutoClassifier()
            >>> results = classifier.classify_batch(['百会穴', '四物汤'])
            >>> [r['category'] for r in results]
            ['acupoint', 'formula']
        """
        if not texts:
            return []
        return [self.classify(text) for text in texts]

    def _match_rule(self, text: str, rule: dict) -> bool:
        """判断文本是否匹配给定规则

        执行规则匹配时考虑以下约束：
        1. 检查 exceptions 集合（如有）
        2. 检查最小长度约束（如有）
        3. 执行正则表达式匹配

        Args:
            text: 待匹配的文本
            rule: 规则字典，包含 pattern、exceptions、min_len 等键

        Returns:
            True 如果文本匹配规则，False 否则
        """
        if not text or not rule:
            return False

        # 检查例外集合
        exceptions = rule.get('exceptions')
        if exceptions and text in exceptions:
            return False

        # 检查最小长度约束
        min_len = rule.get('min_len')
        if min_len is not None and len(text) < min_len:
            return False

        # 执行正则匹配
        pattern = rule.get('pattern', '')
        if not pattern:
            return False

        try:
            return bool(re.search(pattern, text))
        except re.error as e:
            logger.warning("Invalid regex pattern '%s': %s", pattern, e)
            return False
