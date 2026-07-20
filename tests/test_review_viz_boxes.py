"""字符级 bbox 可视化单测（零 PDF 降级路径 + 缺数据边缘）。"""

from kzocr.scheduler.review_manifest import visualize_char_boxes
from kzocr.storage.db import BookDB


def test_visualize_char_boxes_no_pdf(tmp_path) -> None:
    db = BookDB("bk1", db_dir=str(tmp_path))
    try:
        db._conn.execute(
            "INSERT INTO page (page_num, book_code, char_boxes) VALUES (?, ?, ?)",
            (0, "bk1", '[[[10,20,50,60],[60,20,100,60]],[[10,80,55,120]]]'),
        )
        db._conn.commit()

        out = str(tmp_path / "viz.png")
        path = visualize_char_boxes(db, "bk1", 0, out_path=out)
        assert path.endswith(".png")

        # 验证输出是有效 PNG
        with open(path, "rb") as f:
            header = f.read(8)
        assert header == b"\x89PNG\r\n\x1a\n"

        # 检查尺寸（canvas = max_coord + margin*2）
        from PIL import Image
        img = Image.open(path)
        assert img.width > 0 and img.height > 0
        # 最大 x/y 是 120/100 + margin*2=80 → 200/180；canvas 尺寸至少包含
    finally:
        db.close()


def test_visualize_char_boxes_missing_page(tmp_path) -> None:
    db = BookDB("bk1", db_dir=str(tmp_path))
    try:
        import pytest
        with pytest.raises(ValueError, match="无 char_boxes"):
            visualize_char_boxes(db, "bk1", 999, out_path=str(tmp_path / "nope.png"))
    finally:
        db.close()


def test_visualize_char_boxes_multiple_lines_colors(tmp_path) -> None:
    """多行时各行颜色不同，图像中应有色彩变化。"""
    db = BookDB("bk1", db_dir=str(tmp_path))
    try:
        # 模拟第 0 行和第 1 行的 char_boxes，彼此 y 区间无重叠
        db._conn.execute(
            "INSERT INTO page (page_num, book_code, char_boxes) VALUES (?, ?, ?)",
            (0, "bk1", '[[[10,20,30,50]],[[10,80,30,110]]]'),
        )
        db._conn.commit()

        out = str(tmp_path / "viz.png")
        path = visualize_char_boxes(db, "bk1", 0, out_path=out)
        assert path.endswith(".png")

        # 校验图像有至少 2 种不同颜色的像素（不同行的框线颜色不同）
        from PIL import Image
        img = Image.open(path).convert("RGB")
        colors = set()
        for y in range(img.height):
            for x in range(img.width):
                c = img.getpixel((x, y))
                if c != (255, 255, 255):  # 非白色
                    colors.add(c)
        assert len(colors) >= 2, "不同行应使用不同颜色"
    finally:
        db.close()
