"""
BookTypeDetector - 书籍类型自动检测器

根据书籍元数据（标题、作者等）和目录文本自动判断书籍类型：
- formula: 方剂书（仅需药材库）
- acupuncture: 针灸书（仅需经络穴位库）
- internal_medicine: 内科书（药材+常用穴）
- tcm_monograph: 中医专著（药材+经络穴位）

使用关键词匹配 + 加权评分机制，支持多种特征维度综合判断。
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class BookTypeDetector:
    """
    书籍类型自动检测器

    类型：
    - formula: 方剂书（仅需药材库）
    - acupuncture: 针灸书（仅需经络穴位库）
    - internal_medicine: 内科书（药材+常用穴）
    - tcm_monograph: 中医专著（药材+经络穴位）
    """

    # ========================================================================
    # 类型关键词配置
    # ========================================================================
    TYPE_KEYWORDS = {
        'formula': {
            'title': ['方剂', '方书', '经方', '验方', '方剂学', '方歌', '处方',
                      '汤头', '汤头歌', '汤液', '本草方', '秘方', '丹方',
                      '成方', '时方', '古方', '名方', '医方', '药方',
                      '和剂局方', '千金方', '肘后方', '外台秘要',
                      '普济方', '太平惠民', '圣惠方', '圣济总录',
                      '本草备要', '汤头歌诀', '医方集解', '成方切用'],
            'content': ['方剂组成', '君臣佐使', '配伍', '方解', '方义',
                        '组成', '功用', '主治', '用法', '用量',
                        '加减变化', '随证加减', '煎服法', '服法',
                        '药物组成', '组方分析', '配伍意义',
                        '君药', '臣药', '佐药', '使药',
                        '药对', '药组', '方剂配伍'],
        },
        'acupuncture': {
            'title': ['针灸', '针法', '灸法', '经络', '腧穴', '穴位',
                      '针经', '灸经', '甲乙经', '明堂', '铜人',
                      '针道', '灸道', '刺法', '毫针', '艾灸',
                      '十四经', '奇经八脉', '经穴', '经外奇穴',
                      '针灸大成', '针灸聚英', '针灸甲乙经',
                      '针方', '灸方', '子午流注', '灵龟八法',
                      '飞腾八法', '迎随补泻'],
            'content': ['针刺', '艾灸', '得气', '补泻', '循经',
                        '取穴', '针感', '针下', '灸疗', '施灸',
                        '捻转', '提插', '迎随', '呼吸', '开阖',
                        '毫针刺法', '三棱针', '皮肤针', '电针',
                        '温针灸', '艾炷灸', '艾条灸', '温和灸',
                        '雀啄灸', '回旋灸', '隔物灸',
                        '经脉所过', '主治所及', '循经取穴',
                        '近部取穴', '远部取穴', '辨证取穴',
                        '特定穴', '五输穴', '原穴', '络穴',
                        '郄穴', '背俞穴', '募穴', '下合穴',
                        '八会穴', '八脉交会穴', '交会穴'],
        },
        'internal_medicine': {
            'title': ['内科', '杂病', '伤寒', '温病', '金匮',
                      '外感', '热病', '疫病', '瘟疫', '湿热',
                      '风温', '春温', '暑温', '湿温', '秋燥',
                      '冬温', '伏气温病', '新感温病',
                      '伤寒论', '金匮要略', '温病条辨',
                      '湿热条辨', '外感温病', '时病论',
                      '张氏医通', '景岳全书', '临证指南',
                      '薛氏医案', '丹溪心法', '东垣十书',
                      '医学衷中', '医宗金鉴', '古今医案',
                      '医林改错'],
            'content': ['证候', '辨证', '治法', '方药',
                        '六经辨证', '卫气营血', '三焦辨证',
                        '八纲辨证', '脏腑辨证', '气血津液辨证',
                        '病因', '病机', '病位', '病性',
                        '表证', '里证', '寒证', '热证',
                        '虚证', '实证', '阴证', '阳证',
                        '卫分证', '气分证', '营分证', '血分证',
                        '上焦', '中焦', '下焦',
                        '太阳病', '阳明病', '少阳病',
                        '太阴病', '少阴病', '厥阴病',
                        '证型', '治法', '处方', '遣方用药',
                        '辨证论治', '辨证施治', '随证治之',
                        '舌象', '脉象', '舌苔', '脉诊',
                        '问诊', '闻诊', '望诊', '切诊'],
        },
        'tcm_monograph': {
            'title': ['中医', '临证', '经验', '医案', '医话',
                      '医论', '医述', '医镜', '医衡', '医碥',
                      '笔谈', '心得', '荟萃', '集萃', '集要',
                      '全书', '大全', '类编', '类案', '集成',
                      '正传', '正宗', '正宗', '正宗',
                      '珍本', '善本', '孤本', '稿本',
                      '国医', '名医', '老中医', '名老中医',
                      '学术思想', '临床经验', '用药经验',
                      '诊治经验', '从医经验', '实践录',
                      '五十年', '四十年', '三十年',
                      '传薪', '薪传', '传承', '继承',
                      '发挥', '阐微', '探源', '钩玄'],
            'content': ['医案', '医话', '按语', '体会',
                        '心得', '经验', '小结', '总结',
                        '附案', '附记', '附录', '附方',
                        '初诊', '复诊', '三诊', '四诊',
                        '随访', '转归', '预后', '疗效',
                        '典型病例', '病案举例', '举例说明',
                        '临证体会', '诊治体会', '辨治心得',
                        '用药体会', '用药经验', '处方经验'],
        },
    }

    # 关键词权重配置
    TITLE_WEIGHT = 3.0      # 标题匹配权重更高
    CONTENT_WEIGHT = 1.0
    TOC_WEIGHT = 1.5        # 目录匹配权重

    # 各类型最低阈值
    DETECTION_THRESHOLD = 2.0

    # ========================================================================
    # 核心检测方法
    # ========================================================================

    @classmethod
    def detect(cls, book_meta: dict, toc_text: str = '') -> str:
        """
        自动检测书籍类型

        综合标题、内容特征、目录进行加权评分，返回最可能的类型。

        Args:
            book_meta: 书籍元数据字典，包含 'title', 'author', 'publisher' 等字段
            toc_text: 目录文本，用于辅助判断

        Returns:
            检测到的书籍类型字符串: 'formula' | 'acupuncture' |
            'internal_medicine' | 'tcm_monograph'
        """
        title = book_meta.get('title', '') if book_meta else ''
        author = book_meta.get('author', '') if book_meta else ''

        # 合并标题和作者文本用于分析
        title_text = f"{title} {author}".strip()

        logger.info("Detecting book type for: title='%s', toc_length=%d",
                    title, len(toc_text))

        # 多维评分
        title_scores = cls._score_by_title(title_text)
        toc_scores = cls._score_by_toc(toc_text)

        # 合并分数（加权）
        total_scores: Dict[str, float] = {}
        for book_type in cls.TYPE_KEYWORDS:
            total_scores[book_type] = (
                title_scores.get(book_type, 0) * cls.TITLE_WEIGHT +
                toc_scores.get(book_type, 0) * cls.TOC_WEIGHT
            )

        # 查找最高分
        best_type = 'tcm_monograph'  # 默认类型
        best_score = 0.0

        for book_type, score in total_scores.items():
            logger.debug("Type '%s': title_score=%.1f, toc_score=%.1f, total=%.1f",
                         book_type,
                         title_scores.get(book_type, 0),
                         toc_scores.get(book_type, 0),
                         score)
            if score > best_score:
                best_score = score
                best_type = book_type

        # 如果最高分低于阈值，使用默认类型
        if best_score < cls.DETECTION_THRESHOLD:
            logger.info("Detection score %.1f below threshold %.1f, using default 'tcm_monograph'",
                        best_score, cls.DETECTION_THRESHOLD)
            best_type = 'tcm_monograph'

        logger.info("Detected book type: '%s' (score=%.1f)", best_type, best_score)
        return best_type

    # ========================================================================
    # 评分方法
    # ========================================================================

    @classmethod
    def _score_by_title(cls, title: str) -> dict:
        """
        根据标题关键词评分

        Args:
            title: 书籍标题文本

        Returns:
            各类型分数字典
        """
        scores: Dict[str, float] = {}
        title_lower = title.lower()

        for book_type, keywords in cls.TYPE_KEYWORDS.items():
            score = 0.0
            title_keywords = keywords.get('title', [])

            for keyword in title_keywords:
                if keyword in title:
                    # 精确匹配加分更多
                    score += 1.0
                    # 如果是标题开头匹配，额外加分
                    if title.startswith(keyword):
                        score += 0.5

            # 正则匹配（处理变体）
            if book_type == 'formula':
                if re.search(r'方[剂剂歌书解]?', title):
                    score += 0.5
                if re.search(r'[汤本草丹]?方', title):
                    score += 0.3
            elif book_type == 'acupuncture':
                if re.search(r'针[灸刺法道]?', title):
                    score += 0.5
                if re.search(r'灸[法经道]?', title):
                    score += 0.5
                if re.search(r'经络[穴腧]?', title):
                    score += 0.5
            elif book_type == 'internal_medicine':
                if re.search(r'伤[寒 Cold]?', title):
                    score += 0.5
                if re.search(r'温[病热]?', title):
                    score += 0.5
                if re.search(r'杂[病证]?', title):
                    score += 0.5
            elif book_type == 'tcm_monograph':
                if re.search(r'医[案话论述镜衡碥]?', title):
                    score += 0.5
                if re.search(r'临[证床床]?', title):
                    score += 0.5
                if re.search(r'经验', title):
                    score += 0.5

            scores[book_type] = score

        return scores

    @classmethod
    def _score_by_toc(cls, toc_text: str) -> dict:
        """
        根据目录文本评分

        Args:
            toc_text: 目录文本

        Returns:
            各类型分数字典
        """
        scores: Dict[str, float] = {}

        if not toc_text:
            return {bt: 0.0 for bt in cls.TYPE_KEYWORDS}

        for book_type, keywords in cls.TYPE_KEYWORDS.items():
            score = 0.0
            content_keywords = keywords.get('content', [])

            for keyword in content_keywords:
                count = toc_text.count(keyword)
                if count > 0:
                    # 出现次数越多分数越高，但递减
                    score += min(count * 0.3, 2.0)

            scores[book_type] = score

        return scores

    # ========================================================================
    # 缓存组件需求
    # ========================================================================

    @classmethod
    def get_required_caches(cls, book_type: str) -> dict:
        """
        根据书籍类型返回需要的缓存组件配置

        Args:
            book_type: 书籍类型

        Returns:
            缓存组件配置字典，包含各层是否需要加载
        """
        configs = {
            'formula': {
                'layer_0': True,           # critical术语（所有类型都需要）
                'layer_1_herb': True,      # 药材库
                'layer_1_acupoint': False, # 不需要穴位
                'layer_1_meridian': False, # 不需要经络
                'acupoint_common_only': False,
                'description': '方剂书：加载critical术语 + 药材库',
            },
            'acupuncture': {
                'layer_0': True,
                'layer_1_herb': False,     # 不需要药材
                'layer_1_acupoint': True,  # 穴位库
                'layer_1_meridian': True,  # 经络库
                'acupoint_common_only': False,
                'description': '针灸书：加载critical术语 + 经络穴位库',
            },
            'internal_medicine': {
                'layer_0': True,
                'layer_1_herb': True,      # 药材库
                'layer_1_acupoint': True,  # 常用穴位（轻量）
                'layer_1_meridian': False, # 不需要完整经络
                'acupoint_common_only': True,
                'description': '内科书：加载critical术语 + 药材库 + 常用穴位',
            },
            'tcm_monograph': {
                'layer_0': True,
                'layer_1_herb': True,      # 药材库
                'layer_1_acupoint': True,  # 完整穴位库
                'layer_1_meridian': True,  # 经络库
                'acupoint_common_only': False,
                'description': '中医专著：加载全部缓存层',
            },
        }

        if book_type not in configs:
            logger.warning("Unknown book type '%s', using default 'tcm_monograph' config", book_type)
            return configs['tcm_monograph']

        return configs[book_type]

    @classmethod
    def get_supported_types(cls) -> List[str]:
        """
        获取所有支持的书籍类型列表

        Returns:
            书籍类型字符串列表
        """
        return list(cls.TYPE_KEYWORDS.keys())

    @classmethod
    def validate_type(cls, book_type: str) -> bool:
        """
        验证书籍类型是否有效

        Args:
            book_type: 待验证的类型字符串

        Returns:
            是否有效
        """
        return book_type in cls.TYPE_KEYWORDS or book_type == 'auto'
