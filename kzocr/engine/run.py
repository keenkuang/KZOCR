"""引擎驱动器：把一份 PDF 跑成归一化的 BookResult。

策略：
1. 若 KZOCR_USE_MOCK=1 → 直接返回桩数据。
2. 若 KZOCR_USE_VLM=1 → 绕过 BookPipeline，用 PaddleOCR-VL-1.6 逐页 VLM OCR。
3. 否则尝试调用 kimi 的 tcm_ocr 真实管线（BookPipeline）；任何失败都降级到桩数据，
   除非 KZOCR_REQUIRE_REAL=1（此时真实失败会抛出，便于排查）。
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path

import fitz
import numpy as np

from kzocr import config as app_config
from .mock import mock_book_result as build_mock_book
from .types import BookResult, PageResult, ParagraphResult, LineResult
from kzocr.engines.errors import (
    ApiError,
    OcrError,
    OverSizeError,
    RateLimitedError,
    RetryExhaustedError,
    BACKOFF_CONFIGS,
    retry_with_policy,
)
from kzocr.engines.leakage import CharCountBaseline, apply_leakage_defense

logger = logging.getLogger(__name__)

# VLM 引擎标签（便于在数据库/日志中识别）
VLM_ENGINE_LABEL = "PaddleOCR-VL-1.6"


def run_engine(pdf_path: str, book_code: str | None = None, config=None) -> BookResult:
    cfg = config if config is not None else app_config.config
    if cfg.use_mock:
        logger.info("[engine] use_mock=True，使用桩数据")
        return build_mock_book(book_code=book_code or "TCM-MOCK-001")

    if cfg.use_vlm:
        try:
            return _run_vlm(pdf_path, cfg, book_code)
        except Exception as exc:  # noqa: BLE001
            if cfg.require_real:
                raise
            logger.error("[engine] ⚠ VLM 引擎执行失败，已降级为占位【假数据】（非真实 OCR）：%s", exc)
            print("⚠ 警告：VLM 引擎失败，本次产出为占位假数据，并非真实 OCR 结果。")
            return build_mock_book(book_code=book_code or "TCM-MOCK-001")

    try:
        return _run_real(pdf_path, cfg, book_code)
    except Exception as exc:  # noqa: BLE001
        logger.error("[engine] ⚠ 真实引擎执行失败: %s", exc)
        print("⚠ 警告：真实引擎失败，本次产出为占位假数据，并非真实 OCR 结果。")
        if cfg.require_real:
            raise
        return build_mock_book(book_code=book_code or "TCM-MOCK-001")


def _build_engine_config() -> dict:
    """从环境变量构造 kimi BookPipeline 所需的 config 字典。

    需要的最小集：
        KZOCR_ENGINE_LIB_DIR  书籍库/交付物输出目录（必须可写，默认避开 /mnt/agents）
        KZOCR_PG_DSN          PostgreSQL DSN（留空则禁用 PG 归档）
        KZOCR_TERM_KB_PATH    术语知识库路径（可选）
        KZOCR_LLM_ENABLED     是否启用云端 LLM 校对（1/true 开启，否则仅 OCR 校对）
        KZOCR_LLM_API_KEY / KZOCR_LLM_BASE_URL / KZOCR_LLM_MODEL
        KZOCR_GPU_MAP         "auto" 或设备映射
        KZOCR_PUBLISHER_BONUS 出版社准确率奖励（浮点）
    """
    cloud_llm = {
        "enabled": os.environ.get("KZOCR_LLM_ENABLED", "0") in ("1", "true", "True"),
        "api_key": os.environ.get("KZOCR_LLM_API_KEY", ""),
        "base_url": os.environ.get("KZOCR_LLM_BASE_URL", ""),
        "model": os.environ.get("KZOCR_LLM_MODEL", "qwen-max"),
    }
    return {
        "book_library_dir": os.environ.get("KZOCR_ENGINE_LIB_DIR", ""),
        "output_dir": os.environ.get("KZOCR_ENGINE_OUTPUT_DIR", ""),
        "pg_dsn": os.environ.get("KZOCR_PG_DSN", ""),
        "engine_configs": {
            "paddleocr": {
                "use_gpu": os.environ.get("KZOCR_PADDLE_GPU", "0") in ("1", "true", "True"),
            },
            "rapidocr": {"enabled": True},
            "unirec": {"enabled": True},
            "paddleocr_vl16": {"enabled": False, "auto_start": False},
            "shizhengpt": {"enabled": False, "auto_start": False},
            "mineru": {"enabled": True, "use_gpu": False},
            "tesseract": {"enabled": False},
            "cloud_llm": cloud_llm,
        },
        "gpu_device_map": os.environ.get("KZOCR_GPU_MAP", "auto"),
        "term_kb_path": os.environ.get("KZOCR_TERM_KB_PATH", ""),
        "publisher_bonus": float(os.environ.get("KZOCR_PUBLISHER_BONUS", "0.02")),
    }


def _map_cloudllm_env() -> None:
    """将 KZOCR_LLM_* 环境变量映射到 CloudLLMClient 使用的 GLM_*。

    CloudLLMClient 从环境变量读取配置（GLM_API_KEY 等），
    而 KZOCR 用户配置的是 KZOCR_LLM_* 变量。
    仅在目标变量（GLM_*）未设置时才做映射。
    """
    if os.environ.get("KZOCR_LLM_API_KEY") and not os.environ.get("GLM_API_KEY"):
        os.environ["GLM_API_KEY"] = os.environ["KZOCR_LLM_API_KEY"]
    if os.environ.get("KZOCR_LLM_BASE_URL") and not os.environ.get("GLM_API_BASE"):
        os.environ["GLM_API_BASE"] = os.environ["KZOCR_LLM_BASE_URL"]
    if os.environ.get("KZOCR_LLM_MODEL") and not os.environ.get("GLM_MODEL"):
        os.environ["GLM_MODEL"] = os.environ["KZOCR_LLM_MODEL"]


def _run_real(pdf_path: str, cfg, book_code: str | None = None) -> BookResult:
    """调用 kimi tcm_ocr 的 BookPipeline（需安装引擎依赖并配置环境）。"""
    _map_cloudllm_env()

    engine_dir = Path(str(cfg.kimi_engine_dir))
    if not engine_dir.exists():
        raise RuntimeError(f"未找到 kimi 引擎目录：{engine_dir}（请设置 KIMI_ENGINE_DIR）")

    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))

    from tcm_ocr.pipeline.book_pipeline import BookPipeline

    engine_config = _build_engine_config()
    book_id = book_code or "KZOCR-real"
    logger.info("[engine] 调用 kimi BookPipeline 处理 %s（%s）", os.path.basename(pdf_path), book_id)
    pipeline = BookPipeline(engine_config)
    result = pipeline.process_book(pdf_path, book_id)
    logger.info("[engine] BookPipeline 完成，读取交付物…")

    final_md = _read_deliverable(result, engine_config["book_library_dir"], book_id)
    meta = getattr(pipeline, "current_book_meta", {}) or {}
    title = meta.get("title") or os.path.basename(pdf_path)
    book = BookResult(
        book_code=book_id,
        title=title,
        engine_label="kimi",
        final_markdown=final_md or "",
    )
    # H4 修复：真实引擎路径若未给出结构化 pages（如仅 final_markdown），
    # 从 Markdown 重建至少一页多段落，保证 zai 校对台非空（pageCount>0）。
    if not book.pages and book.final_markdown:
        book.pages = _markdown_to_pages(book.final_markdown, book_id)
        logger.warning("[engine] 真实引擎未返回结构化 pages，已从 final_markdown 重建 %d 页", len(book.pages))
    return book


def _read_deliverable(result, lib_dir: str, book_id: str) -> str:
    """从 BookPipeline.process_book 的返回字典里取出最终 Markdown。"""
    if isinstance(result, dict):
        for attr in ("final_markdown", "markdown", "final_text"):
            val = result.get(attr)
            if isinstance(val, str) and val.strip():
                return val
        outputs = result.get("outputs")
    else:
        outputs = getattr(result, "outputs", None)

    if isinstance(outputs, dict):
        for p in outputs.values():
            if isinstance(p, str) and p.endswith(".md") and os.path.exists(p):
                return Path(p).read_text(encoding="utf-8", errors="ignore")

    base = Path(lib_dir) / book_id
    if base.exists():
        for name in ("body.md", "full.md", "final_document.md"):
            cand = base / name
            if cand.exists():
                return cand.read_text(encoding="utf-8", errors="ignore")
        mds = sorted(base.rglob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)
        if mds:
            return mds[0].read_text(encoding="utf-8", errors="ignore")
    return ""


def _markdown_to_pages(markdown: str, book_code: str) -> list:
    """把 final_markdown 拆成 PageResult[]（保底，使校对台非空）。

    优先按 '## 第 N 页' 分段；无标记则整本作为单页多行。
    """
    chunks = re.split(r"(?m)^##\s*第\s*\d+\s*页\s*$", markdown)
    pages: list = []
    idx = 1
    for seg in chunks[1:]:
        text = seg.strip()
        if not text:
            continue
        lines = [
            LineResult(sequence_in_paragraph=i + 1, consensus=ln, final=ln,
                       engine_texts={"kimi": ln})
            for i, ln in enumerate(text.splitlines()) if ln.strip()
        ]
        paras = [ParagraphResult(sequence_in_page=1, lines=lines)] if lines else []
        pages.append(PageResult(page_num=idx, paragraphs=paras))
        idx += 1
    if not pages:
        lines = [
            LineResult(sequence_in_paragraph=i + 1, consensus=ln, final=ln,
                       engine_texts={"kimi": ln})
            for i, ln in enumerate(markdown.splitlines()) if ln.strip()
        ]
        pages = [PageResult(page_num=1, paragraphs=[ParagraphResult(sequence_in_page=1, lines=lines)])]
    return pages


# =============================================================================
# VLM 直接集成（PaddleOCR-VL-1.6 / llama-server）
# =============================================================================


def _init_vlm_adapter(cfg) -> object:
    """初始化 VLM 适配器，带 SenseNova → PaddleOCR-VL 降级链。

    引擎选择（按优先级）：
    1. cfg.vlm_engine == "sensenova" → 强制 SenseNova
    2. cfg.vlm_engine == "auto" 且有 SENSENOVA_API_KEY → 先试 SenseNova
    3. 否则 → PaddleOCR-VL-1.6（本地 llama-server）
    """
    engine_dir = str(cfg.kimi_engine_dir)
    if engine_dir and engine_dir not in sys.path:
        sys.path.insert(0, engine_dir)

    # 判断是否优先尝试 SenseNova
    try_sensenova = (
        cfg.vlm_engine == "sensenova"
        or (cfg.vlm_engine == "auto" and bool(cfg.sensenova_api_key))
    )

    if try_sensenova:
        try:
            from tcm_ocr.core.engines.sensenova_adapter import SenseNovaAdapter

            adapter = SenseNovaAdapter(
                api_key=cfg.sensenova_api_key,
                model=cfg.sensenova_model,
                base_url=cfg.sensenova_base_url,
                timeout=cfg.sensenova_timeout,
            )
            adapter.engine_label = "SenseNova"
            logger.info("[VLM] 使用 SenseNova API（model=%s）", cfg.sensenova_model)
            return adapter
        except Exception as exc:
            logger.warning("[VLM] SenseNova 不可用，降级到 PaddleOCR-VL：%s", exc)

    # 降级到 PaddleOCR-VL-1.6（本地 llama-server）
    from tcm_ocr.core.engines.paddleocr_vl16_adapter import PaddleOCRVl16Adapter

    adapter = PaddleOCRVl16Adapter(
        host=cfg.vlm_host,
        port=cfg.vlm_port,
        auto_start=True,
    )
    adapter.engine_label = "PaddleOCR-VL-1.6"
    logger.info("[VLM] 使用 PaddleOCR-VL-1.6（本地 llama-server）")
    return adapter


def _pdf_page_to_numpy(page, dpi: int = 150) -> np.ndarray:
    """将 PyMuPDF page 渲染为 (H, W, 3) RGB numpy 数组。

    自动处理 RGBA（alpha 通道）和灰度页面的通道转换。
    """
    mat = fitz.Matrix(dpi / 72, dpi / 72)  # 默认 150 DPI
    pix = page.get_pixmap(matrix=mat)
    # RGBA(alpha) 或灰度(n=1) → 转 RGB；否则 reshape(...,3) 会因样本数不符报错
    if pix.n != 3:
        pix = fitz.Pixmap(pix, 0)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)


def _crop_to_body(img: np.ndarray) -> np.ndarray:
    """通过水平投影裁剪版心，去除页眉/页脚/侧边空白。

    使用 numpy 做投影分析，轻量无额外依赖。
    返回裁剪后的 (H', W', 3) RGB 数组。
    """
    h, w = img.shape[:2]
    gray = np.mean(img, axis=2)  # 灰度
    # 每行暗像素比例（文字区域）
    row_dark = np.mean(gray < 128, axis=1)

    # 找正文上下边界：跳过顶部/底部稀疏行
    threshold = 0.01
    top = 0
    for y in range(h):
        if row_dark[y] > threshold:
            top = max(0, y - int(h * 0.02))  # 留 2% 上边距
            break
    bottom = h
    for y in range(h - 1, -1, -1):
        if row_dark[y] > threshold:
            bottom = min(h, y + int(h * 0.02))  # 留 2% 下边距
            break

    # 垂直投影找左右边界
    col_dark = np.mean(gray[top:bottom, :] < 128, axis=0)
    left = 0
    for x in range(w):
        if col_dark[x] > threshold:
            left = max(0, x - int(w * 0.02))
            break
    right = w
    for x in range(w - 1, -1, -1):
        if col_dark[x] > threshold:
            right = min(w, x + int(w * 0.02))
            break

    return img[top:bottom, left:right]


def _vlm_markdown_to_pages(pages_text: list[str]) -> list[PageResult]:
    """将 VLM 输出的逐页文本拆行为 PageResult[]，供 zai 逐行展示。"""
    results = []
    for page_idx, text in enumerate(pages_text):
        lines = []
        for seq, line in enumerate(text.strip().split("\n")):
            line = line.strip()
            if not line:
                continue
            lines.append(LineResult(
                sequence_in_paragraph=seq + 1,
                consensus=line,
                final=line,
                engine_texts={VLM_ENGINE_LABEL: line},
            ))
        paragraphs = []
        if lines:
            paragraphs.append(ParagraphResult(sequence_in_page=1, lines=lines))
        results.append(PageResult(page_num=page_idx + 1, paragraphs=paragraphs))
    return results


# VLM 常见输出噪声的正则替换
_VLM_CLEANUP_RULES = [
    (re.compile(r"\\([()])"), r"\1"),                # \( → (,  \) → )
    (re.compile(r"[♡♝◇◁]"), "："),                    # 字段分隔符标准化
    (re.compile(r"秘方求真\s*\\?\(?\s*R\s*\\?\)?.*", re.MULTILINE), ""),  # 页眉页脚 "秘方求真 R"
    (re.compile(r"秘方求真$", re.MULTILINE), ""),      # 行末的 "秘方求真"
]


def _vlm_postprocess(text: str) -> str:
    """清理 VLM 输出中的常见格式噪声。"""
    for pattern, replacement in _VLM_CLEANUP_RULES:
        text = pattern.sub(replacement, text)
    # 清理多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── D2: VLM 单页处理（含输出长度检查）──


def _process_vlm_page(vlm, img: np.ndarray, supports_two_page: bool, next_img: np.ndarray | None = None) -> str:
    """处理单页 VLM 识别。返回文本。若文本过长可能抛出 OverSizeError。"""
    imgs = [img]
    if supports_two_page and next_img is not None:
        imgs.append(next_img)
    text: str
    if supports_two_page:
        text = vlm.recognize_pages(imgs)
    else:
        text = vlm.recognize_page(imgs[0])
    # D2: 检查输出文本是否过长（超过 8000 字 ≈ 16k tokens，超过 VLM 常见输出上限）
    # 注意: 这不是精确 token 计数，而是保守的字符数保护
    if len(text) > 8000:
        raise OverSizeError(
            f"VLM output too long: {len(text)} chars (limit: 8000)",
        )
    return text


# =============================================================================
# 跨页断裂合并
# =============================================================================

# 方剂/章节标题行的特征（以编号开头或标准科室头）
_PAGE_BREAK_HEADER = re.compile(
    r"^(\d+\.\d+\s|治|§|第)", re.MULTILINE
)


# 句末标点集合（用于判断页末行是否完整）
_SENTENCE_END = set("。！？；）】」\"'")


def _merge_cross_page_breaks(pages_text: list[str]) -> list[str]:
    """检测方剂在页末断裂，将下页起始续接行合并到本页末尾。

    判断逻辑：若本页末尾是未完成行（以顿号/逗号结尾）、
    且下页开头没有独立方剂标题，则合并下页首行到本页。
    """
    if len(pages_text) < 2:
        return pages_text

    result = list(pages_text)
    # 从后往前合并，避免索引失效
    for i in range(len(result) - 2, -1, -1):
        cur = result[i].strip()
        nxt = result[i + 1].strip()
        if not cur or not nxt:
            continue


        # 本页末行不完整检测：不以句末标点结尾，且不是独立行
        cur_lines = cur.split("\n")
        last_line = cur_lines[-1].strip()
        if not last_line:
            continue

        ends_without_period = (
            last_line[-1] not in _SENTENCE_END
            and not last_line.endswith("克。")
        )
        # 排除：编号章节标题（如"29 治食管瘤秘方"、"26.4 鳖甲消瘤方"）
        is_chapter_title = bool(re.match(r"^\d+\.?\d*\s+\S", last_line))
        is_broken = ends_without_period and not is_chapter_title
        if not is_broken:
            continue

        # 下页所有有效续接行（跳过装饰/标题行），并入本页末尾
        nxt_lines = nxt.split("\n")
        cont_lines = []
        remaining_lines = []
        nxt_in_cont = True  # 是否仍在续接段落中
        for l in nxt_lines:
            stripped = l.strip()
            if not stripped:
                remaining_lines.append(l)
                continue
            if nxt_in_cont:
                # 跳过页装饰行
                if re.match(r"[【\-]", stripped):
                    remaining_lines.append(l)
                    continue
                # 遇到独立标题标记 → 续接结束
                if _PAGE_BREAK_HEADER.match(stripped):
                    nxt_in_cont = False
                    remaining_lines.append(l)
                    continue
                # 遇到字段标识 → 续接结束（除非行首以"翘"类续接字开头）
                if re.match(r"^来源|^组成|^用法|^功用|^方解|^主治|^加减|^疗效|^附记", stripped):
                    nxt_in_cont = False
                    remaining_lines.append(l)
                    continue
                cont_lines.append(stripped)
            else:
                remaining_lines.append(l)

        if not cont_lines:
            continue
        logger.debug("[VLM 跨页合并] P%d ← P%d: %d 行", i + 1, i + 2, len(cont_lines))
        result[i] = cur + "\n" + "\n".join(cont_lines)
        result[i + 1] = "\n".join(remaining_lines).strip()
    return result


def _run_vlm(pdf_path: str, cfg, book_code: str | None = None) -> BookResult:
    """绕过 BookPipeline，用 PaddleOCR-VL-1.6 直接逐页 VLM OCR。

    流程：PDF 渲染 → VLM 逐页识别 → Markdown 拼接 + 结构化填充 → BookResult。
    """
    vlm = _init_vlm_adapter(cfg)

    title = os.path.basename(pdf_path)
    raw_book_code = book_code or os.path.splitext(title)[0]
    # book_code 只保留安全字符（ASCII 字母/数字/连字符/下划线）
    safe_book_code = re.sub(r"[^A-Za-z0-9_\-]", "_", raw_book_code)

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    if total_pages == 0:
        doc.close()
        raise ValueError(f"PDF 文件为空（0 页）：{pdf_path}")

    try:
        pages_text: list[str] = []
        # C1: 初始化字符数基线（前 50 页建立中位数基线）
        baseline = CharCountBaseline(window=50)
        # D2: 失败页记录 {page_num: reason}
        failed_pages: dict[int, str] = {}
        # 是否支持双页上下文（SenseNova 适配器）
        supports_two_page = hasattr(vlm, "recognize_pages")

        # 一次展开所有页（避免 doc[i+1] 在 mock 下出错）
        all_pages = list(doc)
        # 页数上限保护（防资源耗尽 DoS），B6 裁决默认 50
        max_pages = int(os.environ.get("KZOCR_MAX_PAGES", "50"))
        if max_pages and len(all_pages) > max_pages:
            logger.warning("[VLM] PDF 页数 %d 超过上限 %d，仅处理前 %d 页", len(all_pages), max_pages, max_pages)
            all_pages = all_pages[:max_pages]
            total_pages = len(all_pages)

        # B6: wall-clock 总预算闸（到点即停后续页）
        total_timeout = int(os.environ.get("KZOCR_TOTAL_TIMEOUT", "7200"))
        _start_ts = time.monotonic()

        for i, page in enumerate(all_pages):
            if i > 0 and i % 5 == 0:
                logger.info("[VLM] 已识别 %d/%d 页", i, total_pages)
            # B6: 检查总时间预算
            elapsed = time.monotonic() - _start_ts
            if elapsed > total_timeout:
                logger.warning(
                    "[VLM] 总时间 %.1fs 超过预算 %ds，提前终止（已处理 %d/%d 页）",
                    elapsed, total_timeout, i, total_pages,
                )
                break
            # --- D2: structured error handling ---
            try:
                img = _crop_to_body(_pdf_page_to_numpy(page))
                # 双页上下文（SenseNova 模式）
                next_ctx = None
                if supports_two_page and i < len(all_pages) - 1:
                    next_full = _pdf_page_to_numpy(all_pages[i + 1])
                    h = next_full.shape[0]
                    next_ctx = next_full[:int(h * 0.15), :, :]
                processed_text = _process_vlm_page(vlm, img, supports_two_page, next_ctx)
            except (ApiError, RateLimitedError) as exc:
                # D2: transient API error → retry with exponential backoff
                def _retry_fn():
                    retry_img = _crop_to_body(_pdf_page_to_numpy(page))
                    retry_ctx = None
                    if supports_two_page and i < len(all_pages) - 1:
                        nf = _pdf_page_to_numpy(all_pages[i + 1])
                        retry_ctx = nf[:int(nf.shape[0] * 0.15), :, :]
                    return _process_vlm_page(vlm, retry_img, supports_two_page, retry_ctx)
                try:
                    processed_text = retry_with_policy(
                        _retry_fn,
                        backoff=BACKOFF_CONFIGS["api"],
                        error_types=(ApiError,),
                    )
                except RetryExhaustedError as rexc:
                    failed_pages[i + 1] = f"API error after retries: {rexc}"
                    logger.warning("[VLM] 第 %d 页 API 错误重试耗尽，跳过：%s", i + 1, rexc)
                    continue
            except OverSizeError:
                # D2: 输出过长 → 降低 DPI 后重试
                logger.info("[VLM] 第 %d 页输出过长，降低 DPI 重试", i + 1)
                try:
                    lo_img = _crop_to_body(_pdf_page_to_numpy(page, dpi=72))
                    processed_text = retry_with_policy(
                        lambda: _process_vlm_page(vlm, lo_img, supports_two_page, None),
                        backoff=BACKOFF_CONFIGS["oversize"],
                        error_types=(OverSizeError,),
                    )
                except RetryExhaustedError:
                    failed_pages[i + 1] = "OverSize even after reduced DPI"
                    logger.warning("[VLM] 第 %d 页降低 DPI 后仍超长，跳过", i + 1)
                    continue
            except OcrError as exc:
                # D2: non-retriable OCR failure → skip page
                failed_pages[i + 1] = f"OCR failed: {exc}"
                logger.warning("[VLM] 第 %d 页 OCR 失败，跳过：%s", i + 1, exc)
                continue
            except Exception as exc:  # noqa: BLE001
                failed_pages[i + 1] = f"Unexpected error: {exc}"
                logger.warning("[VLM] 第 %d 页未知异常，跳过：%s", i + 1, exc)
                continue
            # --- end D2 ---
            pages_text.append(processed_text)
            # C1: 向基线注册当前页字数
            baseline.feed(processed_text)
        if not any(pages_text):
            raise RuntimeError(f"VLM 全部 {total_pages} 页识别均失败")

        # C1: 跨页泄漏防御（L1-L4 四层，在跨页合并之前执行）
        pages_text = apply_leakage_defense(pages_text, baseline)

        # 跨页合并：检测方剂在页末断裂，将下页续接行合并到本页末尾
        pages_text = _merge_cross_page_breaks(pages_text)

        # final_markdown 带页标题，_vlm_markdown_to_pages 只取原文
        pages_text_clean = [_vlm_postprocess(t) for t in pages_text]
        full_md = "\n\n".join(
            f"## 第 {i + 1} 页\n\n{t}" for i, t in enumerate(pages_text_clean)
        )
        pages = _vlm_markdown_to_pages(pages_text_clean)

        return BookResult(
            book_code=safe_book_code,
            title=title,
            engine_label=getattr(vlm, "engine_label", VLM_ENGINE_LABEL),
            final_markdown=full_md,
            pages=pages,
            failed_pages=failed_pages,
        )
    finally:
        doc.close()
