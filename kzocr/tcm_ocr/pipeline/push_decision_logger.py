"""
推送决策日志记录器 - PushDecisionLogger 类

在OCR处理流水线的15个决策点记录推送原因，支持决策链路追踪、
人工校对工作流和完整的统计分析。

使用示例:
    logger = PushDecisionLogger(runtime_db)
    
    # 决策点1: 多引擎识别分歧
    decision_id = logger.log_consensus_dispute(
        book_id="book_001",
        line_id=42,
        page_num=12,
        engine_results={
            "paddleocr": {"text": "当归10g", "confidence": 0.95},
            "mineru": {"text": "当阳10g", "confidence": 0.88},
        }
    )
    
    # 决策点2: 剂量异常
    decision_id = logger.log_dosage_pre_alert(
        book_id="book_001",
        line_id=43,
        page_num=12,
        alert={
            "herb_name": "附子",
            "detected_dosage": "30g",
            "standard_max": "15g",
            "severity": "overdose",
        }
    )
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PushDecisionLogger:
    """推送决策日志记录器 - 在每个决策点记录推送原因"""

    # =========================================================================
    # 15种推送原因字典
    # =========================================================================

    REASON_CODES = {
        'CONSENSUS_DISPUTE': {
            'name': '多引擎识别分歧',
            'priority': 'P1',
            'color': '#FF9500',
            'description': '多个OCR引擎对同一行文本的识别结果存在分歧',
        },
        'DOSAGE_PRE_ALERT': {
            'name': '剂量异常(原始文本)',
            'priority': 'P0',
            'color': '#FF3B30',
            'description': '原始OCR识别文本中的药材剂量超出药典法定范围',
        },
        'DOSAGE_POST_ALERT': {
            'name': '剂量异常(LLM修改后)',
            'priority': 'P0',
            'color': '#FF3B30',
            'description': 'LLM校正后的文本中剂量仍然异常',
        },
        'NEGATION_VIOLATION': {
            'name': '否定词完整性破坏',
            'priority': 'P0',
            'color': '#FF3B30',
            'description': 'LLM校正过程中破坏了否定词结构',
        },
        'GLYPH_VERIFY_FAILED': {
            'name': '字形验证失败',
            'priority': 'P0',
            'color': '#FF3B30',
            'description': '字形验证引擎发现关键字段的字形不匹配',
        },
        'LLM_LOCAL_TIMEOUT': {
            'name': '本地LLM超时',
            'priority': 'P1',
            'color': '#FF9500',
            'description': '本地部署的LLM在规定时间内未返回结果',
        },
        'LLM_CLOUD_TIMEOUT': {
            'name': '云端LLM超时',
            'priority': 'P1',
            'color': '#FF9500',
            'description': '云端LLM API在规定时间内未返回结果',
        },
        'LLM_PARSE_ERROR': {
            'name': 'LLM输出解析失败',
            'priority': 'P1',
            'color': '#FF9500',
            'description': 'LLM返回的输出格式不符合预期',
        },
        'LINE_COUNT_MISMATCH': {
            'name': '行数不守恒',
            'priority': 'P1',
            'color': '#FF9500',
            'description': 'LLM处理后输出行数与输入行数不一致',
        },
        'CROSS_PAGE_SPLIT_FAIL': {
            'name': '跨页拆分失败',
            'priority': 'P1',
            'color': '#FF9500',
            'description': '跨页段落拆分算法失败',
        },
        'FORMULA_EXTRACT_FAIL': {
            'name': '方剂提取异常',
            'priority': 'P2',
            'color': '#FFCC00',
            'description': '从文本中方剂名称提取失败',
        },
        'FORMULA_REF_MISMATCH': {
            'name': '方剂引用不一致',
            'priority': 'P2',
            'color': '#FFCC00',
            'description': '方剂引用与正文中的方剂名称不匹配',
        },
        'MISSING_CHAR_DETECTED': {
            'name': '疑似漏字',
            'priority': 'P2',
            'color': '#FFCC00',
            'description': '缺失字符检测算法发现疑似漏识别的字符',
        },
        'EXTRA_CHAR_DETECTED': {
            'name': '疑似粘连字',
            'priority': 'P2',
            'color': '#FFCC00',
            'description': '额外字符检测算法发现疑似粘连识别的字符',
        },
        'PUBLISHER_LOW_ACCURACY': {
            'name': '出版社低准确率',
            'priority': 'P3',
            'color': '#007AFF',
            'description': '该出版社历史书籍的OCR准确率统计偏低',
        },
    }

    # =========================================================================
    # 初始化
    # =========================================================================

    def __init__(self, runtime_db: object) -> None:
        """
        初始化推送决策日志记录器

        Args:
            runtime_db: RuntimeDB 实例，提供 PostgreSQL 数据库连接
        """
        self.runtime_db = runtime_db

    # =========================================================================
    # 决策点 1: 多引擎识别分歧
    # =========================================================================

    def log_consensus_dispute(
        self,
        book_id: str,
        line_id: int,
        page_num: int,
        engine_results: dict,
    ) -> int:
        """
        决策点1: 记录多引擎识别分歧

        当多个OCR引擎对同一行文本的识别结果存在分歧，且投票无法达成共识时触发。

        Args:
            book_id: 书籍ID
            line_id: 行ID
            page_num: 页码
            engine_results: 各引擎识别结果，格式为
                {"engine_name": {"text": "...", "confidence": 0.9}, ...}

        Returns:
            int: 新创建的决策记录ID
        """
        # 分析分歧详情
        texts = []
        for engine_name, result in engine_results.items():
            if isinstance(result, dict):
                texts.append(result.get('text', ''))
            else:
                texts.append(str(result))

        unique_texts = list(set(t for t in texts if t))

        reason_details = {
            'CONSENSUS_DISPUTE': {
                'engine_count': len(engine_results),
                'unique_variants': len(unique_texts),
                'variants': [
                    {'text': text, 'engines': [
                        name for name, res in engine_results.items()
                        if (isinstance(res, dict) and res.get('text') == text)
                        or str(res) == text
                    ]}
                    for text in unique_texts
                ],
                'full_engine_results': {
                    name: res if isinstance(res, dict) else {'text': str(res), 'confidence': 0.0}
                    for name, res in engine_results.items()
                },
            }
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=line_id,
            para_id=None,
            page_num=page_num,
            reason_codes=['CONSENSUS_DISPUTE'],
            reason_details=reason_details,
            priority='P1',
            engine_snapshots={'engine_results': engine_results},
        )

    # =========================================================================
    # 决策点 2: 剂量异常(原始文本)
    # =========================================================================

    def log_dosage_pre_alert(
        self,
        book_id: str,
        line_id: int,
        page_num: int,
        alert: dict,
    ) -> int:
        """
        决策点2: 记录剂量异常（原始OCR文本）

        原始OCR识别文本中的药材剂量超出药典法定范围时触发。

        Args:
            book_id: 书籍ID
            line_id: 行ID
            page_num: 页码
            alert: 剂量告警信息，格式为
                {
                    "herb_name": "附子",
                    "detected_dosage": "30g",
                    "standard_max": "15g",
                    "standard_min": "3g",
                    "severity": "overdose",  # overdose|underdose|severe_overdose
                    "original_line_text": "..."
                }

        Returns:
            int: 新创建的决策记录ID
        """
        reason_details = {
            'DOSAGE_PRE_ALERT': {
                'herb_name': alert.get('herb_name', 'unknown'),
                'detected_dosage': alert.get('detected_dosage', ''),
                'detected_value': alert.get('detected_value'),
                'detected_unit': alert.get('detected_unit', 'g'),
                'standard_max': alert.get('standard_max'),
                'standard_min': alert.get('standard_min'),
                'severe_threshold': alert.get('severe_threshold'),
                'severity': alert.get('severity', 'unknown'),
                'toxicity_level': alert.get('toxicity_level'),
                'original_line_text': alert.get('original_line_text', ''),
                'pharmacopoeia_version': alert.get('pharmacopoeia_version'),
            }
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=line_id,
            para_id=None,
            page_num=page_num,
            reason_codes=['DOSAGE_PRE_ALERT'],
            reason_details=reason_details,
            priority='P0',
        )

    # =========================================================================
    # 决策点 3: 剂量异常(LLM修改后)
    # =========================================================================

    def log_dosage_post_alert(
        self,
        book_id: str,
        line_id: int,
        page_num: int,
        pre_alerts: list,
        post_alerts: list,
    ) -> int:
        """
        决策点3: 记录剂量异常（LLM修改后）

        LLM校正后的文本中剂量仍然异常，或LLM错误修改了正确的剂量。

        Args:
            book_id: 书籍ID
            line_id: 行ID
            page_num: 页码
            pre_alerts: LLM校正前的告警列表
            post_alerts: LLM校正后的告警列表

        Returns:
            int: 新创建的决策记录ID
        """
        # 分析LLM修改前后变化
        pre_count = len(pre_alerts)
        post_count = len(post_alerts)

        # 找出新增和消除的告警
        pre_keys = set(
            f"{a.get('herb_name', '')}:{a.get('detected_dosage', '')}"
            for a in pre_alerts
        )
        post_keys = set(
            f"{a.get('herb_name', '')}:{a.get('detected_dosage', '')}"
            for a in post_alerts
        )

        new_alerts = post_keys - pre_keys
        resolved_alerts = pre_keys - post_keys

        reason_details = {
            'DOSAGE_POST_ALERT': {
                'pre_alert_count': pre_count,
                'post_alert_count': post_count,
                'pre_alerts': [
                    {
                        'herb_name': a.get('herb_name'),
                        'dosage': a.get('detected_dosage'),
                        'severity': a.get('severity'),
                    }
                    for a in pre_alerts
                ],
                'post_alerts': [
                    {
                        'herb_name': a.get('herb_name'),
                        'dosage': a.get('detected_dosage'),
                        'severity': a.get('severity'),
                    }
                    for a in post_alerts
                ],
                'newly_introduced': list(new_alerts),
                'resolved': list(resolved_alerts),
                'llm_modified_dosage': post_count > 0 and pre_count != post_count,
            }
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=line_id,
            para_id=None,
            page_num=page_num,
            reason_codes=['DOSAGE_POST_ALERT'],
            reason_details=reason_details,
            priority='P0',
        )

    # =========================================================================
    # 决策点 4: 否定词完整性破坏
    # =========================================================================

    def log_negation_violation(
        self,
        book_id: str,
        line_id: int,
        page_num: int,
        original: str,
        modified: str,
        lost: list,
    ) -> int:
        """
        决策点4: 记录否定词完整性破坏

        LLM校正过程中破坏了否定词结构（如不、无、非、未、否、莫、勿等），
        导致语义反转或完整性受损。

        Args:
            book_id: 书籍ID
            line_id: 行ID
            page_num: 页码
            original: 原始文本
            modified: LLM修改后的文本
            lost: 丢失的否定词列表

        Returns:
            int: 新创建的决策记录ID
        """
        reason_details = {
            'NEGATION_VIOLATION': {
                'original_text': original,
                'modified_text': modified,
                'lost_negations': lost,
                'negation_count_before': self._count_negations(original),
                'negation_count_after': self._count_negations(modified),
                'semantic_risk': 'high' if len(lost) > 0 else 'medium',
                'affected_negations': [
                    {
                        'negation': neg,
                        'position': original.find(neg) if neg in original else -1,
                        'context_before': self._extract_context(original, neg),
                        'context_after': self._extract_context(modified, neg),
                    }
                    for neg in lost
                ],
            }
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=line_id,
            para_id=None,
            page_num=page_num,
            reason_codes=['NEGATION_VIOLATION'],
            reason_details=reason_details,
            priority='P0',
        )

    # =========================================================================
    # 决策点 5: 字形验证失败
    # =========================================================================

    def log_glyph_verify_failed(
        self,
        book_id: str,
        line_id: int,
        page_num: int,
        verify_result: dict,
        engine_snapshots: dict,
    ) -> int:
        """
        决策点5: 记录字形验证失败

        字形验证引擎发现关键字段（药名、剂量、功效词）的字形不匹配。

        Args:
            book_id: 书籍ID
            line_id: 行ID
            page_num: 页码
            verify_result: 字形验证结果，格式为
                {
                    "field": "herb_name",
                    "expected": "当归",
                    "detected": "当阳",
                    "confidence": 0.45,
                    "verification_method": "structure_match"
                }
            engine_snapshots: 各引擎结果快照

        Returns:
            int: 新创建的决策记录ID
        """
        field = verify_result.get('field', 'unknown')
        critical_fields = {'herb_name', 'dosage', 'efficacy', 'toxicity', 'processing'}

        # 关键字段失败优先级更高
        priority = 'P0' if field in critical_fields else 'P1'

        reason_details = {
            'GLYPH_VERIFY_FAILED': {
                'field_type': field,
                'expected_text': verify_result.get('expected', ''),
                'detected_text': verify_result.get('detected', ''),
                'confidence': verify_result.get('confidence', 0.0),
                'verification_method': verify_result.get('verification_method', ''),
                'glyph_similarity': verify_result.get('glyph_similarity'),
                'is_critical_field': field in critical_fields,
                'suggested_correction': verify_result.get('suggested_correction'),
                'verification_details': verify_result.get('details', {}),
            }
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=line_id,
            para_id=None,
            page_num=page_num,
            reason_codes=['GLYPH_VERIFY_FAILED'],
            reason_details=reason_details,
            priority=priority,
            engine_snapshots=engine_snapshots,
        )

    # =========================================================================
    # 决策点 6/7: LLM超时
    # =========================================================================

    def log_llm_timeout(
        self,
        book_id: str,
        line_id: int,
        page_num: int,
        llm_type: str,
        timeout_sec: int,
        engine_snapshots: dict,
    ) -> int:
        """
        决策点6/7: 记录LLM超时

        本地或云端LLM在规定时间内未返回结果。

        Args:
            book_id: 书籍ID
            line_id: 行ID
            page_num: 页码
            llm_type: LLM类型，'local' 或 'cloud'
            timeout_sec: 超时时间（秒）
            engine_snapshots: 各引擎结果快照

        Returns:
            int: 新创建的决策记录ID
        """
        reason_code = (
            'LLM_LOCAL_TIMEOUT' if llm_type == 'local' else 'LLM_CLOUD_TIMEOUT'
        )

        reason_details = {
            reason_code: {
                'llm_type': llm_type,
                'timeout_sec': timeout_sec,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'fallback_strategy': 'use_vote_consensus' if llm_type == 'local' else 'retry_local',
                'engine_snapshots_summary': {
                    name: {
                        'text': (res.get('text', '')[:50] if isinstance(res, dict) else str(res)[:50]),
                        'confidence': res.get('confidence') if isinstance(res, dict) else None,
                    }
                    for name, res in (engine_snapshots or {}).items()
                },
            }
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=line_id,
            para_id=None,
            page_num=page_num,
            reason_codes=[reason_code],
            reason_details=reason_details,
            priority='P1',
            engine_snapshots=engine_snapshots,
        )

    # =========================================================================
    # 决策点 8: LLM输出解析失败
    # =========================================================================

    def log_llm_parse_error(
        self,
        book_id: str,
        line_id: int,
        page_num: int,
        llm_type: str,
        raw_output: str,
        error: str,
    ) -> int:
        """
        决策点8: 记录LLM输出解析失败

        LLM返回的输出格式不符合预期，无法进行JSON解析或结构提取。

        Args:
            book_id: 书籍ID
            line_id: 行ID
            page_num: 页码
            llm_type: LLM类型，'local' 或 'cloud'
            raw_output: LLM原始输出文本
            error: 解析错误信息

        Returns:
            int: 新创建的决策记录ID
        """
        reason_details = {
            'LLM_PARSE_ERROR': {
                'llm_type': llm_type,
                'parse_error': error,
                'raw_output_preview': raw_output[:500] if raw_output else '',
                'raw_output_length': len(raw_output) if raw_output else 0,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'expected_format': 'json_array_of_lines',
                'recovery_strategy': 'fallback_to_regex_extraction',
            }
        }

        llm_snapshots = {
            'llm_type': llm_type,
            'raw_output': raw_output,
            'parse_error': error,
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=line_id,
            para_id=None,
            page_num=page_num,
            reason_codes=['LLM_PARSE_ERROR'],
            reason_details=reason_details,
            priority='P1',
            llm_snapshots=llm_snapshots,
        )

    # =========================================================================
    # 决策点 9: 行数不守恒
    # =========================================================================

    def log_line_count_mismatch(
        self,
        book_id: str,
        para_id: int,
        page_num: int,
        input_lines: int,
        output_lines: int,
    ) -> int:
        """
        决策点9: 记录行数不守恒

        LLM处理后输出行数与输入行数不一致，可能存在丢行或重复。

        Args:
            book_id: 书籍ID
            para_id: 段落ID
            page_num: 页码
            input_lines: 输入行数
            output_lines: 输出行数

        Returns:
            int: 新创建的决策记录ID
        """
        diff = output_lines - input_lines
        severity = 'severe' if abs(diff) > 2 else 'moderate' if abs(diff) > 0 else 'minor'

        reason_details = {
            'LINE_COUNT_MISMATCH': {
                'input_lines': input_lines,
                'output_lines': output_lines,
                'difference': diff,
                'severity': severity,
                'expected_lines': input_lines,
                'actual_lines': output_lines,
                'risk': 'lines_lost' if diff < 0 else 'lines_added' if diff > 0 else 'none',
                'suggestion': (
                    '检查是否有行被LLM合并或丢弃'
                    if diff < 0
                    else '检查是否有重复行被LLM添加'
                ),
            }
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=None,
            para_id=para_id,
            page_num=page_num,
            reason_codes=['LINE_COUNT_MISMATCH'],
            reason_details=reason_details,
            priority='P1',
        )

    # =========================================================================
    # 决策点 10: 跨页拆分失败
    # =========================================================================

    def log_cross_page_split_fail(
        self,
        book_id: str,
        para_id: int,
        page_nums: list,
        merged_lines: int,
    ) -> int:
        """
        决策点10: 记录跨页拆分失败

        跨页段落拆分算法失败，段落被错误地合并或拆分。

        Args:
            book_id: 书籍ID
            para_id: 段落ID
            page_nums: 涉及的页码列表
            merged_lines: 合并后的总行数

        Returns:
            int: 新创建的决策记录ID
        """
        reason_details = {
            'CROSS_PAGE_SPLIT_FAIL': {
                'involved_pages': page_nums,
                'page_count': len(page_nums),
                'merged_lines': merged_lines,
                'split_strategy': 'paragraph_continuation',
                'failure_type': 'unsure_split_point',
                'pages_detail': [
                    {
                        'page_num': pn,
                        'role': 'start' if i == 0 else 'end' if i == len(page_nums) - 1 else 'middle',
                    }
                    for i, pn in enumerate(page_nums)
                ],
            }
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=None,
            para_id=para_id,
            page_num=page_nums[0] if page_nums else None,
            reason_codes=['CROSS_PAGE_SPLIT_FAIL'],
            reason_details=reason_details,
            priority='P1',
        )

    # =========================================================================
    # 决策点 11: 方剂提取异常
    # =========================================================================

    def log_formula_extract_fail(
        self,
        book_id: str,
        para_id: int,
        page_num: int,
        alert_type: str,
        detail: dict,
    ) -> int:
        """
        决策点11: 记录方剂提取异常

        从文本中方剂名称提取失败或提取结果异常。

        Args:
            book_id: 书籍ID
            para_id: 段落ID
            page_num: 页码
            alert_type: 告警类型，如 'no_formula_found' | 'ambiguous_match' | 'invalid_structure'
            detail: 详细信息

        Returns:
            int: 新创建的决策记录ID
        """
        reason_details = {
            'FORMULA_EXTRACT_FAIL': {
                'alert_type': alert_type,
                'source_text': detail.get('source_text', ''),
                'source_text_preview': detail.get('source_text', '')[:200],
                'extracted_formulas': detail.get('extracted_formulas', []),
                'expected_formulas': detail.get('expected_formulas', []),
                'extraction_method': detail.get('extraction_method', 'pattern_match'),
                'error_detail': detail.get('error', ''),
                'suggestion': detail.get(
                    'suggestion',
                    '请人工确认方剂名称是否正确提取',
                ),
            }
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=None,
            para_id=para_id,
            page_num=page_num,
            reason_codes=['FORMULA_EXTRACT_FAIL'],
            reason_details=reason_details,
            priority='P2',
        )

    # =========================================================================
    # 决策点 12: 方剂引用不一致
    # =========================================================================

    def log_formula_ref_mismatch(
        self,
        book_id: str,
        formula_id: int,
        ref_type: str,
        expected: str,
        actual: str,
    ) -> int:
        """
        决策点12: 记录方剂引用不一致

        方剂引用与正文中的方剂名称不匹配。

        Args:
            book_id: 书籍ID
            formula_id: 方剂ID
            ref_type: 引用类型，如 'name_mismatch' | 'dosage_mismatch' | 'herb_count_mismatch'
            expected: 期望的引用内容
            actual: 实际的引用内容

        Returns:
            int: 新创建的决策记录ID
        """
        reason_details = {
            'FORMULA_REF_MISMATCH': {
                'formula_id': formula_id,
                'ref_type': ref_type,
                'expected': expected,
                'actual': actual,
                'similarity': self._calculate_similarity(expected, actual),
                'mismatch_description': self._describe_mismatch(expected, actual, ref_type),
            }
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=None,
            para_id=None,
            page_num=None,
            reason_codes=['FORMULA_REF_MISMATCH'],
            reason_details=reason_details,
            priority='P2',
        )

    # =========================================================================
    # 决策点 13: 疑似漏字
    # =========================================================================

    def log_missing_char(
        self,
        book_id: str,
        line_id: int,
        page_num: int,
        detections: list,
    ) -> int:
        """
        决策点13: 记录疑似漏字

        缺失字符检测算法发现行中可能存在漏识别的字符。

        Args:
            book_id: 书籍ID
            line_id: 行ID
            page_num: 页码
            detections: 检测结果列表，每项为
                {
                    "position": 5,
                    "context": "...",
                    "confidence": 0.75,
                    "detection_method": "context_gap"
                }

        Returns:
            int: 新创建的决策记录ID
        """
        avg_confidence = (
            sum(d.get('confidence', 0) for d in detections) / len(detections)
            if detections
            else 0
        )

        reason_details = {
            'MISSING_CHAR_DETECTED': {
                'detection_count': len(detections),
                'avg_confidence': round(avg_confidence, 4),
                'detections': [
                    {
                        'position': d.get('position'),
                        'context_before': d.get('context_before', ''),
                        'context_after': d.get('context_after', ''),
                        'confidence': d.get('confidence', 0),
                        'detection_method': d.get('detection_method', 'unknown'),
                        'suggested_char': d.get('suggested_char'),
                    }
                    for d in detections
                ],
                'detection_methods_used': list(set(
                    d.get('detection_method', 'unknown') for d in detections
                )),
            }
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=line_id,
            para_id=None,
            page_num=page_num,
            reason_codes=['MISSING_CHAR_DETECTED'],
            reason_details=reason_details,
            priority='P2',
        )

    # =========================================================================
    # 决策点 14: 疑似粘连字
    # =========================================================================

    def log_extra_char(
        self,
        book_id: str,
        line_id: int,
        page_num: int,
        detections: list,
    ) -> int:
        """
        决策点14: 记录疑似粘连字

        额外字符检测算法发现行中可能存在粘连识别的字符。

        Args:
            book_id: 书籍ID
            line_id: 行ID
            page_num: 页码
            detections: 检测结果列表，每项为
                {
                    "position": 8,
                    "detected_sequence": "...",
                    "confidence": 0.68,
                    "detection_method": "width_anomaly"
                }

        Returns:
            int: 新创建的决策记录ID
        """
        avg_confidence = (
            sum(d.get('confidence', 0) for d in detections) / len(detections)
            if detections
            else 0
        )

        reason_details = {
            'EXTRA_CHAR_DETECTED': {
                'detection_count': len(detections),
                'avg_confidence': round(avg_confidence, 4),
                'detections': [
                    {
                        'position': d.get('position'),
                        'detected_sequence': d.get('detected_sequence', ''),
                        'expected_sequence': d.get('expected_sequence', ''),
                        'confidence': d.get('confidence', 0),
                        'detection_method': d.get('detection_method', 'unknown'),
                        'char_width_ratio': d.get('char_width_ratio'),
                    }
                    for d in detections
                ],
                'detection_methods_used': list(set(
                    d.get('detection_method', 'unknown') for d in detections
                )),
            }
        }

        return self._insert_decision(
            book_id=book_id,
            line_id=line_id,
            para_id=None,
            page_num=page_num,
            reason_codes=['EXTRA_CHAR_DETECTED'],
            reason_details=reason_details,
            priority='P2',
        )

    # =========================================================================
    # 决策点 15: 出版社低准确率
    # =========================================================================

    def log_publisher_low_accuracy(
        self,
        book_id: str,
        publisher: str,
        dispute_rate: float,
    ) -> list:
        """
        决策点15: 记录出版社低准确率

        该出版社历史书籍的OCR准确率统计偏低，建议加强校对。

        Args:
            book_id: 书籍ID
            publisher: 出版社名称
            dispute_rate: 分歧率（0-1之间）

        Returns:
            list: 创建的决策记录ID列表（可能涉及多个页面）
        """
        # 获取该出版社相关的待处理决策
        existing = self.get_pending_decisions(
            book_id=book_id,
            reason_code='PUBLISHER_LOW_ACCURACY',
        )

        if existing:
            # 已存在同类型的推送，更新统计
            decision_id = existing[0]['id']
            self._update_publisher_accuracy_decision(decision_id, dispute_rate)
            return [decision_id]

        reason_details = {
            'PUBLISHER_LOW_ACCURACY': {
                'publisher': publisher,
                'dispute_rate': round(dispute_rate, 4),
                'dispute_rate_percent': f"{dispute_rate * 100:.2f}%",
                'threshold': '15%',
                'exceeded': dispute_rate > 0.15,
                'severity': (
                    'high' if dispute_rate > 0.30
                    else 'medium' if dispute_rate > 0.15
                    else 'low'
                ),
                'suggestion': (
                    f'出版社 {publisher} 的历史分歧率为 {dispute_rate * 100:.2f}%，'
                    f'建议对此书加强校对力度'
                ),
                'affected_pages': [],  # 后续可补充
            }
        }

        decision_id = self._insert_decision(
            book_id=book_id,
            line_id=None,
            para_id=None,
            page_num=None,
            reason_codes=['PUBLISHER_LOW_ACCURACY'],
            reason_details=reason_details,
            priority='P3',
        )
        return [decision_id]

    # =========================================================================
    # 通用方法
    # =========================================================================

    def _insert_decision(
        self,
        book_id: str,
        line_id: Optional[int],
        para_id: Optional[int],
        page_num: Optional[int],
        reason_codes: List[str],
        reason_details: Dict[str, Any],
        priority: str,
        engine_snapshots: Optional[Dict[str, Any]] = None,
        llm_snapshots: Optional[Dict[str, Any]] = None,
        parent_decision_id: Optional[int] = None,
    ) -> int:
        """
        通用决策插入方法

        Args:
            book_id: 书籍ID
            line_id: 行ID（可选）
            para_id: 段落ID（可选）
            page_num: 页码（可选）
            reason_codes: 原因代码列表
            reason_details: 原因详细信息字典
            priority: 优先级 P0/P1/P2/P3
            engine_snapshots: 引擎快照（可选）
            llm_snapshots: LLM快照（可选）
            parent_decision_id: 父决策ID（可选）

        Returns:
            int: 新创建的决策记录ID
        """
        # 验证 reason_codes
        for code in reason_codes:
            if code not in self.REASON_CODES:
                raise ValueError(f"Unknown reason code: {code}")

        # 验证 priority
        valid_priorities = {'P0', 'P1', 'P2', 'P3'}
        if priority not in valid_priorities:
            raise ValueError(f"Invalid priority: {priority}. Must be one of {valid_priorities}")

        # 使用最高优先级（数值最小）
        effective_priority = priority
        if len(reason_codes) > 1:
            priority_order = {'P0': 0, 'P1': 1, 'P2': 2, 'P3': 3}
            effective_priority = min(
                reason_codes,
                key=lambda c: priority_order.get(
                    self.REASON_CODES.get(c, {}).get('priority', 'P3'), 3
                ),
            )
            effective_priority = self.REASON_CODES[effective_priority]['priority']

        # 构建决策链快照
        decision_chain = None
        if parent_decision_id:
            parent_chain = self._get_parent_chain(parent_decision_id)
            decision_chain = {
                'parent_decision_id': parent_decision_id,
                'chain_depth': len(parent_chain) + 1,
                'chain_ids': [d['id'] for d in parent_chain] + [parent_decision_id],
            }

        with self.runtime_db.get_cursor() as cursor:
            # 插入决策记录
            cursor.execute(
                """
                INSERT INTO PushDecisionLog (
                    book_id, line_id, para_id, page_num,
                    reason_codes, reason_details, priority, status,
                    engine_snapshots, llm_snapshots,
                    parent_decision_id, decision_chain,
                    created_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, 'pending',
                    %s, %s,
                    %s, %s,
                    NOW()
                )
                RETURNING id
                """,
                (
                    book_id,
                    line_id,
                    para_id,
                    page_num,
                    reason_codes,
                    json.dumps(reason_details),
                    effective_priority,
                    json.dumps(engine_snapshots) if engine_snapshots else None,
                    json.dumps(llm_snapshots) if llm_snapshots else None,
                    parent_decision_id,
                    json.dumps(decision_chain) if decision_chain else None,
                ),
            )
            result = cursor.fetchone()
            decision_id = result['id']

            # 记录决策链事件
            self._record_chain_event(
                cursor, decision_id, 'created',
                {'reason_codes': reason_codes, 'priority': effective_priority}
            )

            if engine_snapshots:
                self._record_chain_event(
                    cursor, decision_id, 'engine_snapshot',
                    {'engines': list(engine_snapshots.keys())}
                )

            logger.info(
                "Push decision logged: id=%d, book=%s, reasons=%s, priority=%s",
                decision_id, book_id, reason_codes, effective_priority
            )
            return decision_id

    def get_pending_decisions(
        self,
        book_id: Optional[str] = None,
        priority: Optional[str] = None,
        reason_code: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取待处理的推送决策列表

        Args:
            book_id: 按书籍ID过滤（可选）
            priority: 按优先级过滤（可选）
            reason_code: 按原因代码过滤（可选）

        Returns:
            List[Dict]: 待处理决策列表
        """
        conditions = ["status = 'pending'"]
        params = []

        if book_id:
            conditions.append("book_id = %s")
            params.append(book_id)
        if priority:
            conditions.append("priority = %s")
            params.append(priority)
        if reason_code:
            conditions.append("%s = ANY(reason_codes)")
            params.append(reason_code)

        where_clause = " AND ".join(conditions)

        with self.runtime_db.get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id, book_id, line_id, para_id, page_num,
                       reason_codes, reason_details, priority, status,
                       engine_snapshots, llm_snapshots,
                       parent_decision_id, decision_chain,
                       created_at
                FROM PushDecisionLog
                WHERE {where_clause}
                ORDER BY
                    CASE priority
                        WHEN 'P0' THEN 1
                        WHEN 'P1' THEN 2
                        WHEN 'P2' THEN 3
                        WHEN 'P3' THEN 4
                    END,
                    created_at ASC
                """,
                tuple(params),
            )
            return [dict(row) for row in cursor.fetchall()]

    def resolve_decision(
        self,
        decision_id: int,
        action: str,
        final_text: str,
        note: str,
        reviewer_id: int,
    ) -> bool:
        """
        解决推送决策

        Args:
            decision_id: 决策记录ID
            action: 处理动作（accept/reject/modify/escalate/auto_resolve）
            final_text: 校对后的最终文本
            note: 审校备注
            reviewer_id: 审校者用户ID

        Returns:
            bool: 是否更新成功
        """
        valid_actions = {'accept', 'reject', 'modify', 'escalate', 'auto_resolve'}
        if action not in valid_actions:
            raise ValueError(f"Invalid action: {action}. Must be one of {valid_actions}")

        status_map = {
            'accept': 'resolved',
            'reject': 'rejected',
            'modify': 'resolved',
            'escalate': 'escalated',
            'auto_resolve': 'auto_resolved',
        }
        status = status_map[action]

        with self.runtime_db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE PushDecisionLog
                SET action = %s,
                    final_text = %s,
                    note = %s,
                    reviewer_id = %s,
                    status = %s,
                    resolved_at = NOW(),
                    resolution_time_sec = EXTRACT(EPOCH FROM (NOW() - created_at))::INTEGER
                WHERE id = %s
                RETURNING id
                """,
                (action, final_text, note, reviewer_id, status, decision_id),
            )
            result = cursor.fetchone()

            if result:
                # 记录链路事件
                self._record_chain_event(
                    cursor, decision_id, 'human_review_completed',
                    {
                        'action': action,
                        'reviewer_id': reviewer_id,
                        'had_note': bool(note),
                        'had_final_text': bool(final_text),
                    }
                )
                logger.info(
                    "Push decision resolved: id=%d, action=%s, reviewer=%d",
                    decision_id, action, reviewer_id,
                )
                return True
            return False

    def get_decision_stats(
        self,
        book_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取推送决策统计信息

        Args:
            book_id: 按书籍ID过滤（可选）

        Returns:
            Dict: 统计信息字典
        """
        conditions = []
        params = []

        if book_id:
            conditions.append("book_id = %s")
            params.append(book_id)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.runtime_db.get_cursor() as cursor:
            # 总体统计
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) as total_decisions,
                    COUNT(*) FILTER (WHERE status = 'pending') as pending_count,
                    COUNT(*) FILTER (WHERE status = 'resolved') as resolved_count,
                    COUNT(*) FILTER (WHERE status = 'rejected') as rejected_count,
                    COUNT(*) FILTER (WHERE status = 'auto_resolved') as auto_resolved_count,
                    COUNT(*) FILTER (WHERE status = 'escalated') as escalated_count,
                    AVG(resolution_time_sec) FILTER (WHERE resolution_time_sec IS NOT NULL)
                        as avg_resolution_time_sec
                FROM PushDecisionLog
                {where_clause}
                """,
                tuple(params),
            )
            overall = dict(cursor.fetchone())

            # 按优先级统计
            cursor.execute(
                f"""
                SELECT
                    priority,
                    COUNT(*) as total_count,
                    COUNT(*) FILTER (WHERE status = 'pending') as pending_count,
                    COUNT(*) FILTER (WHERE status IN ('resolved', 'auto_resolved')) as resolved_count
                FROM PushDecisionLog
                {where_clause}
                GROUP BY priority
                ORDER BY
                    CASE priority
                        WHEN 'P0' THEN 1
                        WHEN 'P1' THEN 2
                        WHEN 'P2' THEN 3
                        WHEN 'P3' THEN 4
                    END
                """,
                tuple(params),
            )
            by_priority = [dict(row) for row in cursor.fetchall()]

            # 按原因统计（展开 reason_codes 数组）
            cursor.execute(
                f"""
                SELECT
                    reason_code,
                    COUNT(*) as total_count,
                    COUNT(*) FILTER (WHERE status = 'pending') as pending_count,
                    COUNT(*) FILTER (WHERE status IN ('resolved', 'auto_resolved')) as resolved_count
                FROM PushDecisionLog, UNNEST(reason_codes) as reason_code
                {where_clause}
                GROUP BY reason_code
                ORDER BY total_count DESC
                """,
                tuple(params),
            )
            by_reason = [dict(row) for row in cursor.fetchall()]

            # 按状态统计
            cursor.execute(
                f"""
                SELECT
                    status,
                    COUNT(*) as count
                FROM PushDecisionLog
                {where_clause}
                GROUP BY status
                ORDER BY count DESC
                """,
                tuple(params),
            )
            by_status = [dict(row) for row in cursor.fetchall()]

            return {
                'total_decisions': overall.get('total_decisions', 0) or 0,
                'pending_count': overall.get('pending_count', 0) or 0,
                'resolved_count': overall.get('resolved_count', 0) or 0,
                'rejected_count': overall.get('rejected_count', 0) or 0,
                'auto_resolved_count': overall.get('auto_resolved_count', 0) or 0,
                'escalated_count': overall.get('escalated_count', 0) or 0,
                'avg_resolution_time_sec': overall.get('avg_resolution_time_sec'),
                'by_priority': by_priority,
                'by_reason': by_reason,
                'by_status': by_status,
            }

    def get_decision_chain(self, decision_id: int) -> Dict[str, Any]:
        """
        获取决策的完整链路信息

        Args:
            decision_id: 决策记录ID

        Returns:
            Dict: 包含当前决策、父决策链、子决策和事件的信息
        """
        with self.runtime_db.get_cursor() as cursor:
            # 获取当前决策
            cursor.execute(
                """
                SELECT * FROM PushDecisionLog WHERE id = %s
                """,
                (decision_id,),
            )
            current = cursor.fetchone()
            if not current:
                raise ValueError(f"Decision not found: {decision_id}")

            current_dict = dict(current)

            # 获取父决策链
            parent_chain = []
            parent_id = current_dict.get('parent_decision_id')
            visited = {decision_id}  # 防止循环
            while parent_id and parent_id not in visited:
                visited.add(parent_id)
                cursor.execute(
                    "SELECT * FROM PushDecisionLog WHERE id = %s",
                    (parent_id,),
                )
                parent = cursor.fetchone()
                if parent:
                    parent_dict = dict(parent)
                    parent_chain.insert(0, parent_dict)
                    parent_id = parent_dict.get('parent_decision_id')
                else:
                    break

            # 获取子决策
            cursor.execute(
                "SELECT * FROM PushDecisionLog WHERE parent_decision_id = %s ORDER BY created_at",
                (decision_id,),
            )
            child_decisions = [dict(row) for row in cursor.fetchall()]

            # 获取决策事件
            cursor.execute(
                """
                SELECT id, decision_id, event_type, event_data, created_at
                FROM DecisionChainEvent
                WHERE decision_id = %s
                ORDER BY created_at
                """,
                (decision_id,),
            )
            events = [dict(row) for row in cursor.fetchall()]

            return {
                'decision': current_dict,
                'parent_chain': parent_chain,
                'child_decisions': child_decisions,
                'events': events,
            }

    # =========================================================================
    # 辅助方法
    # =========================================================================

    def _record_chain_event(
        self,
        cursor: object,
        decision_id: int,
        event_type: str,
        event_data: Dict[str, Any],
    ) -> None:
        """记录决策链事件"""
        cursor.execute(
            """
            INSERT INTO DecisionChainEvent (decision_id, event_type, event_data, created_at)
            VALUES (%s, %s, %s, NOW())
            RETURNING id
            """,
            (decision_id, event_type, json.dumps(event_data)),
        )

    def _get_parent_chain(
        self,
        parent_decision_id: int,
    ) -> List[Dict[str, Any]]:
        """获取父决策链"""
        chain = []
        current_id = parent_decision_id
        visited = set()

        with self.runtime_db.get_cursor() as cursor:
            while current_id and current_id not in visited:
                visited.add(current_id)
                cursor.execute(
                    "SELECT * FROM PushDecisionLog WHERE id = %s",
                    (current_id,),
                )
                result = cursor.fetchone()
                if result:
                    result_dict = dict(result)
                    chain.insert(0, result_dict)
                    current_id = result_dict.get('parent_decision_id')
                else:
                    break

        return chain

    def _update_publisher_accuracy_decision(
        self,
        decision_id: int,
        new_dispute_rate: float,
    ) -> None:
        """更新出版社准确率决策的统计数据"""
        with self.runtime_db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE PushDecisionLog
                SET reason_details = jsonb_set(
                    reason_details,
                    '{PUBLISHER_LOW_ACCURACY,dispute_rate}',
                    to_jsonb(%s)
                ),
                reason_details = jsonb_set(
                    reason_details,
                    '{PUBLISHER_LOW_ACCURACY,dispute_rate_percent}'::text[],
                    to_jsonb(%s)
                ),
                reason_details = jsonb_set(
                    reason_details,
                    '{PUBLISHER_LOW_ACCURACY,update_count}'::text[],
                    COALESCE(reason_details->'PUBLISHER_LOW_ACCURACY'->>'update_count', '0')::int + 1
                ),
                created_at = NOW()
                WHERE id = %s
                """,
                (new_dispute_rate, f"{new_dispute_rate * 100:.2f}%", decision_id),
            )

    @staticmethod
    def _count_negations(text: str) -> int:
        """统计文本中的否定词数量"""
        negations = ['不', '无', '非', '未', '否', '莫', '勿', '别', '没有', '不曾']
        count = 0
        for neg in negations:
            count += text.count(neg)
        return count

    @staticmethod
    def _extract_context(text: str, keyword: str, window: int = 5) -> str:
        """提取关键词的上下文"""
        pos = text.find(keyword)
        if pos == -1:
            return ''
        start = max(0, pos - window)
        end = min(len(text), pos + len(keyword) + window)
        return text[start:end]

    @staticmethod
    def _calculate_similarity(a: str, b: str) -> float:
        """计算两个字符串的相似度（简化版）"""
        if not a or not b:
            return 0.0
        # 使用最长公共子序列的简化版本
        set_a = set(a)
        set_b = set(b)
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return round(intersection / union, 4) if union > 0 else 0.0

    @staticmethod
    def _describe_mismatch(expected: str, actual: str, ref_type: str) -> str:
        """描述不匹配的类型"""
        descriptions = {
            'name_mismatch': f'方剂名称不匹配: 期望"{expected}",实际"{actual}"',
            'dosage_mismatch': f'方剂剂量不匹配: 期望"{expected}",实际"{actual}"',
            'herb_count_mismatch': f'药材数量不匹配: 期望"{expected}",实际"{actual}"',
        }
        return descriptions.get(ref_type, f'引用不一致: 期望"{expected}",实际"{actual}"')
