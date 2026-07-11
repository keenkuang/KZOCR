"""
版式分类模块

对页面图像进行版式分类，判断页面类型：
- 'text': 纯文本页面（单栏）
- 'table': 表格主导页面
- 'multi_column': 多栏排版页面

分类方法基于传统图像处理（无需深度学习模型）：
- 通过分析水平/垂直投影
- 连通域分析
- 直线检测（表格线）
- 文本列分布统计

置信度 < 0.9 时建议并行运行双管线。
"""

from typing import Any, Dict, Tuple

import cv2
import numpy as np


class LayoutClassifier:
    """页面版式分类器。

    使用传统图像处理方法对页面进行版式分类，无需深度学习模型。
    分类结果用于决定后续处理流程（单栏/多栏/表格专用管线）。

    Attributes
    ----------
    min_table_line_ratio : float
        判定为表格的最低直线比例阈值。
    min_col_gap_ratio : float
        判定为多栏的最低列间距比例。
    min_col_count : int
        判定为多栏的最少列数。

    Examples
    --------
    >>> classifier = LayoutClassifier()
    >>> result = classifier.classify_page(page_img)
    >>> print(result)
    {'page_type': 'text', 'confidence': 0.92}
    """

    def __init__(
        self,
        min_table_line_ratio: float = 0.08,
        min_col_gap_ratio: float = 0.04,
        min_col_count: int = 2,
    ):
        """初始化版式分类器。

        Parameters
        ----------
        min_table_line_ratio :
            判定为表格的最小水平/垂直直线面积占页面比例，默认 0.08。
        min_col_gap_ratio :
            判定为多栏的最小列间距占页面宽度比例，默认 0.04。
        min_col_count :
            判定为多栏的最少列数，默认 2。
        """
        self.min_table_line_ratio = min_table_line_ratio
        self.min_col_gap_ratio = min_col_gap_ratio
        self.min_col_count = min_col_count

    def classify_page(self, page_img: np.ndarray) -> Dict[str, Any]:
        """对页面图像进行版式分类。

        分类逻辑：
        1. 预处理：灰度化 + 二值化
        2. 表格检测：检测水平和垂直直线
        3. 多栏检测：通过垂直投影分析文本列分布
        4. 综合判定并计算置信度

        Parameters
        ----------
        page_img :
            输入页面图像，BGR uint8。

        Returns
        -------
        dict
            {
                'page_type': 'text' | 'table' | 'multi_column',
                'confidence': float,  # 0.0 ~ 1.0
            }
            置信度 < 0.9 时建议并行运行双管线。
        """
        if page_img is None or page_img.size == 0:
            return {'page_type': 'text', 'confidence': 0.0}

        # 灰度化
        if len(page_img.shape) == 3 and page_img.shape[2] == 3:
            gray = cv2.cvtColor(page_img, cv2.COLOR_BGR2GRAY)
        else:
            gray = page_img.copy()

        h, w = gray.shape[:2]
        if h < 10 or w < 10:
            return {'page_type': 'text', 'confidence': 0.5}

        # 二值化（反转：文字为白色，背景为黑色）
        _, binary_inv = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )

        # ---- 1. 表格检测 ----
        table_score = self._detect_table_score(binary_inv, w, h)

        # ---- 2. 多栏检测 ----
        column_score, col_count = self._detect_multi_column(
            binary_inv, w, h
        )

        # ---- 3. 综合判定 ----
        page_type, confidence = self._decide_layout(
            table_score, column_score, col_count, w, h
        )

        return {
            'page_type': page_type,
            'confidence': round(confidence, 3),
        }

    def _detect_table_score(
        self, binary_inv: np.ndarray, page_w: int, page_h: int
    ) -> float:
        """检测表格线的比例，返回表格特征分数。

        使用形态学操作检测水平和垂直直线，计算其占页面面积的比例。

        Parameters
        ----------
        binary_inv :
            反转二值图（文字白、背景黑）。
        page_w, page_h :
            页面宽高。

        Returns
        -------
        float
            表格线面积占页面面积的比例。
        """
        page_area = page_w * page_h
        if page_area == 0:
            return 0.0

        # 检测水平线
        h_kernel_len = max(page_w // 15, 20)
        h_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (h_kernel_len, 1)
        )
        h_lines = cv2.morphologyEx(binary_inv, cv2.MORPH_OPEN, h_kernel)
        np.sum(h_lines > 0)

        # 检测垂直线
        v_kernel_len = max(page_h // 30, 15)
        v_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (1, v_kernel_len)
        )
        v_lines = cv2.morphologyEx(binary_inv, cv2.MORPH_OPEN, v_kernel)
        np.sum(v_lines > 0)

        # 合并直线区域
        combined_lines = cv2.bitwise_or(h_lines, v_lines)
        total_line_area = np.sum(combined_lines > 0)

        # 计算比例
        line_ratio = total_line_area / page_area

        # 额外检测：Hough 直线检测（作为辅助）
        edges = cv2.Canny(binary_inv, 50, 150)
        try:
            hough_lines = cv2.HoughLinesP(
                edges, 1, np.pi / 180,
                threshold=max(page_w // 8, 50),
                minLineLength=max(page_w // 10, 30),
                maxLineGap=5,
            )
        except cv2.error:
            hough_lines = None

        hough_boost = 0.0
        if hough_lines is not None and len(hough_lines) >= 3:
            # 统计近似水平和垂直的线
            h_count = 0
            v_count = 0
            for line in hough_lines:
                x1, y1, x2, y2 = line[0]
                dx = abs(x2 - x1)
                dy = abs(y2 - y1)
                if dy == 0:
                    h_count += 1
                elif dx == 0:
                    v_count += 1
                elif dy / (dx + 1e-6) < 0.1:
                    h_count += 1
                elif dx / (dy + 1e-6) < 0.1:
                    v_count += 1

            # 同时有水平和垂直线 → 更可能是表格
            if h_count >= 2 and v_count >= 2:
                hough_boost = 0.05

        return min(line_ratio + hough_boost, 1.0)

    def _detect_multi_column(
        self, binary_inv: np.ndarray, page_w: int, page_h: int
    ) -> Tuple[float, int]:
        """检测多栏排版特征。

        通过垂直投影分析，寻找文本列的分布模式。

        Parameters
        ----------
        binary_inv :
            反转二值图（文字白、背景黑）。
        page_w, page_h :
            页面宽高。

        Returns
        -------
        Tuple[float, int]
            (多栏特征分数, 检测到的列数)。
        """
        if page_w < 100 or page_h < 100:
            return 0.0, 1

        # 垂直投影（统计每列的白色像素数 = 文字量）
        v_projection = np.sum(binary_inv > 0, axis=0).astype(np.float64)

        # 平滑投影曲线
        kernel_size = max(5, page_w // 100)
        if kernel_size % 2 == 0:
            kernel_size += 1
        smoothed = cv2.GaussianBlur(
            v_projection.reshape(1, -1), (kernel_size, 1), 0
        ).flatten()

        # 归一化
        if np.max(smoothed) > 0:
            smoothed = smoothed / np.max(smoothed)

        # 检测文本列和列间隙
        # 使用阈值区分文本区（高投影值）和间隙区（低投影值）
        threshold = 0.15
        is_text = smoothed > threshold

        # 寻找文本列
        columns = []
        in_text = False
        col_start = 0

        for i, text_flag in enumerate(is_text):
            if text_flag and not in_text:
                in_text = True
                col_start = i
            elif not text_flag and in_text:
                in_text = False
                col_end = i
                col_width = col_end - col_start
                # 过滤过窄的列
                min_col_width = page_w * 0.08
                if col_width >= min_col_width:
                    columns.append((col_start, col_end, col_width))

        # 处理末尾
        if in_text:
            col_end = len(is_text)
            col_width = col_end - col_start
            min_col_width = page_w * 0.08
            if col_width >= min_col_width:
                columns.append((col_start, col_end, col_width))

        col_count = len(columns)

        if col_count < self.min_col_count:
            return 0.0, col_count

        # 计算列间间隙的一致性
        gaps = []
        for i in range(len(columns) - 1):
            gap = columns[i + 1][0] - columns[i][1]
            gaps.append(gap)

        if not gaps:
            return 0.0, col_count

        avg_gap = np.mean(gaps)
        gap_ratio = avg_gap / page_w if page_w > 0 else 0.0

        # 列宽一致性（变异系数）
        col_widths = [c[2] for c in columns]
        cv_width = np.std(col_widths) / (np.mean(col_widths) + 1e-6)

        # 多栏分数：间隙比例 + 列宽一致性奖励
        multi_col_score = gap_ratio
        if cv_width < 0.3:  # 列宽接近一致
            multi_col_score += 0.05
        if col_count >= 3:
            multi_col_score += 0.03  # 3+列额外加权

        return min(multi_col_score, 1.0), col_count

    def _decide_layout(
        self,
        table_score: float,
        column_score: float,
        col_count: int,
        page_w: int,
        page_h: int,
    ) -> Tuple[str, float]:
        """综合判定页面版式类型和置信度。

        Parameters
        ----------
        table_score :
            表格特征分数（直线面积比例）。
        column_score :
            多栏特征分数。
        col_count :
            检测到的列数。
        page_w, page_h :
            页面宽高。

        Returns
        -------
        Tuple[str, float]
            (page_type, confidence)
        """
        # 表格判定
        is_table = table_score >= self.min_table_line_ratio

        # 多栏判定
        is_multi_col = (
            column_score >= self.min_col_gap_ratio
            and col_count >= self.min_col_count
        )

        # 优先级：表格 > 多栏 > 纯文本
        if is_table and is_multi_col:
            # 同时满足，比较分数
            if table_score > column_score * 2:
                page_type = 'table'
                confidence = min(table_score * 5, 0.95)
            else:
                page_type = 'multi_column'
                confidence = min(column_score * 8, 0.95)
        elif is_table:
            page_type = 'table'
            # 置信度：直线比例越高越确信
            confidence = min(table_score * 5, 0.98)
        elif is_multi_col:
            page_type = 'multi_column'
            # 置信度：基于列间间隙和列数
            confidence = min(0.7 + column_score * 5 + col_count * 0.05, 0.95)
        else:
            page_type = 'text'
            # 纯文本的置信度：表格和多栏分数越低越确信
            anti_confidence = max(table_score * 5, column_score * 8)
            confidence = max(0.5, 1.0 - anti_confidence)

        # 确保置信度在合理范围
        confidence = float(np.clip(confidence, 0.0, 1.0))

        return page_type, confidence
