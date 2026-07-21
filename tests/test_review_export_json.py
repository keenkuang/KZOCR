"""review_manifest JSON 导出纯逻辑单测（零外部资源）。

覆盖 #2 校对台/导出增强：``export_review_manifest_json`` 将审核清单序列化为
JSON，供外部校对台 / CI / 数据交换消费。
"""
from __future__ import annotations

import json

from kzocr.scheduler.review_manifest import (
    ReviewIssue,
    ReviewManifest,
    ReviewPageItem,
    export_review_manifest_json,
)


def _sample_manifest() -> ReviewManifest:
    return ReviewManifest(
        book_code="BC-001",
        pages=[
            ReviewPageItem(
                page_num=1,
                priority="P0",
                engine_results={"kimi": "麤"},
                issues=[ReviewIssue(position=0, ocr_char="麤", expected="粗")],
            ),
            ReviewPageItem(
                page_num=2,
                priority="P1",
                engine_results={},
                issues=[],
            ),
        ],
    )


def test_export_review_manifest_json_structure(tmp_path) -> None:
    out = tmp_path / "review.json"
    path = export_review_manifest_json(_sample_manifest(), out_path=str(out))
    assert path == str(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["book_code"] == "BC-001"
    assert len(data["pages"]) == 2
    p0 = data["pages"][0]
    assert p0["page_num"] == 1
    assert p0["priority"] == "P0"
    assert p0["engine_results"] == {"kimi": "麤"}
    assert p0["issues"][0]["ocr_char"] == "麤"
    assert p0["issues"][0]["expected"] == "粗"
    # 无 issue 的页也被保留
    assert data["pages"][1]["priority"] == "P1"
    assert data["pages"][1]["issues"] == []


def test_export_review_manifest_json_default_name(tmp_path, monkeypatch) -> None:
    # 不传 out_path → 默认写 <book_code>_review_manifest.json 到当前目录
    monkeypatch.chdir(tmp_path)
    path = export_review_manifest_json(_sample_manifest())
    assert path == "BC-001_review_manifest.json"
    assert (tmp_path / path).exists()
