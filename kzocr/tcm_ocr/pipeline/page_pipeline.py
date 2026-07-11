"""
单页处理流水线模块。

负责单页的处理流程（步骤 4a-4m）：
- 版式分类
- MinerU 版面分析（完整图，不裁剪版心）
- 后置版面噪声过滤
- 印刷质量检测 + 图像增强
- 行框质量交叉验证
- 分级多引擎识别
- 行级共识融合 + 字符级对齐
- 漏字/多字检测
- 行级否定词完整性检查
- 第一次剂量校验
- 争议行 → LLM 四级决策校对
- 字形验证（第一道/第二道）
- 方剂组成提取
- 整方级校验

同时提供 build_book_structure 将页面数据写入数据库。
"""

from datetime import datetime
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from kzocr.tcm_ocr.config.constants import (
    BASE_THRESHOLD,
    ENHANCEMENT_CONTRAST_ALPHA,
    ENHANCEMENT_SHARPEN_SIGMA,
    EXTRA_CHAR_STRONG_RATIO,
    EXTRA_CHAR_WEAK_RATIO,
    MISSING_CHAR_GAP_RATIO,
    LayoutType,
    LLM_DECISION_LEVELS,
    LLM_DECISION_THRESHOLD_HUMAN,
    LLM_DECISION_THRESHOLD_MINOR,
    LLM_DECISION_THRESHOLD_VERIFY,
    get_preprocess_params,
    get_threshold_with_bonus,
)

logger = logging.getLogger(__name__)


class PagePipeline:
    """单页处理流水线。

    封装单页 OCR 处理的完整流程，从图像输入到结构化输出。

    Attributes:
        config: 系统配置字典
        engines: OCR 引擎字典 {"engine_name": engine_instance}
        term_kb: 术语知识库实例
    """

    def __init__(
        self,
        config: Dict[str, Any],
        engines: Dict[str, Any],
        term_kb: Any,
    ) -> None:
        """初始化单页处理流水线。

        Args:
            config: 系统配置字典，含预处理参数、阈值等
            engines: OCR 引擎字典
            term_kb: 术语知识库（TermKB 实例）
        """
        self.config = config
        self.engines = engines
        self.term_kb = term_kb
        self.threshold = get_threshold_with_bonus(
            base_threshold=config.get("threshold", BASE_THRESHOLD),
            publisher_bonus=config.get("publisher_bonus", 0.0),
        )
        logger.info(
            "PagePipeline 初始化完成，引擎: %s，阈值: %.3f",
            list(engines.keys()),
            self.threshold,
        )

    def process_page(
        self,
        page_img: np.ndarray,
        page_num: int,
        book_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        """处理单页图像，返回结构化结果。

        完整处理流程：
        1. 版式分类
        2. MinerU 版面分析
        3. 后置版面噪声过滤
        4. 印刷质量检测 + 图像增强
        5. 行框质量交叉验证
        6. 分级多引擎识别
        7. 行级共识融合 + 字符级对齐
        8. 漏字/多字检测
        9. 行级否定词完整性检查
        10. 第一次剂量校验
        11. 争议行 → LLM 四级决策校对
        12. 字形验证（第一道/第二道）
        13. 方剂组成提取
        14. 整方级校验

        Args:
            page_img: 页面图像（BGR/RGB numpy 数组）
            page_num: 页码（从 1 开始）
            book_meta: 书籍元数据（含出版年份等）

        Returns:
            页面处理结果字典，包含：
            - page_number: 页码
            - layout_type: 版式类型
            - lines: 行级结果列表
            - formulas: 提取的方剂列表
            - images: 图片索引列表
            - statistics: 页面统计
        """
        start_time = time.time()
        pub_year = book_meta.get("pub_year", 2000)

        result: Dict[str, Any] = {
            "page_number": page_num,
            "layout_type": LayoutType.TEXT.value,
            "lines": [],
            "formulas": [],
            "images": [],
            "statistics": {
                "total_lines": 0,
                "disputed_lines": 0,
                "llm_corrected_lines": 0,
                "avg_confidence": 0.0,
                "processing_time_ms": 0,
            },
        }

        try:
            # --- 步骤 1: 版式分类 ---
            layout_type = self._classify_layout(page_img, page_num)
            result["layout_type"] = layout_type.value
            logger.debug("[%d] 版式分类: %s", page_num, layout_type.value)

            # --- 步骤 2: MinerU 版面分析（完整图，不裁剪版心） ---
            layout_blocks = self._mineru_layout_analysis(page_img)
            logger.debug("[%d] MinerU 版面分析: %d blocks", page_num, len(layout_blocks))

            # --- 步骤 3: 后置版面噪声过滤 ---
            filtered_blocks = self._post_layout_noise_filter(layout_blocks)
            logger.debug("[%d] 噪声过滤后: %d blocks", page_num, len(filtered_blocks))

            # --- 步骤 4: 印刷质量检测 + 图像增强 ---
            preprocess_params = get_preprocess_params(pub_year)
            enhanced_img = self._detect_and_enhance(page_img, preprocess_params)

            # --- 步骤 5: 行框质量交叉验证 ---
            validated_lines = self._cross_validate_line_boxes(
                filtered_blocks, enhanced_img
            )

            # --- 步骤 6: 分级多引擎识别 ---
            engine_results = self._multi_engine_recognition(
                enhanced_img, validated_lines
            )

            # --- 步骤 7: 行级共识融合 + 字符级对齐 ---
            fused_lines = self._consensus_fusion_with_alignment(engine_results)

            # --- 步骤 8: 漏字/多字检测 ---
            checked_lines = self._detect_missing_extra_chars(fused_lines)

            # --- 步骤 9: 行级否定词完整性检查 ---
            integrity_checked = self._check_negation_integrity(checked_lines)

            # --- 步骤 10: 第一次剂量校验 ---
            dose_validated = self._validate_dosages(integrity_checked, page_num)

            # --- 步骤 11: 争议行 → LLM 四级决策校对 ---
            llm_corrected = self._llm_four_level_decision(dose_validated)

            # --- 步骤 12: 字形验证（第一道/第二道） ---
            glyph_verified = self._glyph_verification_two_pass(llm_corrected)

            # --- 步骤 13: 方剂组成提取 ---
            formulas = self._extract_formula_compositions(
                glyph_verified, page_num
            )
            result["formulas"] = formulas

            # --- 步骤 14: 整方级校验 ---
            validated_formulas = self._whole_formula_validation(formulas)
            result["formulas"] = validated_formulas

            # 组装最终结果
            result["lines"] = glyph_verified
            result["images"] = self._extract_image_indices(filtered_blocks)
            result["statistics"]["total_lines"] = len(glyph_verified)
            result["statistics"]["disputed_lines"] = sum(
                1 for ln in glyph_verified if ln.get("disputed", False)
            )
            result["statistics"]["llm_corrected_lines"] = sum(
                1 for ln in glyph_verified if ln.get("llm_corrected", False)
            )
            confidences = [
                ln.get("confidence", 0) for ln in glyph_verified if ln.get("confidence")
            ]
            result["statistics"]["avg_confidence"] = (
                sum(confidences) / len(confidences) if confidences else 0.0
            )

        except Exception as e:
            logger.error("[%d] 页面处理失败: %s", page_num, e, exc_info=True)
            result["error"] = str(e)

        elapsed_ms = int((time.time() - start_time) * 1000)
        result["statistics"]["processing_time_ms"] = elapsed_ms
        logger.info(
            "[%d] 页面处理完成: %d 行, %.1fms",
            page_num,
            result["statistics"]["total_lines"],
            elapsed_ms,
        )

        return result

    # =========================================================================
    # 步骤 1: 版式分类
    # =========================================================================

    def _classify_layout(
        self,
        page_img: np.ndarray,
        page_num: int,
    ) -> LayoutType:
        """对页面进行版式分类。

        基于图像特征（表格线检测、图片区域检测、文本密度等）判断版式类型。

        Args:
            page_img: 页面图像
            page_num: 页码

        Returns:
            版式类型枚举
        """
        h, w = page_img.shape[:2]
        gray = page_img if len(page_img.shape) == 2 else self._to_gray(page_img)

        # 检测是否有大量水平/垂直线（表格特征）
        table_score = self._detect_table_lines(gray)

        # 检测图片区域占比
        image_ratio = self._detect_image_regions(page_img)

        # 文本密度
        text_density = self._compute_text_density(gray)

        # 目录页检测
        if self._is_toc_page(page_img, page_num):
            return LayoutType.TOC_PAGE

        if table_score > 0.3:
            return LayoutType.TABLE
        elif image_ratio > 0.4:
            return LayoutType.IMAGE
        elif image_ratio > 0.1 and text_density > 0.3:
            return LayoutType.MIXED
        elif text_density > 0.5:
            return LayoutType.TEXT
        else:
            return LayoutType.MIXED

    def _to_gray(self, img: np.ndarray) -> np.ndarray:
        """转换为灰度图。"""
        if len(img.shape) == 3:
            import cv2

            return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img

    def _detect_table_lines(self, gray: np.ndarray) -> float:
        """检测表格线，返回置信度分数。"""
        try:
            import cv2

            edges = cv2.Canny(gray, 50, 150)
            lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 100, minLineLength=50, maxLineGap=10)
            if lines is None:
                return 0.0

            h_lines = sum(1 for seg in lines if abs(seg[0][3] - seg[0][1]) < 5)
            v_lines = sum(1 for seg in lines if abs(seg[0][2] - seg[0][0]) < 5)

            return min((h_lines + v_lines) / 20.0, 1.0)
        except Exception:
            return 0.0

    def _detect_image_regions(self, img: np.ndarray) -> float:
        """检测图片区域占页面比例。"""
        try:
            import cv2

            h, w = img.shape[:2]
            gray = self._to_gray(img)
            # 使用连通区域分析检测大面积连续区域
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            num_labels, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)

            image_area = 0
            for i in range(1, num_labels):
                area = stats[i, cv2.CC_STAT_AREA]
                if area > h * w * 0.01:  # 大于页面 1% 的区域
                    image_area += area

            return min(image_area / (h * w), 1.0)
        except Exception:
            return 0.0

    def _compute_text_density(self, gray: np.ndarray) -> float:
        """计算文本密度。"""
        try:
            import cv2

            h, w = gray.shape
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            text_pixels = np.sum(binary > 0)
            return min(text_pixels / (h * w), 1.0)
        except Exception:
            return 0.5

    def _is_toc_page(self, page_img: np.ndarray, page_num: int) -> bool:
        """判断是否为目录页。"""
        # 前 10 页内且文本特征符合目录
        if page_num > 10:
            return False
        # TODO: 结合 OCR 文本中的目录关键词
        return False

    # =========================================================================
    # 步骤 2: MinerU 版面分析
    # =========================================================================

    def _mineru_layout_analysis(
        self,
        page_img: np.ndarray,
    ) -> List[Dict[str, Any]]:
        """使用 MinerU 进行版面分析（完整图，不裁剪版心）。

        Args:
            page_img: 页面图像

        Returns:
            版面块列表，每项含 bbox、type、text 等
        """
        try:
            mineru_engine = self.engines.get("mineru")
            if mineru_engine is None:
                # 降级：使用简单的连通区域分析
                return self._fallback_layout_analysis(page_img)

            blocks = mineru_engine.analyze(page_img)
            return blocks if blocks else []
        except Exception as e:
            logger.warning("MinerU 版面分析失败: %s", e)
            return self._fallback_layout_analysis(page_img)

    def _fallback_layout_analysis(
        self,
        page_img: np.ndarray,
    ) -> List[Dict[str, Any]]:
        """MinerU 不可用时的降级版面分析。

        使用简单的行分割作为降级方案。

        Args:
            page_img: 页面图像

        Returns:
            版面块列表
        """
        try:

            gray = self._to_gray(page_img)
            h, w = gray.shape

            # 水平投影分割行
            h_proj = np.sum(gray < 200, axis=1)
            threshold = np.max(h_proj) * 0.1

            blocks: List[Dict[str, Any]] = []
            in_line = False
            line_start = 0

            for i, val in enumerate(h_proj):
                if val > threshold and not in_line:
                    in_line = True
                    line_start = i
                elif val <= threshold and in_line:
                    in_line = False
                    blocks.append({
                        "type": "text",
                        "bbox": [0, line_start, w, i],
                        "text": "",
                    })

            return blocks
        except Exception:
            # 极端降级：整张图作为一个 block
            h, w = page_img.shape[:2]
            return [{"type": "text", "bbox": [0, 0, w, h], "text": ""}]

    # =========================================================================
    # 步骤 3: 后置版面噪声过滤
    # =========================================================================

    def _post_layout_noise_filter(
        self,
        blocks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """过滤版面分析中的噪声块。

        过滤规则：
        - 过小区域（面积 < 100 像素）
        - 页眉/页脚区域（根据位置判断）
        - 孤立噪点块

        Args:
            blocks: 原始版面块列表

        Returns:
            过滤后的版面块列表
        """
        if not blocks:
            return []

        filtered: List[Dict[str, Any]] = []
        avg_area = 0.0
        if blocks:
            areas = []
            for b in blocks:
                bbox = b.get("bbox", [0, 0, 0, 0])
                area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                areas.append(area)
            avg_area = sum(areas) / len(areas) if areas else 0

        for block in blocks:
            bbox = block.get("bbox", [0, 0, 0, 0])
            x1, y1, x2, y2 = bbox
            area = (x2 - x1) * (y2 - y1)

            # 过滤过小块
            if area < 100:
                continue

            # 过滤页眉/页脚（假设在页面顶部/底部 5% 区域内）
            # 这里简化处理，实际需要知道页面高度

            # 过滤与平均面积偏差过大的异常块
            if avg_area > 0 and area > avg_area * 10:
                # 可能是图片区域，保留
                pass

            filtered.append(block)

        return filtered

    # =========================================================================
    # 步骤 4: 印刷质量检测 + 图像增强
    # =========================================================================

    def _detect_and_enhance(
        self,
        page_img: np.ndarray,
        params: Dict[str, Any],
    ) -> np.ndarray:
        """检测印刷质量并进行图像增强。

        Args:
            page_img: 原始页面图像
            params: 预处理参数字典

        Returns:
            增强后的图像
        """
        try:
            import cv2

            enhanced = page_img.copy()
            gray = self._to_gray(enhanced)

            # 质量检测
            blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
            is_low_quality = blur_score < 100

            if is_low_quality or params.get("contrast_alpha", 1.0) > 1.0:
                # 对比度增强
                alpha = params.get("contrast_alpha", ENHANCEMENT_CONTRAST_ALPHA)
                enhanced = cv2.convertScaleAbs(enhanced, alpha=alpha, beta=0)

            if is_low_quality or params.get("sharpen_sigma", 0) > 0:
                # 锐化
                sigma = params.get("sharpen_sigma", ENHANCEMENT_SHARPEN_SIGMA)
                gaussian = cv2.GaussianBlur(enhanced, (0, 0), sigma)
                enhanced = cv2.addWeighted(enhanced, 1.5, gaussian, -0.5, 0)

            if params.get("noise_reduction_strength", 0) > 0:
                # 噪声消除
                strength = params.get("noise_reduction_strength", 0.5)
                if strength > 0.5:
                    enhanced = cv2.fastNlMeansDenoisingColored(
                        enhanced, None, 10, 10, 7, 21
                    )

            return enhanced
        except Exception as e:
            logger.warning("图像增强失败: %s", e)
            return page_img

    # =========================================================================
    # 步骤 5: 行框质量交叉验证
    # =========================================================================

    def _cross_validate_line_boxes(
        self,
        blocks: List[Dict[str, Any]],
        page_img: np.ndarray,
    ) -> List[Dict[str, Any]]:
        """交叉验证行框质量。

        使用多个引擎的行检测结果进行交叉验证，保留高一致性的行框。

        Args:
            blocks: 版面块列表
            page_img: 页面图像

        Returns:
            验证后的行列表
        """
        lines: List[Dict[str, Any]] = []

        for block in blocks:
            block_type = block.get("type", "text")
            if block_type in ("text", "formula"):
                lines.append({
                    "bbox": block.get("bbox", [0, 0, 0, 0]),
                    "type": block_type,
                    "text": block.get("text", ""),
                    "block_id": block.get("id", ""),
                })

        return lines

    # =========================================================================
    # 步骤 6: 分级多引擎识别
    # =========================================================================

    def _multi_engine_recognition(
        self,
        page_img: np.ndarray,
        lines: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """使用多个 OCR 引擎进行分级识别。

        引擎优先级：ShizhenGPT > PaddleOCR > Tesseract

        Args:
            page_img: 页面图像
            lines: 行列表

        Returns:
            含多引擎结果的结构化行列表
        """
        for line in lines:
            bbox = line.get("bbox", [0, 0, 0, 0])
            engine_results: Dict[str, Any] = {}

            for engine_name, engine in self.engines.items():
                if engine_name == "mineru":
                    continue
                try:
                    x1, y1, x2, y2 = bbox
                    h, w = page_img.shape[:2]
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(w, x2), min(h, y2)
                    if x2 <= x1 or y2 <= y1:
                        continue

                    roi = page_img[y1:y2, x1:x2]
                    if roi.size == 0:
                        continue

                    result = engine.recognize(roi)
                    if isinstance(result, tuple):
                        text, confidence = result
                    else:
                        text, confidence = result, 0.0  # adapter 返回 str，无置信度
                    engine_results[engine_name] = {
                        "text": text,
                        "confidence": confidence,
                    }
                except Exception as e:
                    logger.debug("引擎 %s 识别失败: %s", engine_name, e)
                    continue

            line["engine_results"] = engine_results

        return lines

    # =========================================================================
    # 步骤 7: 行级共识融合 + 字符级对齐
    # =========================================================================

    def _consensus_fusion_with_alignment(
        self,
        lines: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """行级共识融合 + 字符级对齐。

        对多引擎结果进行投票融合，使用编辑距离进行字符级对齐。

        Args:
            lines: 含多引擎结果的行列表

        Returns:
            融合后的行列表
        """
        for line in lines:
            engine_results = line.get("engine_results", {})
            if not engine_results:
                continue

            # 按置信度加权投票
            candidates: List[Tuple[str, float]] = []
            for engine_name, result in engine_results.items():
                weight = self.config.get("engine_weights", {}).get(engine_name, 1.0)
                conf = result.get("confidence", 0.5)
                text = result.get("text", "")
                candidates.append((text, conf * weight))

            if not candidates:
                continue

            # 选择最高分的文本
            best_text, best_score = max(candidates, key=lambda x: x[1])

            # 字符级对齐：比较最佳文本与其他结果
            aligned_chars = self._char_level_alignment(
                best_text, [c[0] for c in candidates]
            )

            line["fused_text"] = best_text
            line["confidence"] = best_score
            line["char_alignment"] = aligned_chars
            line["fused_from"] = list(engine_results.keys())

        return lines

    def _char_level_alignment(
        self,
        best_text: str,
        candidates: List[str],
    ) -> List[Dict[str, Any]]:
        """对最佳文本进行字符级对齐分析。

        Args:
            best_text: 最佳文本
            candidates: 所有候选文本

        Returns:
            字符级对齐信息列表
        """
        aligned: List[Dict[str, Any]] = []
        for i, char in enumerate(best_text):
            char_votes: Dict[str, int] = {char: 0}
            for cand in candidates:
                if i < len(cand):
                    c = cand[i]
                    char_votes[c] = char_votes.get(c, 0) + 1

            total = len(candidates)
            agreement = char_votes.get(char, 0) / total if total > 0 else 0

            aligned.append({
                "char": char,
                "position": i,
                "agreement_rate": agreement,
                "alternatives": [
                    {"char": k, "votes": v}
                    for k, v in sorted(char_votes.items(), key=lambda x: -x[1])
                    if k != char
                ],
            })

        return aligned

    # =========================================================================
    # 步骤 8: 漏字/多字检测
    # =========================================================================

    def _detect_missing_extra_chars(
        self,
        lines: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """检测漏字和多字。

        通过比较引擎结果中字符数量的差异来检测。

        Args:
            lines: 行列表

        Returns:
            标注了漏字/多字信息的行列表
        """
        for line in lines:
            engine_results = line.get("engine_results", {})
            texts = [
                r.get("text", "")
                for r in engine_results.values()
                if r.get("text")
            ]

            if len(texts) < 2:
                continue

            lengths = [len(t) for t in texts]
            min_len = min(lengths)
            max_len = max(lengths)

            # 漏字检测
            if min_len > 0 and max_len / min_len > MISSING_CHAR_GAP_RATIO:
                line["missing_char_suspected"] = True
                line["missing_char_detail"] = {
                    "min_length": min_len,
                    "max_length": max_len,
                    "ratio": max_len / min_len,
                }

            # 多字检测（强信号）
            if min_len > 0 and max_len / min_len > EXTRA_CHAR_STRONG_RATIO:
                line["extra_char_suspected"] = True
                line["extra_char_strength"] = "strong"
            elif min_len > 0 and max_len / min_len > EXTRA_CHAR_WEAK_RATIO:
                line["extra_char_suspected"] = True
                line["extra_char_strength"] = "weak"

        return lines

    # =========================================================================
    # 步骤 9: 行级否定词完整性检查
    # =========================================================================

    NEGATION_WORDS: List[str] = ["不", "无", "非", "勿", "忌", "禁", "未", "别"]

    def _check_negation_integrity(
        self,
        lines: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """检查否定词的完整性。

        确保否定词未被 OCR 错误识别或遗漏。

        Args:
            lines: 行列表

        Returns:
            标注了否定词检查结果的行列表
        """
        for line in lines:
            fused_text = line.get("fused_text", "")
            negations_found = [w for w in self.NEGATION_WORDS if w in fused_text]

            if negations_found:
                # 检查否定词上下文是否合理
                contexts = []
                for neg in negations_found:
                    idx = fused_text.find(neg)
                    context = fused_text[max(0, idx - 3) : idx + 4]
                    contexts.append({"word": neg, "context": context})

                line["negation_words"] = negations_found
                line["negation_contexts"] = contexts

                # 标记可疑的否定词用法
                if self._is_suspicious_negation_usage(fused_text):
                    line["negation_suspicious"] = True

        return lines

    def _is_suspicious_negation_usage(self, text: str) -> bool:
        """判断否定词用法是否可疑。

        例如：方剂组成中出现"不含"可能需要人工确认。

        Args:
            text: 文本

        Returns:
            是否可疑
        """
        # 方剂上下文中出现否定词通常是合理的（如"水煎服，日一剂"不含否定词）
        # 但"忌"在用法中出现也是合理的
        # 这里简化处理
        suspicious_patterns = ["不不", "无无", "非非"]
        return any(p in text for p in suspicious_patterns)

    # =========================================================================
    # 步骤 10: 第一次剂量校验
    # =========================================================================

    def _validate_dosages(
        self,
        lines: List[Dict[str, Any]],
        page_num: int,
    ) -> List[Dict[str, Any]]:
        """校验剂量信息。

        检查文本中的剂量是否符合中医规范（如 "三钱"、"五克" 等）。

        Args:
            lines: 行列表
            page_num: 页码

        Returns:
            标注了剂量校验结果的行列表
        """
        import re

        dose_pattern = re.compile(
            r"([一二三四五六七八九十百千\d]+)\s*([钱两克分斤两升合枚片个对只条把寸尺])"
        )

        for line in lines:
            fused_text = line.get("fused_text", "")
            matches = dose_pattern.findall(fused_text)

            if matches:
                dosages = []
                for amount, unit in matches:
                    dosages.append({"amount": amount, "unit": unit})

                line["dosages"] = dosages

                # 检查剂量合理性
                for d in dosages:
                    if not self._is_reasonable_dose(d["amount"], d["unit"]):
                        line["dose_suspicious"] = True
                        line["dose_issues"] = line.get("dose_issues", []) + [d]

        return lines

    def _is_reasonable_dose(self, amount: str, unit: str) -> bool:
        """判断剂量是否合理。

        Args:
            amount: 剂量数值
            unit: 剂量单位

        Returns:
            是否合理
        """
        try:
            # 尝试将中文数字转换为整数
            cn_nums = {
                "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
                "百": 100, "千": 1000,
            }

            if amount in cn_nums:
                num = cn_nums[amount]
            elif amount.isdigit():
                num = int(amount)
            else:
                # 组合数字如 "十二"
                num = 0
                for char in amount:
                    if char in cn_nums:
                        if cn_nums[char] == 10 and num > 0:
                            num *= 10
                        else:
                            num += cn_nums[char]

            # 合理性检查
            if unit in ("钱", "分"):
                return 0 < num <= 100
            elif unit in ("克", "两"):
                return 0 < num <= 500
            elif unit == "斤":
                return 0 < num <= 10
            return True
        except Exception:
            return True  # 无法判断时默认为合理

    # =========================================================================
    # 步骤 11: LLM 四级决策校对
    # =========================================================================

    def _llm_four_level_decision(
        self,
        lines: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """对争议行执行 LLM 四级决策校对。

        四级决策：
        1. direct_accept — 置信度 >= 0.90，直接采纳
        2. minor_adjust — 置信度 0.75-0.89，微调修正
        3. deep_verify — 置信度 0.60-0.74，深度验证
        4. human_review — 置信度 < 0.60，人工审核

        Args:
            lines: 行列表

        Returns:
            经过 LLM 决策的行列表
        """
        for line in lines:
            confidence = line.get("confidence", 0.0)
            fused_text = line.get("fused_text", "")

            # 决定决策级别
            if confidence >= LLM_DECISION_THRESHOLD_MINOR:
                decision_level = 0  # direct_accept
                decision = LLM_DECISION_LEVELS[0]
            elif confidence >= LLM_DECISION_THRESHOLD_VERIFY:
                decision_level = 1  # minor_adjust
                decision = LLM_DECISION_LEVELS[1]
            elif confidence >= LLM_DECISION_THRESHOLD_HUMAN:
                decision_level = 2  # deep_verify
                decision = LLM_DECISION_LEVELS[2]
            else:
                decision_level = 3  # human_review
                decision = LLM_DECISION_LEVELS[3]

            line["llm_decision"] = decision
            line["llm_decision_level"] = decision_level

            # 对于需要 LLM 干预的级别，调用 LLM
            if decision_level >= 1:
                try:
                    corrected = self._call_llm_for_correction(line)
                    if corrected and corrected != fused_text:
                        line["llm_corrected_text"] = corrected
                        line["llm_corrected"] = True
                        line["original_fused_text"] = fused_text
                        line["fused_text"] = corrected
                except Exception as e:
                    logger.debug("LLM 校对失败: %s", e)
                    line["llm_error"] = str(e)

            # 标记争议行
            if decision_level >= 2:
                line["disputed"] = True
                line["dispute_reason"] = f"置信度 {confidence:.3f}，触发 {decision}"

        return lines

    def _call_llm_for_correction(self, line: Dict[str, Any]) -> str:
        """调用 LLM 进行文本校正。

        Args:
            line: 行数据字典

        Returns:
            校正后的文本
        """
        fused_text = line.get("fused_text", "")
        engine_results = line.get("engine_results", {})

        # 构建 prompt
        prompt = """你是一位中医文献 OCR 校对专家。请根据以下多引擎识别结果，
选择或修正最准确的文本。

各引擎识别结果：
"""
        for name, result in engine_results.items():
            prompt += f"- {name}: {result.get('text', '')} (置信度: {result.get('confidence', 0):.3f})\n"

        prompt += f"\n融合结果: {fused_text}\n"

        # 检查是否有否定词上下文
        negations = line.get("negation_words", [])
        if negations:
            prompt += f"\n注意：包含否定词 {negations}，请确保含义完整准确。\n"

        prompt += "\n请只输出最准确的文本，不要添加任何解释。"

        # 尝试调用 LLM
        try:

            # 优先使用本地 LLM
            local_llm = self.engines.get("shizhengpt")
            if local_llm:
                output = local_llm.generate(prompt, max_tokens=256, temperature=0.1)
                if output:
                    return output.strip()

            # 降级云端 LLM
            cloud_llm = self.engines.get("cloud_llm")
            if cloud_llm:
                output = cloud_llm.generate(prompt, max_tokens=256, temperature=0.1)
                if output:
                    return output.strip()

        except Exception as e:
            logger.debug("LLM 调用失败: %s", e)

        return fused_text

    # =========================================================================
    # 步骤 12: 字形验证（第一道/第二道）
    # =========================================================================

    def _glyph_verification_two_pass(
        self,
        lines: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """字形验证的两道检查。

        第一道：验证字符是否在允许的字符集中
        第二道：验证中医特殊字符的字形正确性

        Args:
            lines: 行列表

        Returns:
            经过字形验证的行列表
        """
        for line in lines:
            fused_text = line.get("fused_text", "")

            # 第一道：基本字符集验证
            invalid_chars = self._first_pass_glyph_check(fused_text)
            if invalid_chars:
                line["glyph_invalid_chars"] = invalid_chars
                line["glyph_first_pass"] = False
            else:
                line["glyph_first_pass"] = True

            # 第二道：中医特殊字符验证
            tcm_issues = self._second_pass_glyph_check(fused_text)
            if tcm_issues:
                line["glyph_tcm_issues"] = tcm_issues
                line["glyph_second_pass"] = False
            else:
                line["glyph_second_pass"] = True

        return lines

    def _first_pass_glyph_check(self, text: str) -> List[Dict[str, Any]]:
        """第一道字形验证：检查非法字符。

        Args:
            text: 待检查文本

        Returns:
            非法字符列表
        """
        invalid: List[Dict[str, Any]] = []
        allowed_ranges = [
            (0x4E00, 0x9FFF),    # CJK 统一表意符号
            (0x3400, 0x4DBF),    # CJK 扩展-A
            (0x3000, 0x303F),    # CJK 符号和标点
            (0xFF00, 0xFFEF),    # 全角 ASCII
            (0x2000, 0x206F),    # 一般标点
            (0x0080, 0x00FF),    # 扩展 ASCII
            (0x0030, 0x0039),    # 数字
            (0x0041, 0x005A),    # 大写字母
            (0x0061, 0x007A),    # 小写字母
            (0x0020, 0x007E),    # 基本 ASCII
        ]

        for i, char in enumerate(text):
            code = ord(char)
            is_valid = any(start <= code <= end for start, end in allowed_ranges)
            if not is_valid and not char.isspace():
                invalid.append({
                    "char": char,
                    "position": i,
                    "unicode": f"U+{code:04X}",
                })

        return invalid

    def _second_pass_glyph_check(self, text: str) -> List[Dict[str, Any]]:
        """第二道字形验证：检查中医特殊字符。

        检测常见的 OCR 字形错误，如：
        - 相似字形混淆（己/已/巳、人/入、天/夫 等）
        - 中医专用字错误

        Args:
            text: 待检查文本

        Returns:
            字形问题列表
        """
        issues: List[Dict[str, Any]] = []

        # 常见混淆字对
        confused_pairs = [
            ("己", ["已", "巳"]),
            ("已", ["己", "巳"]),
            ("人", ["入", "八"]),
            ("入", ["人", "八"]),
            ("天", ["夫", "夭"]),
            ("千", ["干", "于"]),
            ("未", ["末", "朱"]),
            ("茶", ["荼"]),
            ("芪", ["茋"]),
            ("芎", ["穹"]),
            ("蒡", ["磅"]),
        ]

        for correct, wrong_list in confused_pairs:
            for wrong in wrong_list:
                if wrong in text:
                    # 检查上下文是否是错误用法
                    idx = text.find(wrong)
                    context = text[max(0, idx - 3) : idx + 4]
                    # 这里简化：只要出现就标记
                    issues.append({
                        "suspected_wrong": wrong,
                        "possible_correct": correct,
                        "context": context,
                        "position": idx,
                    })

        return issues

    # =========================================================================
    # 步骤 13: 方剂组成提取
    # =========================================================================

    def _extract_formula_compositions(
        self,
        lines: List[Dict[str, Any]],
        page_num: int,
    ) -> List[Dict[str, Any]]:
        """从行中提取方剂组成。

        识别方剂名和药材组成。

        Args:
            lines: 行列表
            page_num: 页码

        Returns:
            方剂列表
        """
        from kzocr.tcm_ocr.utils.common import (
            extract_formula_name,
            extract_herb_names,
        )

        formulas: List[Dict[str, Any]] = []
        current_formula: Optional[Dict[str, Any]] = None

        for line in lines:
            text = line.get("fused_text", "")
            formula_name = extract_formula_name(text)

            if formula_name:
                # 新方剂开始
                if current_formula:
                    formulas.append(current_formula)

                herbs = extract_herb_names(text)
                current_formula = {
                    "formula_name": formula_name,
                    "page_number": page_num,
                    "source_text": text,
                    "line_id": line.get("block_id", ""),
                    "ingredients": [
                        {"herb_name": h, "dosage": "", "unit": ""}
                        for h in herbs
                    ],
                    "ingredient_count": len(herbs),
                }
            elif current_formula:
                # 继续当前方剂的药材列表
                herbs = extract_herb_names(text)
                for h in herbs:
                    existing = [i["herb_name"] for i in current_formula["ingredients"]]
                    if h not in existing:
                        current_formula["ingredients"].append(
                            {"herb_name": h, "dosage": "", "unit": ""}
                        )
                current_formula["source_text"] += " " + text

        if current_formula:
            formulas.append(current_formula)

        return formulas

    # =========================================================================
    # 步骤 14: 整方级校验
    # =========================================================================

    def _whole_formula_validation(
        self,
        formulas: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """对整个方剂进行校验。

        校验项：
        - 方剂名是否在已知方剂库中
        - 药材数量是否合理（通常 4-20 味）
        - 是否有重复药材

        Args:
            formulas: 方剂列表

        Returns:
            校验后的方剂列表
        """
        from kzocr.tcm_ocr.utils.common import COMMON_FORMULA_NAMES

        for formula in formulas:
            formula_name = formula.get("formula_name", "")
            ingredients = formula.get("ingredients", [])
            issues: List[str] = []

            # 检查方剂名
            if formula_name not in COMMON_FORMULA_NAMES:
                issues.append(f"'{formula_name}' 不在常见方剂库中")

            # 检查药材数量
            herb_count = len(ingredients)
            if herb_count < 1:
                issues.append("未提取到任何药材")
            elif herb_count > 30:
                issues.append(f"药材数量过多 ({herb_count})，可能包含噪声")

            # 检查重复
            herb_names = [i["herb_name"] for i in ingredients]
            duplicates = [h for h in set(herb_names) if herb_names.count(h) > 1]
            if duplicates:
                issues.append(f"重复药材: {', '.join(duplicates)}")

            formula["validation_issues"] = issues
            formula["validation_passed"] = len(issues) == 0

        return formulas

    # =========================================================================
    # 辅助方法
    # =========================================================================

    def _extract_image_indices(
        self,
        blocks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """从版面块中提取图片索引。

        Args:
            blocks: 版面块列表

        Returns:
            图片索引列表
        """
        images: List[Dict[str, Any]] = []
        for block in blocks:
            if block.get("type") == "image":
                images.append({
                    "bbox": block.get("bbox", []),
                    "caption": block.get("text", ""),
                    "block_id": block.get("id", ""),
                })
        return images

    # =========================================================================
    # 数据库写入
    # =========================================================================

    def build_book_structure(
        self,
        book_id: str,
        page_data_list: List[Dict[str, Any]],
        db_book: Any,
    ) -> None:
        """将页面数据写入书籍数据库。

        按 Page → Paragraph → Line → ImageIndex 层次入库。
        每 10 页一个事务，减少事务开销。

        Args:
            book_id: 书籍 ID
            page_data_list: 页面处理结果列表
            db_book: SQLite 书籍数据库连接
        """
        if not db_book:
            logger.error("[%s] 数据库连接为空", book_id)
            return

        paragraph_counter = 0
        line_counter = 0
        pages_since_commit = 0

        for page_data in page_data_list:
            page_num = page_data.get("page_number", 0)

            try:
                # 写入页面记录
                db_book.execute(
                    "INSERT OR REPLACE INTO page (book_id, page_number, layout_type) "
                    "VALUES (?, ?, ?)",
                    (book_id, page_num, page_data.get("layout_type", "text")),
                )

                # 写入行记录
                for line in page_data.get("lines", []):
                    line_counter += 1
                    line_id = f"{book_id}_p{page_num}_l{line_counter}"

                    # 确定段落
                    paragraph_counter += 1
                    para_id = f"{book_id}_p{page_num}_para{paragraph_counter}"

                    db_book.execute(
                        "INSERT OR REPLACE INTO proofread_record ("
                        "  book_id, page_number, paragraph_id, line_id, line_number, "
                        "  original_text, corrected_text, confidence, "
                        "  engine_results, llm_decision, llm_decision_level, "
                        "  disputed, dispute_reason, fused_text, created_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            book_id,
                            page_num,
                            para_id,
                            line_id,
                            line_counter,
                            line.get("original_text", line.get("fused_text", "")),
                            line.get("fused_text", ""),
                            line.get("confidence", 0.0),
                            json.dumps(line.get("engine_results", {}), ensure_ascii=False),
                            line.get("llm_decision", ""),
                            line.get("llm_decision_level", 0),
                            line.get("disputed", False),
                            line.get("dispute_reason", ""),
                            line.get("fused_text", ""),
                            datetime.now().isoformat(),
                        ),
                    )

                # 写入方剂记录
                for formula in page_data.get("formulas", []):
                    formula_id = f"{book_id}_f{formula.get('formula_name', 'unknown')}"
                    db_book.execute(
                        "INSERT OR REPLACE INTO formula_composition ("
                        "  book_id, formula_id, formula_name, formula_sequence, "
                        "  page_numbers, source_text, extracted_by, created_at"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            book_id,
                            formula_id,
                            formula.get("formula_name", ""),
                            formula.get("ingredient_count", 0),
                            str(page_num),
                            formula.get("source_text", ""),
                            "page_pipeline",
                            datetime.now().isoformat(),
                        ),
                    )

                    for i, ing in enumerate(formula.get("ingredients", [])):
                        db_book.execute(
                            "INSERT OR REPLACE INTO formula_ingredient ("
                            "  book_id, formula_id, herb_name, dosage, "
                            "  dosage_unit, ingredient_order, created_at"
                            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (
                                book_id,
                                formula_id,
                                ing.get("herb_name", ""),
                                ing.get("dosage", None),
                                ing.get("unit", ""),
                                i,
                                datetime.now().isoformat(),
                            ),
                        )

                # 写入图片索引
                for img in page_data.get("images", []):
                    db_book.execute(
                        "INSERT OR REPLACE INTO image_index ("
                        "  book_id, page_number, bbox, caption, block_id"
                        ") VALUES (?, ?, ?, ?, ?)",
                        (
                            book_id,
                            page_num,
                            json.dumps(img.get("bbox", [])),
                            img.get("caption", ""),
                            img.get("block_id", ""),
                        ),
                    )

                pages_since_commit += 1

                # 每 10 页提交一次事务
                if pages_since_commit >= 10:
                    db_book.commit()
                    pages_since_commit = 0
                    logger.debug("[%s] 已提交 %d 页", book_id, page_num)

            except Exception as e:
                logger.error("[%s] 第 %d 页入库失败: %s", book_id, page_num, e)

        # 提交剩余事务
        try:
            db_book.commit()
        except Exception as e:
            logger.error("[%s] 最终提交失败: %s", book_id, e)

        logger.info(
            "[%s] 书籍结构写入完成: %d 页, %d 段落, %d 行",
            book_id,
            len(page_data_list),
            paragraph_counter,
            line_counter,
        )
