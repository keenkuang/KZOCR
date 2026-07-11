"""
中医术语知识库模块

提供术语导入、自动分类、别名反规范化等核心功能。

主要类:
    TermImporter: 术语辞典导入器
    AutoClassifier: 自动分类引擎
    HerbNormalizedMaps: 药材别名反规范化字典
"""

from kzocr.tcm_ocr.knowledge.term.auto_classifier import AutoClassifier
from kzocr.tcm_ocr.knowledge.term.importer import TermImporter
from kzocr.tcm_ocr.knowledge.term.normalized_maps import HerbNormalizedMaps

__all__ = [
    'AutoClassifier',
    'HerbNormalizedMaps',
    'TermImporter',
]
