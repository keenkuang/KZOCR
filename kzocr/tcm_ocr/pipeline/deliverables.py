"""
交付物生成模块。

负责生成最终输出文件：
- body.md: 仅正文内容的 Markdown
- full.md: 完整版（含前言、正文、附录等全部章节）
- final_document.json: 结构化 JSON 数据
- assets/: 争议行图片归档
- checksums.sha256: 文件校验和
"""

import hashlib
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Set

from kzocr.tcm_ocr.config.constants import (
    OUTPUT_SUBDIR_ASSETS,
    OUTPUT_SUBDIR_BODY_MD,
    OUTPUT_SUBDIR_FINAL_JSON,
    OUTPUT_SUBDIR_FULL_MD,
    SHA256_MANIFEST_FILENAME,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Section → 中文标题映射
# =============================================================================


def get_section_title(section_name: str) -> str:
    """将 section 标识映射为中文标题。

    Args:
        section_name: section 标识字符串

    Returns:
        对应的中文标题

    Example:
        >>> get_section_title("preface")
        '前言'
        >>> get_section_title("body")
        '正文'
    """
    mapping = {
        "preface": "前言",
        "foreword": "序",
        "introduction": "绪论",
        "body": "正文",
        "appendix": "附录",
        "index": "索引",
        "references": "参考文献",
        "acknowledgements": "致谢",
        "postscript": "跋",
        "copyright": "版权页",
        "toc": "目录",
    }
    return mapping.get(section_name.lower(), section_name)


# =============================================================================
# 节点渲染
# =============================================================================


def render_node(node: Dict[str, Any], depth: int = 0) -> str:
    """渲染单个文档节点为 Markdown 字符串。

    根据节点类型（heading、text、formula、table、image）
    生成对应的 Markdown 格式。

    Args:
        node: 文档节点字典
        depth: 当前层级深度

    Returns:
        Markdown 字符串
    """
    if not node:
        return ""

    node_type = node.get("type", "text")
    content_type = node.get("content_type", node_type)
    text = node.get("text", "")
    heading = node.get("heading", "")
    title = node.get("title", "")
    display_text = heading or title or text or ""

    lines: List[str] = []

    if node_type in ("heading", "header", "title"):
        level = min(node.get("level", depth + 1), 6)
        prefix = "#" * level
        lines.append(f"{prefix} {display_text}")
        lines.append("")

    elif content_type == "formula" or node_type == "formula":
        # 方剂节点
        formula_name = node.get("formula_name", "")
        ingredients = node.get("ingredients", [])
        source_text = node.get("source_text", text)

        lines.append("---")
        if formula_name:
            lines.append(f"**【方剂】{formula_name}**")
        lines.append(f"> {source_text}")
        if ingredients:
            lines.append("")
            lines.append("**组成：**")
            for ing in ingredients:
                herb = ing.get("herb_name", "") if isinstance(ing, dict) else str(ing)
                dosage = ing.get("dosage", "") if isinstance(ing, dict) else ""
                unit = ing.get("dosage_unit", "") if isinstance(ing, dict) else ""
                dose_str = f"{dosage}{unit}" if dosage and unit else str(dosage)
                if dose_str:
                    lines.append(f"- {herb} {dose_str}")
                else:
                    lines.append(f"- {herb}")
        lines.append("---")
        lines.append("")

    elif content_type == "table" or node_type == "table":
        # 表格节点
        table_data = node.get("table_data", [])
        if table_data and isinstance(table_data, list):
            lines.append(render_table_markdown(table_data))
        else:
            lines.append(display_text)
        lines.append("")

    elif content_type == "image" or node_type == "image":
        # 图片节点
        img_path = node.get("image_path", node.get("path", ""))
        img_caption = node.get("caption", title or display_text)
        if img_path:
            lines.append(f"![{img_caption}]({img_path})")
        lines.append("")

    elif content_type == "text" or node_type == "text":
        # 普通文本节点
        if display_text:
            lines.append(display_text)
            lines.append("")

    else:
        # 未知类型，原样输出
        if display_text:
            lines.append(display_text)
            lines.append("")

    # 递归渲染子节点
    children = node.get("children", [])
    for child in children:
        child_md = render_node(child, depth + 1)
        if child_md:
            lines.append(child_md)

    return "\n".join(lines)


def render_table_markdown(table_data: List[List[str]]) -> str:
    """将表格数据渲染为 Markdown 表格。

    Args:
        table_data: 二维表格数据

    Returns:
        Markdown 表格字符串
    """
    if not table_data or not isinstance(table_data, list):
        return ""

    lines: List[str] = []

    # 表头
    header = table_data[0] if table_data else []
    if header:
        lines.append("| " + " | ".join(str(cell) for cell in header) + " |")
        lines.append("| " + " | ".join("---" for _ in header) + " |")

    # 数据行
    for row in table_data[1:]:
        if row:
            lines.append("| " + " | ".join(str(cell) for cell in row) + " |")

    return "\n".join(lines)


# =============================================================================
# Markdown 渲染（从文档树）
# =============================================================================


def render_body_markdown(
    book_meta: Dict[str, Any],
    document_tree: Dict[str, Any],
) -> str:
    """渲染仅含正文（section='body'）的 Markdown。

    遍历文档树，只保留 section='body' 的节点。

    Args:
        book_meta: 书籍元数据
        document_tree: 文档树结构

    Returns:
        body.md 内容字符串
    """
    lines: List[str] = []

    # 文档标题
    book_title = book_meta.get("title", "未知标题")
    lines.append(f"# {book_title}")
    lines.append("")

    # 元数据
    author = book_meta.get("author", "")
    publisher = book_meta.get("publisher", "")
    pub_year = book_meta.get("pub_year", "")
    meta_parts = [p for p in [author, publisher, pub_year] if p]
    if meta_parts:
        lines.append(f"*{' · '.join(meta_parts)}*")
        lines.append("")
    lines.append("---")
    lines.append("")

    # 遍历节点，只渲染 body 部分
    nodes = document_tree.get("nodes", document_tree.get("sections", []))
    body_nodes = [
        n for n in nodes
        if n.get("section", n.get("section_name", "body")).lower() == "body"
    ]

    for node in body_nodes:
        md = render_node(node, depth=0)
        if md:
            lines.append(md)

    return "\n".join(lines)


def render_full_markdown(
    book_meta: Dict[str, Any],
    document_tree: Dict[str, Any],
) -> str:
    """渲染完整版 Markdown，按 section 分组并去重。

    Args:
        book_meta: 书籍元数据
        document_tree: 文档树结构

    Returns:
        full.md 内容字符串
    """
    lines: List[str] = []

    # 文档标题
    book_title = book_meta.get("title", "未知标题")
    lines.append(f"# {book_title}")
    lines.append("")

    # 元数据
    lines.append("## 元数据")
    lines.append("")
    for key in ["isbn", "author", "publisher", "pub_year", "pub_month",
                "edition", "price", "category", "language"]:
        val = book_meta.get(key, "")
        if val:
            lines.append(f"- **{key}**: {val}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 按 section 分组
    nodes = document_tree.get("nodes", document_tree.get("sections", []))

    # section 排序
    section_order = ["copyright", "toc", "preface", "foreword", "introduction",
                     "body", "appendix", "references", "index", "acknowledgements", "postscript"]

    section_groups: Dict[str, List[Dict]] = {}
    for node in nodes:
        sec = node.get("section", node.get("section_name", "body")).lower()
        if sec not in section_groups:
            section_groups[sec] = []
        section_groups[sec].append(node)

    seen_sections: Set[str] = set()

    # 按预定义顺序输出
    for sec in section_order:
        if sec in section_groups and sec not in seen_sections:
            seen_sections.add(sec)
            sec_title = get_section_title(sec)
            lines.append(f"# {sec_title}")
            lines.append("")

            for node in section_groups[sec]:
                md = render_node(node)
                if md:
                    lines.append(md)

            lines.append("")
            lines.append("---")
            lines.append("")

    # 输出未预定义顺序的 section
    for sec, sec_nodes in section_groups.items():
        if sec not in seen_sections:
            seen_sections.add(sec)
            sec_title = get_section_title(sec)
            lines.append(f"# {sec_title}")
            lines.append("")

            for node in sec_nodes:
                md = render_node(node)
                if md:
                    lines.append(md)

            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


# =============================================================================
# 从 final_doc 直接渲染（备选路径）
# =============================================================================


def render_body_markdown_from_doc(final_doc: Dict[str, Any]) -> str:
    """直接从 final_doc 数据结构渲染 body.md（备选路径）。

    当 document_tree 不可用时，直接从 final_doc 的 content 字段渲染。

    Args:
        final_doc: 最终文档字典

    Returns:
        body.md 内容字符串
    """
    lines: List[str] = []

    # 标题
    title = final_doc.get("title", final_doc.get("book_title", "未知标题"))
    lines.append(f"# {title}")
    lines.append("")

    # 元数据
    meta = final_doc.get("metadata", {})
    author = meta.get("author", "")
    publisher = meta.get("publisher", "")
    pub_year = meta.get("pub_year", "")
    meta_parts = [p for p in [author, publisher, pub_year] if p]
    if meta_parts:
        lines.append(f"*{' · '.join(meta_parts)}*")
        lines.append("")
    lines.append("---")
    lines.append("")

    # 正文内容
    content = final_doc.get("content", [])
    for item in content:
        section = item.get("section", "body")
        if section != "body":
            continue

        item_type = item.get("type", "text")
        text = item.get("text", "")

        if item_type == "heading":
            level = min(item.get("level", 2), 6)
            lines.append("#" * level + f" {text}")
            lines.append("")
        elif item_type == "formula":
            formula_name = item.get("formula_name", "")
            ingredients = item.get("ingredients", [])
            lines.append("---")
            if formula_name:
                lines.append(f"**【方剂】{formula_name}**")
            lines.append(f"> {text}")
            if ingredients:
                lines.append("")
                lines.append("**组成：**")
                for ing in ingredients:
                    herb = ing.get("herb_name", "") if isinstance(ing, dict) else str(ing)
                    dosage = ing.get("dosage", "") if isinstance(ing, dict) else ""
                    unit = ing.get("unit", "") if isinstance(ing, dict) else ""
                    if dosage and unit:
                        lines.append(f"- {herb} {dosage}{unit}")
                    else:
                        lines.append(f"- {herb}")
            lines.append("---")
            lines.append("")
        else:
            if text:
                lines.append(text)
                lines.append("")

    return "\n".join(lines)


def render_full_markdown_from_doc(final_doc: Dict[str, Any]) -> str:
    """直接从 final_doc 数据结构渲染 full.md（备选路径）。

    Args:
        final_doc: 最终文档字典

    Returns:
        full.md 内容字符串
    """
    lines: List[str] = []

    # 标题
    title = final_doc.get("title", final_doc.get("book_title", "未知标题"))
    lines.append(f"# {title}")
    lines.append("")

    # 元数据
    lines.append("## 元数据")
    lines.append("")
    meta = final_doc.get("metadata", {})
    for key, val in meta.items():
        if val:
            lines.append(f"- **{key}**: {val}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 按 section 分组内容
    content = final_doc.get("content", [])
    section_order = ["copyright", "toc", "preface", "foreword", "introduction",
                     "body", "appendix", "references", "index"]

    section_groups: Dict[str, List[Dict]] = {}
    for item in content:
        sec = item.get("section", "body")
        if sec not in section_groups:
            section_groups[sec] = []
        section_groups[sec].append(item)

    seen_sections: Set[str] = set()

    for sec in section_order:
        if sec in section_groups and sec not in seen_sections:
            seen_sections.add(sec)
            sec_title = get_section_title(sec)
            lines.append(f"# {sec_title}")
            lines.append("")

            for item in section_groups[sec]:
                item_type = item.get("type", "text")
                text = item.get("text", "")

                if item_type == "heading":
                    level = min(item.get("level", 2), 6)
                    lines.append("#" * level + f" {text}")
                    lines.append("")
                elif item_type == "formula":
                    formula_name = item.get("formula_name", "")
                    ingredients = item.get("ingredients", [])
                    lines.append("---")
                    if formula_name:
                        lines.append(f"**【方剂】{formula_name}**")
                    lines.append(f"> {text}")
                    if ingredients:
                        lines.append("")
                        lines.append("**组成：**")
                        for ing in ingredients:
                            herb = ing.get("herb_name", "") if isinstance(ing, dict) else str(ing)
                            dosage = ing.get("dosage", "") if isinstance(ing, dict) else ""
                            unit = ing.get("unit", "") if isinstance(ing, dict) else ""
                            if dosage and unit:
                                lines.append(f"- {herb} {dosage}{unit}")
                            else:
                                lines.append(f"- {herb}")
                    lines.append("---")
                    lines.append("")
                elif item_type == "table":
                    table_data = item.get("table_data", [])
                    if table_data:
                        lines.append(render_table_markdown(table_data))
                    lines.append("")
                else:
                    if text:
                        lines.append(text)
                        lines.append("")

            lines.append("")
            lines.append("---")
            lines.append("")

    # 其他 section
    for sec, sec_items in section_groups.items():
        if sec not in seen_sections:
            seen_sections.add(sec)
            sec_title = get_section_title(sec)
            lines.append(f"# {sec_title}")
            lines.append("")
            for item in sec_items:
                text = item.get("text", "")
                if text:
                    lines.append(text)
                    lines.append("")
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


# =============================================================================
# final_doc 构建
# =============================================================================


def _build_final_doc_from_book_db(
    book_id: str,
    db_book: object,
    book_meta: Dict[str, Any],
) -> Dict[str, Any]:
    """从书籍数据库构建 final_doc 数据结构。

    从 SQLite 书籍库中提取校对后的内容，构建完整的 final_doc 字典。

    Args:
        book_id: 书籍 ID
        db_book: SQLite 书籍数据库连接
        book_meta: 书籍元数据

    Returns:
        final_doc 字典
    """
    final_doc: Dict[str, Any] = {
        "book_id": book_id,
        "title": book_meta.get("title", ""),
        "metadata": {
            "isbn": book_meta.get("isbn", ""),
            "author": book_meta.get("author", ""),
            "publisher": book_meta.get("publisher", ""),
            "pub_year": book_meta.get("pub_year", ""),
            "pub_month": book_meta.get("pub_month", ""),
            "edition": book_meta.get("edition", ""),
            "price": book_meta.get("price", ""),
            "category": book_meta.get("category", "中医"),
            "language": book_meta.get("language", "zh-CN"),
        },
        "content": [],
        "statistics": {},
        "formulas": [],
        "generated_at": datetime.now().isoformat(),
    }

    # 1. 提取内容树
    try:
        cursor = db_book.execute(
            "SELECT * FROM content_node WHERE book_id = ? ORDER BY node_order",
            (book_id,),
        )
        nodes = cursor.fetchall()

        for row in nodes:
            row_dict = dict(row)
            node_entry = {
                "section": "body",
                "type": row_dict.get("node_type", "text"),
                "text": row_dict.get("content", ""),
                "title": row_dict.get("title", ""),
                "level": row_dict.get("node_level", 0),
                "page_start": row_dict.get("page_start", 0),
                "page_end": row_dict.get("page_end", 0),
                "node_id": row_dict.get("node_id", ""),
            }
            final_doc["content"].append(node_entry)
    except Exception as e:
        logger.error("[%s] 提取内容节点失败: %s", book_id, e)

    # 1b. 若 content_node 为空（如单页图片 OCR），回退读 proofread_record
    if not final_doc["content"]:
        try:
            cursor = db_book.execute(
                "SELECT page_number, line_number, "
                "COALESCE(human_final_text, corrected_text, fused_text, original_text, '') AS line_text "
                "FROM proofread_record WHERE book_id = ? ORDER BY page_number, line_number",
                (book_id,),
            )
            for row in cursor.fetchall():
                line_text = row["line_text"]
                if line_text.strip():
                    final_doc["content"].append({
                        "section": "body",
                        "type": "text",
                        "text": line_text,
                        "page_start": row["page_number"],
                    })
            if final_doc["content"]:
                logger.info("[%s] 已从 proofread_record 回退加载 %d 行内容", book_id, len(final_doc["content"]))
        except Exception as e:
            logger.error("[%s] 从 proofread_record 加载内容失败: %s", book_id, e)

    # 2. 提取方剂
    try:
        cursor = db_book.execute(
            "SELECT * FROM formula_composition WHERE book_id = ? ORDER BY formula_sequence",
            (book_id,),
        )
        formulas = cursor.fetchall()

        for row in formulas:
            row_dict = dict(row)
            formula_id = row_dict.get("formula_id", "")

            # 查询药材
            ing_cursor = db_book.execute(
                "SELECT * FROM formula_ingredient WHERE formula_id = ? ORDER BY ingredient_order",
                (formula_id,),
            )
            ingredients = []
            for ing_row in ing_cursor.fetchall():
                ing = dict(ing_row)
                ingredients.append({
                    "herb_name": ing["herb_name"],
                    "dosage": ing.get("dosage", ""),
                    "unit": ing.get("dosage_unit", ""),
                    "processing": ing.get("processing_note", ""),
                })

            final_doc["formulas"].append({
                "formula_id": formula_id,
                "formula_name": row_dict.get("formula_name", ""),
                "sequence": row_dict.get("formula_sequence", 0),
                "page_numbers": row_dict.get("page_numbers", ""),
                "ingredients": ingredients,
                "source_text": row_dict.get("source_text", ""),
            })
    except Exception as e:
        logger.error("[%s] 提取方剂失败: %s", book_id, e)

    # 3. 统计信息
    try:
        stats_cursor = db_book.execute(
            """
            SELECT
                COUNT(*) as total_lines,
                SUM(CASE WHEN original_text != corrected_text THEN 1 ELSE 0 END) as corrected_lines,
                SUM(CASE WHEN disputed = 1 THEN 1 ELSE 0 END) as disputed_lines,
                SUM(CASE WHEN human_verified = 1 THEN 1 ELSE 0 END) as human_verified_lines,
                AVG(confidence) as avg_confidence
            FROM proofread_record WHERE book_id = ?
            """,
            (book_id,),
        )
        stats_row = stats_cursor.fetchone()
        if stats_row:
            final_doc["statistics"] = {
                "total_lines": stats_row["total_lines"] or 0,
                "corrected_lines": stats_row["corrected_lines"] or 0,
                "disputed_lines": stats_row["disputed_lines"] or 0,
                "human_verified_lines": stats_row["human_verified_lines"] or 0,
                "avg_confidence": round(stats_row["avg_confidence"] or 0, 4),
                "total_formulas": len(final_doc["formulas"]),
            }
    except Exception as e:
        logger.error("[%s] 提取统计信息失败: %s", book_id, e)
        final_doc["statistics"] = {
            "total_lines": 0,
            "corrected_lines": 0,
            "disputed_lines": 0,
            "human_verified_lines": 0,
            "avg_confidence": 0,
            "total_formulas": len(final_doc["formulas"]),
        }

    return final_doc


# =============================================================================
# 图片归档
# =============================================================================


def archive_images_for_final_doc(
    book_id: str,
    output_dir: str,
    disputed_lines: List[Dict[str, Any]],
) -> Dict[str, str]:
    """将争议行图片复制到输出目录的 assets/ 子目录。

    Args:
        book_id: 书籍 ID
        output_dir: 输出目录路径
        disputed_lines: 争议行列表，每项含 image_path 字段

    Returns:
        路径映射字典 {原路径: 新路径}
    """
    assets_dir = Path(output_dir) / OUTPUT_SUBDIR_ASSETS
    try:
        assets_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error("创建 assets 目录失败 %s: %s", assets_dir, e)
        return {}

    path_map: Dict[str, str] = {}

    for line in disputed_lines:
        src_path = line.get("image_path", line.get("disputed_image_path", ""))
        if not src_path:
            continue

        src = Path(src_path)
        if not src.exists():
            logger.warning("争议图片不存在: %s", src_path)
            continue

        # 生成目标文件名
        line_id = line.get("line_id", "unknown")
        dst_name = f"{book_id}_{line_id}{src.suffix}"
        dst = assets_dir / dst_name

        try:
            shutil.copy2(src, dst)
            path_map[str(src.resolve())] = str(dst)
            logger.debug("归档图片: %s -> %s", src, dst)
        except OSError as e:
            logger.error("复制图片失败 %s -> %s: %s", src, dst, e)

    return path_map


def _replace_image_paths(
    final_doc: Dict[str, Any],
    path_map: Dict[str, str],
) -> None:
    """将 final_doc 中的绝对图片路径替换为相对路径。

    就地修改 final_doc。

    Args:
        final_doc: 最终文档字典
        path_map: 路径映射 {绝对路径: 新路径}
    """
    if not path_map:
        return

    def _replace_in_value(value: object) -> object:
        if isinstance(value, str):
            for abs_path, new_path in path_map.items():
                value = value.replace(abs_path, new_path)
            return value
        elif isinstance(value, dict):
            return {k: _replace_in_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [_replace_in_value(v) for v in value]
        return value

    for key in list(final_doc.keys()):
        final_doc[key] = _replace_in_value(final_doc[key])


# =============================================================================
# SHA-256 校验
# =============================================================================


def _compute_file_sha256(file_path: Path) -> str:
    """计算文件的 SHA-256 哈希。

    Args:
        file_path: 文件路径

    Returns:
        SHA-256 十六进制字符串
    """
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


# =============================================================================
# 主导出函数
# =============================================================================


def export_final_outputs(
    book_id: str,
    output_dir: str,
    db_book: object,
    runtime_db: object,
) -> Dict[str, Any]:
    """导出书籍的最终交付物。

    流程：
    1. 从书籍库构建 final_doc
    2. 调用 MinerU-Popo 重建文档树（失败时降级通用 LLM）
    3. 渲染 body.md + full.md
    4. 归档争议图片到 assets/
    5. 写出 final_document.json
    6. SHA-256 记录

    Args:
        book_id: 书籍 ID
        output_dir: 输出目录
        db_book: SQLite 书籍数据库连接
        runtime_db: RuntimeDB（PostgreSQL）连接

    Returns:
        导出结果信息字典
    """
    logger.info("[%s] 开始导出最终交付物", book_id)
    start_time = datetime.now()

    out_path = Path(output_dir)
    try:
        out_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error("创建输出目录失败 %s: %s", out_path, e)
        raise

    # 1. 获取书籍元数据
    book_meta = _load_book_meta(db_book, book_id)

    # 2. 从书籍库构建 final_doc
    final_doc = _build_final_doc_from_book_db(book_id, db_book, book_meta)

    # 3. 尝试 MinerU-Popo 重建文档树
    document_tree: Dict[str, Any] = {"nodes": [], "sections": []}
    try:
        document_tree = _rebuild_with_mineru_popo(final_doc, runtime_db)
        logger.info("[%s] MinerU-Popo 文档树重建成功", book_id)
    except Exception as e:
        logger.warning("[%s] MinerU-Popo 不可用，降级通用 LLM: %s", book_id, e)
        try:
            document_tree = _rebuild_with_fallback_llm(final_doc, runtime_db)
            logger.info("[%s] 通用 LLM 文档树重建成功", book_id)
        except Exception as e2:
            logger.error("[%s] 文档树重建全部失败: %s", book_id, e2)
            # 降级：直接使用 final_doc 内容
            document_tree = {"nodes": final_doc.get("content", []), "sections": []}

    # 4. 渲染 Markdown
    body_md = render_body_markdown(book_meta, document_tree)
    full_md = render_full_markdown(book_meta, document_tree)

    # 5. 争议图片归档
    disputed_lines = _load_disputed_lines(db_book, book_id)
    path_map = archive_images_for_final_doc(book_id, output_dir, disputed_lines)
    _replace_image_paths(final_doc, path_map)

    # 6. 写出文件
    body_md_path = out_path / OUTPUT_SUBDIR_BODY_MD
    full_md_path = out_path / OUTPUT_SUBDIR_FULL_MD
    final_json_path = out_path / OUTPUT_SUBDIR_FINAL_JSON

    try:
        body_md_path.write_text(body_md, encoding="utf-8")
        logger.info("[%s] 已写出 %s", book_id, body_md_path)
    except OSError as e:
        logger.error("[%s] 写出 body.md 失败: %s", book_id, e)
        raise

    try:
        full_md_path.write_text(full_md, encoding="utf-8")
        logger.info("[%s] 已写出 %s", book_id, full_md_path)
    except OSError as e:
        logger.error("[%s] 写出 full.md 失败: %s", book_id, e)
        raise

    try:
        final_json_path.write_text(
            json.dumps(final_doc, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("[%s] 已写出 %s", book_id, final_json_path)
    except OSError as e:
        logger.error("[%s] 写出 final_document.json 失败: %s", book_id, e)
        raise

    # 7. SHA-256 校验文件
    checksums = _write_checksum_manifest(out_path)

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("[%s] 交付物导出完成，耗时 %.1f 秒", book_id, elapsed)

    return {
        "book_id": book_id,
        "output_dir": str(out_path),
        "files": {
            "body_md": str(body_md_path),
            "full_md": str(full_md_path),
            "final_json": str(final_json_path),
            "checksums": str(out_path / SHA256_MANIFEST_FILENAME),
        },
        "checksums": checksums,
        "image_count": len(path_map),
        "elapsed_seconds": elapsed,
    }


# =============================================================================
# 内部辅助函数
# =============================================================================


def _load_book_meta(db_book: object, book_id: str) -> Dict[str, Any]:
    """从数据库加载书籍元数据。

    Args:
        db_book: 数据库连接
        book_id: 书籍 ID

    Returns:
        元数据字典
    """
    try:
        cursor = db_book.execute(
            "SELECT * FROM book_metadata WHERE book_id = ?",
            (book_id,),
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
    except Exception as e:
        logger.warning("[%s] 加载元数据失败: %s", book_id, e)

    return {"title": "", "author": "", "publisher": "", "pub_year": ""}


def _rebuild_with_mineru_popo(
    final_doc: Dict[str, Any],
    runtime_db: object,
) -> Dict[str, Any]:
    """使用 MinerU-Popo 重建文档树。

    Args:
        final_doc: 最终文档数据
        runtime_db: RuntimeDB 连接

    Returns:
        文档树字典

    Raises:
        RuntimeError: MinerU-Popo 不可用或调用失败
    """
    # 尝试导入 MinerU-Popo 模块
    try:
        from kzocr.tcm_ocr.models.mineru_popo import MinerUPopoRebuilder

        rebuilder = MinerUPopoRebuilder()
        content_text = _concatenate_final_doc_text(final_doc)
        tree = rebuilder.rebuild(content_text, final_doc.get("metadata", {}))
        return tree
    except ImportError:
        raise RuntimeError("MinerU-Popo 模块未安装")
    except Exception as e:
        raise RuntimeError(f"MinerU-Popo 重建失败: {e}")


def _rebuild_with_fallback_llm(
    final_doc: Dict[str, Any],
    runtime_db: object,
) -> Dict[str, Any]:
    """使用通用 LLM（Qwen2.5-7B）重建文档树。

    Args:
        final_doc: 最终文档数据
        runtime_db: RuntimeDB 连接

    Returns:
        文档树字典

    Raises:
        RuntimeError: LLM 调用失败
    """
    from kzocr.tcm_ocr.utils.common import build_fallback_tree_prompt, parse_llm_json_with_retry

    content_text = _concatenate_final_doc_text(final_doc)
    prompt = build_fallback_tree_prompt(content_text)

    # 尝试调用 LLM
    try:
        # 优先使用云端 LLM
        llm_output = _call_cloud_llm(prompt)
    except Exception:
        # 降级本地 LLM
        llm_output = _call_local_llm(prompt)

    parsed = parse_llm_json_with_retry(llm_output, prompt)
    if parsed and "sections" in parsed:
        return {"nodes": parsed["sections"], "sections": parsed["sections"]}

    # 最终降级：扁平结构
    content = final_doc.get("content", [])
    return {"nodes": content, "sections": content}


def _concatenate_final_doc_text(final_doc: Dict[str, Any]) -> str:
    """将 final_doc 内容拼接为文本。

    Args:
        final_doc: 最终文档字典

    Returns:
        拼接后的文本
    """
    parts: List[str] = []
    meta = final_doc.get("metadata", {})
    if meta.get("title"):
        parts.append(f"# {meta['title']}")
    for item in final_doc.get("content", []):
        text = item.get("text", "") if isinstance(item, dict) else str(item)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _load_disputed_lines(db_book: object, book_id: str) -> List[Dict[str, Any]]:
    """加载争议行列表。

    Args:
        db_book: 数据库连接
        book_id: 书籍 ID

    Returns:
        争议行字典列表
    """
    try:
        cursor = db_book.execute(
            "SELECT line_id, original_text, corrected_text, "
            "dispute_reason, disputed_image_path "
            "FROM proofread_record "
            "WHERE book_id = ? AND disputed = 1",
            (book_id,),
        )
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.warning("[%s] 加载争议行失败: %s", book_id, e)
        return []


def _call_cloud_llm(prompt: str) -> str:
    """调用云端 LLM。

    Args:
        prompt: 提示词

    Returns:
        LLM 输出文本
    """
    import os

    api_key = os.environ.get("TCM_OCR_CLOUD_LLM_API_KEY", "")
    base_url = os.environ.get(
        "TCM_OCR_CLOUD_LLM_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    try:
        import openai

        client = openai.OpenAI(api_key=api_key, base_url=base_url)
        model = os.environ.get("TCM_OCR_CLOUD_LLM_MODEL", "qwen-max")
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4096,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.warning("云端 LLM 调用失败: %s", e)
        raise


def _call_local_llm(prompt: str) -> str:
    """调用本地 LLM。

    注意：若模型不存在于本地磁盘，立即抛出（不触发 HuggingFace 在线下载），
    从而让调用方降级为 final_doc 内容直接出 body.md。
    若有已下载的模型，设 TCM_OCR_FALLBACK_LLM_MODEL 指向模型目录即可。

    Args:
        prompt: 提示词

    Returns:
        LLM 输出文本
    """
    model_path = os.environ.get("TCM_OCR_FALLBACK_LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    # 如果路径不是本地存在的目录，大概率是 HuggingFace repo ID，跳过下载
    if not os.path.isdir(os.path.expanduser(model_path)):
        raise RuntimeError(
            f"本地 LLM 模型未下载（路径不存在）：{model_path}。"
            f"设 TCM_OCR_FALLBACK_LLM_MODEL 指向已有模型目录可启用。"
        )
    try:
        import transformers

        pipeline = transformers.pipeline(
            "text-generation",
            model=model_path,
            device_map="auto",
            torch_dtype="auto",
        )
        messages = [{"role": "user", "content": prompt}]
        output = pipeline(messages, max_new_tokens=4096, temperature=0.1)
        return output[0]["generated_text"][-1].get("content", "") if output else ""
    except Exception as e:
        logger.warning("本地 LLM 调用失败: %s", e)
        raise


def _write_checksum_manifest(output_dir: Path) -> Dict[str, str]:
    """写出 SHA-256 校验文件。

    Args:
        output_dir: 输出目录

    Returns:
        文件路径到哈希值的映射
    """
    manifest_path = output_dir / SHA256_MANIFEST_FILENAME
    checksums: Dict[str, str] = {}
    lines: List[str] = []

    for filepath in sorted(output_dir.iterdir()):
        if filepath.is_file() and filepath.name != SHA256_MANIFEST_FILENAME:
            file_hash = _compute_file_sha256(filepath)
            rel_path = filepath.name
            checksums[rel_path] = file_hash
            lines.append(f"{file_hash}  {rel_path}")

    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksums


# =============================================================================
# 交付物校验
# =============================================================================


def verify_deliverables(book_id: str, output_dir: str) -> bool:
    """校验交付物完整性。

    检查：
    1. body.md 存在且非空
    2. full.md 存在且非空
    3. final_document.json 存在且为有效 JSON
    4. SHA-256 校验文件存在

    Args:
        book_id: 书籍 ID
        output_dir: 输出目录路径

    Returns:
        全部检查通过返回 True
    """
    out_path = Path(output_dir)
    if not out_path.exists():
        logger.error("[%s] 输出目录不存在: %s", book_id, output_dir)
        return False

    checks = {
        "body_md": out_path / OUTPUT_SUBDIR_BODY_MD,
        "full_md": out_path / OUTPUT_SUBDIR_FULL_MD,
        "final_json": out_path / OUTPUT_SUBDIR_FINAL_JSON,
        "checksums": out_path / SHA256_MANIFEST_FILENAME,
    }

    all_pass = True

    for name, filepath in checks.items():
        if not filepath.exists():
            logger.error("[%s] 缺失文件: %s", book_id, filepath)
            all_pass = False
            continue

        if filepath.stat().st_size == 0:
            logger.error("[%s] 文件为空: %s", book_id, filepath)
            all_pass = False
            continue

        # final_document.json 额外校验 JSON 有效性
        if name == "final_json":
            try:
                content = filepath.read_text(encoding="utf-8")
                data = json.loads(content)
                if not isinstance(data, dict):
                    logger.error("[%s] final_document.json 不是字典", book_id)
                    all_pass = False
                elif "book_id" not in data:
                    logger.error("[%s] final_document.json 缺少 book_id", book_id)
                    all_pass = False
            except json.JSONDecodeError as e:
                logger.error("[%s] final_document.json JSON 解析失败: %s", book_id, e)
                all_pass = False

        logger.info("[%s] 校验通过: %s", book_id, filepath)

    # SHA-256 校验
    manifest_path = out_path / SHA256_MANIFEST_FILENAME
    if manifest_path.exists():
        try:
            manifest_content = manifest_path.read_text(encoding="utf-8")
            for line in manifest_content.strip().split("\n"):
                parts = line.strip().split("  ", 1)
                if len(parts) == 2:
                    expected_hash, filename = parts
                    file_path = out_path / filename
                    if file_path.exists():
                        actual_hash = _compute_file_sha256(file_path)
                        if expected_hash != actual_hash:
                            logger.error(
                                "[%s] SHA-256 不匹配: %s (期望 %s, 实际 %s)",
                                book_id, filename, expected_hash, actual_hash,
                            )
                            all_pass = False
        except Exception as e:
            logger.error("[%s] SHA-256 校验过程出错: %s", book_id, e)
            all_pass = False

    if all_pass:
        logger.info("[%s] 全部交付物校验通过", book_id)
    else:
        logger.error("[%s] 交付物校验未通过", book_id)

    return all_pass
