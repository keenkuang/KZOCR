"""
书籍处理主流水线模块。

BookPipeline 是中医现代出版物 OCR 校对系统的核心编排器，
负责协调所有组件完成从 PDF 到最终交付物的完整处理流程。

处理流程：
1. PDF 渲染
2. 元数据提取
3. 目录处理
4. 按章循环处理页面（版式分类 → 多引擎识别 → LLM 校对 → 方剂提取）
5. 人工核验
6. 第二次剂量校验
7. 阶段 CER 回填
8. 自动发现
9. 数据归档（SQLite → PostgreSQL）
10. 生成最终交付物（Markdown + JSON）
11. 清理书籍库
"""

import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from kzocr.tcm_ocr.config.constants import (
    DPI,
)
from kzocr.tcm_ocr.pipeline.archival import archive_to_postgresql, cleanup_book_directory
from kzocr.tcm_ocr.pipeline.auto_discovery import _run_auto_discovery
from kzocr.tcm_ocr.pipeline.deliverables import (
    export_final_outputs,
    verify_deliverables,
)
from kzocr.tcm_ocr.pipeline.page_pipeline import PagePipeline

logger = logging.getLogger(__name__)


class BookPipeline:
    """书籍处理主流水线。

    协调 PDF 渲染、OCR 识别、校对、方剂提取、交付物生成等全流程。

    Attributes:
        config: 系统配置字典
        db_book: SQLite 书籍数据库连接
        db_pg: PostgreSQL 连接
        engines: OCR 引擎字典
        term_kb: 术语知识库
        page_pipeline: 单页处理流水线实例
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """初始化书籍处理流水线。

        初始化所有组件：数据库连接、OCR 引擎、LLM、验证器、提取器。
        支持 GPU 资源声明机制。

        Args:
            config: 系统配置字典，需包含：
                - book_library_dir: 书籍库目录
                - pg_dsn: PostgreSQL DSN
                - engine_configs: 引擎配置
                - gpu_device_map: GPU 设备映射
                - publisher_bonus: 出版社准确率奖励

        Raises:
            OSError: 目录创建失败
            sqlite3.Error: 数据库连接失败
        """
        self.config = config

        # GPU 资源声明
        self._setup_gpu_resources()

        # 初始化书籍库（SQLite）
        self.book_library_dir = Path(config.get(
            "book_library_dir", "/mnt/agents/output/tcm_ocr_library"
        ))
        self.book_library_dir.mkdir(parents=True, exist_ok=True)

        # 初始化 PostgreSQL 连接
        self.db_pg = self._init_postgresql(config.get("pg_dsn", ""))

        # 初始化 OCR 引擎
        self.engines = self._init_engines(config.get("engine_configs", {}))

        # 初始化术语知识库
        self.term_kb = self._init_term_kb(config.get("term_kb_path", ""))

        # 初始化单页流水线
        self.page_pipeline = PagePipeline(
            config=config,
            engines=self.engines,
            term_kb=self.term_kb,
        )

        # 当前处理的书籍信息
        self.current_book_id: Optional[str] = None
        self.current_db_book: Optional[sqlite3.Connection] = None
        self.current_book_meta: Dict[str, Any] = {}
        self.page_results: List[Dict[str, Any]] = []

        logger.info(
            "BookPipeline 初始化完成，引擎: %s，GPU: %s",
            list(self.engines.keys()),
            config.get("gpu_device_map", "auto"),
        )

    def _setup_gpu_resources(self) -> None:
        """声明 GPU 资源。"""
        gpu_config = self.config.get("gpu_device_map", "auto")
        if isinstance(gpu_config, dict):
            for device, memory in gpu_config.items():
                logger.info("GPU 资源声明: device=%s, max_memory=%s", device, memory)
        elif gpu_config == "auto":
            logger.info("GPU 资源声明: auto")

        # 设置环境变量
        if "cuda_visible_devices" in self.config:
            os.environ["CUDA_VISIBLE_DEVICES"] = str(
                self.config["cuda_visible_devices"]
            )

    def _init_postgresql(self, pg_dsn: str) -> object:
        """初始化 PostgreSQL 连接。

        Args:
            pg_dsn: PostgreSQL DSN 字符串

        Returns:
            PostgreSQL 连接对象，或 None（如果未配置）
        """
        if not pg_dsn:
            logger.warning("PostgreSQL DSN 未配置")
            return None
        try:
            import psycopg2

            conn = psycopg2.connect(pg_dsn)
            logger.info("PostgreSQL 连接成功")
            return conn
        except ImportError:
            logger.warning("psycopg2 未安装，PostgreSQL 功能不可用")
            return None
        except Exception as e:
            logger.error("PostgreSQL 连接失败: %s", e)
            return None

    def _init_engines(self, engine_configs: Dict[str, Any]) -> Dict[str, Any]:
        """初始化 OCR 引擎。

        Args:
            engine_configs: 引擎配置字典

        Returns:
            引擎实例字典
        """
        engines: Dict[str, Any] = {}

        # ShizhenGPT（本地中医微调模型）
        if engine_configs.get("shizhengpt", {}).get("enabled", True):
            try:
                from kzocr.tcm_ocr.llm.local.shizhen_gpt import ShizhenGPTClient

                device_str = engine_configs.get("shizhengpt", {}).get("device", "cuda:0")
                gpu_id = 0
                if device_str and ":" in device_str:
                    try:
                        gpu_id = int(device_str.split(":", 1)[1])
                    except ValueError:
                        gpu_id = 0
                engines["shizhengpt"] = ShizhenGPTClient(
                    model_path=engine_configs.get("shizhengpt", {}).get("model_path", ""),
                    gpu_id=gpu_id,
                    quantization=engine_configs.get("shizhengpt", {}).get("quantization", "4bit"),
                )
                logger.info("ShizhenGPT 引擎初始化成功")
            except ImportError:
                logger.warning("ShizhenGPT 引擎不可用")
            except Exception as e:
                logger.warning("ShizhenGPT 引擎初始化失败: %s", e)

        # PaddleOCR
        if engine_configs.get("paddleocr", {}).get("enabled", True):
            try:
                from kzocr.tcm_ocr.core.engines.paddleocr_adapter import PaddleOCRAdapter

                use_gpu = engine_configs["paddleocr"].get("use_gpu", True)
                engines["paddleocr"] = PaddleOCRAdapter(
                    device="cuda:0" if use_gpu else "cpu",
                )
                logger.info("PaddleOCR 引擎初始化成功")
            except ImportError:
                logger.warning("PaddleOCR 引擎不可用")
            except Exception as e:
                logger.warning("PaddleOCR 引擎初始化失败: %s", e)

        # RapidOCR
        if engine_configs.get("rapidocr", {}).get("enabled", True):
            try:
                from kzocr.tcm_ocr.core.engines.rapidocr_adapter import RapidOCRAdapter

                engines["rapidocr"] = RapidOCRAdapter()
                logger.info("RapidOCR 引擎初始化成功")
            except ImportError:
                logger.warning("RapidOCR 引擎不可用")
            except Exception as e:
                logger.warning("RapidOCR 引擎初始化失败: %s", e)

        # UniRec
        if engine_configs.get("unirec", {}).get("enabled", True):
            try:
                from kzocr.tcm_ocr.core.engines.unirec_adapter import UniRecAdapter

                engines["unirec"] = UniRecAdapter()
                logger.info("UniRec 引擎初始化成功")
            except ImportError:
                logger.warning("UniRec 引擎不可用")
            except Exception as e:
                logger.warning("UniRec 引擎初始化失败: %s", e)

        # PaddleOCR-VL-1.6 (VLM via llama-server)
        if engine_configs.get("paddleocr_vl16", {}).get("enabled", False):
            try:
                from kzocr.tcm_ocr.core.engines.paddleocr_vl16_adapter import PaddleOCRVl16Adapter

                engines["paddleocr_vl16"] = PaddleOCRVl16Adapter(
                    auto_start=engine_configs.get("paddleocr_vl16", {}).get("auto_start", True),
                )
                logger.info("PaddleOCR-VL-1.6 引擎初始化成功")
            except ImportError:
                logger.warning("PaddleOCR-VL-1.6 引擎不可用")
            except Exception as e:
                logger.warning("PaddleOCR-VL-1.6 引擎初始化失败: %s", e)

        # ShizhenGPT-7B-VL (GGUF VLM via llama-server)
        if engine_configs.get("shizhengpt", {}).get("enabled", False):
            try:
                from kzocr.tcm_ocr.core.engines.shizhengpt_adapter import ShizhenGPTAdapter

                engines["shizhengpt_vl"] = ShizhenGPTAdapter(
                    auto_start=engine_configs.get("shizhengpt", {}).get("auto_start", True),
                )
                logger.info("ShizhenGPT-7B-VL 引擎初始化成功")
            except ImportError:
                logger.warning("ShizhenGPT-7B-VL 引擎不可用")
            except Exception as e:
                logger.warning("ShizhenGPT-7B-VL 引擎初始化失败: %s", e)

        # MinerU
        if engine_configs.get("mineru", {}).get("enabled", True):
            try:
                from kzocr.tcm_ocr.core.engines.mineru_adapter import MinerUAdapter

                use_gpu = engine_configs.get("paddleocr", {}).get("use_gpu", True)
                engines["mineru"] = MinerUAdapter(
                    device="cuda:0" if use_gpu else "cpu",
                )
                logger.info("MinerU 引擎初始化成功")
            except ImportError:
                logger.warning("MinerU 引擎不可用")
            except Exception as e:
                logger.warning("MinerU 引擎初始化失败: %s", e)

        # 云端 LLM（用于争议行校对与文档树重建）
        if engine_configs.get("cloud_llm", {}).get("enabled", True):
            try:
                from kzocr.tcm_ocr.llm.cloud.cloud_llm import CloudLLMClient

                engines["cloud_llm"] = CloudLLMClient()
                logger.info("云端 LLM 引擎初始化成功（CloudLLMClient）")
            except ImportError:
                logger.warning("云端 LLM 引擎不可用")
            except Exception as e:
                logger.warning("云端 LLM 引擎初始化失败: %s", e)

        if not engines:
            logger.error("没有可用的 OCR 引擎！")

        return engines

    def _init_term_kb(self, kb_path: str) -> object:
        """初始化术语知识库。

        新版 TermKB 依赖 PostgreSQL RuntimeDB；本地运行无 PG 时返回空知识库。

        Args:
            kb_path: 知识库文件路径（已弃用）

        Returns:
            术语知识库实例
        """
        try:
            from kzocr.tcm_ocr.knowledge.term.term_kb import TermKB

            pg_dsn = self.config.get("pg_dsn", "")
            if pg_dsn:
                from kzocr.tcm_ocr.database.postgres.runtime_db import RuntimeDB
                runtime_db = RuntimeDB(dsn=pg_dsn)
                kb = TermKB(runtime_db)
            else:
                logger.warning("PostgreSQL 未配置，术语知识库不可用")
                return _EmptyTermKB()
            logger.info("术语知识库初始化成功")
            return kb
        except ImportError:
            logger.warning("术语知识库模块不可用，使用空知识库")
            return _EmptyTermKB()
        except Exception as e:
            logger.warning("术语知识库初始化失败: %s", e)
            return _EmptyTermKB()

    def _create_book_database(self, book_id: str) -> sqlite3.Connection:
        """创建书籍 SQLite 数据库。

        Args:
            book_id: 书籍 ID

        Returns:
            SQLite 连接对象
        """
        db_path = self.book_library_dir / f"{book_id}.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # 创建表结构
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS book_metadata (
                book_id TEXT PRIMARY KEY,
                title TEXT,
                author TEXT,
                publisher TEXT,
                pub_year INTEGER,
                pub_month INTEGER,
                isbn TEXT,
                edition TEXT,
                price TEXT,
                category TEXT,
                language TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS page (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT,
                page_number INTEGER,
                layout_type TEXT,
                created_at TEXT,
                UNIQUE(book_id, page_number)
            );

            CREATE TABLE IF NOT EXISTS content_node (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT,
                node_id TEXT UNIQUE,
                parent_id TEXT,
                node_type TEXT,
                node_level INTEGER,
                title TEXT,
                content TEXT,
                page_start INTEGER,
                page_end INTEGER,
                node_order INTEGER,
                metadata TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS proofread_record (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT,
                page_number INTEGER,
                paragraph_id TEXT,
                line_id TEXT UNIQUE,
                line_number INTEGER,
                original_text TEXT,
                corrected_text TEXT,
                fused_text TEXT,
                confidence REAL,
                engine_results TEXT,
                llm_decision TEXT,
                llm_decision_level INTEGER,
                disputed INTEGER DEFAULT 0,
                dispute_reason TEXT,
                disputed_image_path TEXT,
                human_verified INTEGER DEFAULT 0,
                human_final_text TEXT,
                cer_before REAL,
                cer_after REAL,
                correction_type TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS line_engine_result (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT,
                page_number INTEGER,
                line_id TEXT,
                engine_name TEXT,
                raw_text TEXT,
                confidence REAL,
                char_confidences TEXT,
                bbox TEXT,
                processing_time_ms INTEGER,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS formula_composition (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT,
                formula_id TEXT,
                formula_name TEXT,
                formula_sequence INTEGER,
                page_numbers TEXT,
                paragraph_ids TEXT,
                source_text TEXT,
                extracted_by TEXT,
                verification_status TEXT DEFAULT 'pending',
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS formula_ingredient (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT,
                formula_id TEXT,
                herb_name TEXT,
                dosage REAL,
                dosage_unit TEXT,
                processing_note TEXT,
                ingredient_order INTEGER,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS image_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id TEXT,
                page_number INTEGER,
                bbox TEXT,
                caption TEXT,
                block_id TEXT,
                created_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_proofread_book_page
                ON proofread_record(book_id, page_number);
            CREATE INDEX IF NOT EXISTS idx_proofread_line_id
                ON proofread_record(line_id);
            CREATE INDEX IF NOT EXISTS idx_formula_book
                ON formula_composition(book_id);
            CREATE INDEX IF NOT EXISTS idx_content_node_book
                ON content_node(book_id);
        """)

        conn.commit()
        logger.info("[%s] 书籍数据库创建完成: %s", book_id, db_path)
        return conn

    # =====================================================================
    # 主处理流程
    # =====================================================================

    def process_book(self, pdf_path: str, book_id: str) -> Dict[str, Any]:
        """处理整本书籍，返回处理结果摘要。

        完整处理流程：
        1. PDF 渲染（按需）
        2. 元数据提取（封面/扉页/版权页 OCR + 正则/LLM 实体提取）
        3. 目录处理（构建 ContentNode 树）
        4. 按章循环处理页面
        5. 人工核验
        6. 第二次剂量校验
        7. 阶段 CER 回填
        8. 自动发现
        9. 数据归档（SQLite → PostgreSQL）
        10. 生成最终交付物（Markdown + JSON）
        11. 清理书籍库

        Args:
            pdf_path: PDF 文件路径
            book_id: 书籍唯一标识

        Returns:
            处理结果摘要字典
        """
        start_time = time.time()
        self.current_book_id = book_id
        self.page_results = []

        logger.info("=" * 60)
        logger.info("[%s] 开始处理书籍: %s", book_id, pdf_path)
        logger.info("=" * 60)

        result: Dict[str, Any] = {
            "book_id": book_id,
            "pdf_path": pdf_path,
            "status": "processing",
            "pages_processed": 0,
            "lines_processed": 0,
            "formulas_extracted": 0,
            "elapsed_seconds": 0,
            "outputs": {},
        }

        try:
            # 1. 创建/连接书籍数据库
            self.current_db_book = self._create_book_database(book_id)

            # 2. PDF 渲染
            page_images = self._render_pdf(pdf_path, book_id)
            total_pages = len(page_images)
            result["total_pages"] = total_pages
            logger.info("[%s] PDF 渲染完成: %d 页", book_id, total_pages)

            # 3. 元数据提取（从封面/扉页/版权页）
            self.current_book_meta = self._extract_metadata(
                page_images, book_id
            )
            self._save_book_metadata(book_id, self.current_book_meta)
            logger.info("[%s] 元数据提取完成: %s", book_id, self.current_book_meta.get("title", ""))

            # 4. 目录处理
            toc_tree = self._process_toc(page_images, book_id)
            logger.info("[%s] 目录处理完成: %d 节点", book_id, len(toc_tree))

            # 5. 按章循环处理页面
            all_formulas: List[Dict[str, Any]] = []
            total_lines = 0
            total_disputed = 0

            for page_idx, page_img in enumerate(page_images):
                page_num = page_idx + 1

                page_result = self.page_pipeline.process_page(
                    page_img=page_img,
                    page_num=page_num,
                    book_meta=self.current_book_meta,
                )
                self.page_results.append(page_result)

                total_lines += page_result["statistics"]["total_lines"]
                total_disputed += page_result["statistics"]["disputed_lines"]
                all_formulas.extend(page_result.get("formulas", []))

                if page_num % 10 == 0:
                    logger.info(
                        "[%s] 进度: %d/%d 页, %d 行, %d 争议",
                        book_id, page_num, total_pages,
                        total_lines, total_disputed,
                    )

            result["pages_processed"] = total_pages
            result["lines_processed"] = total_lines
            result["disputed_lines"] = total_disputed
            result["formulas_extracted"] = len(all_formulas)
            logger.info(
                "[%s] 页面处理完成: %d 页, %d 行, %d 争议行, %d 方剂",
                book_id, total_pages, total_lines, total_disputed, len(all_formulas),
            )

            # 6. 构建书籍结构并入库
            if self.current_db_book:
                self.page_pipeline.build_book_structure(
                    book_id=book_id,
                    page_data_list=self.page_results,
                    db_book=self.current_db_book,
                )

            # 7. 人工核验
            if self.config.get("enable_human_review", True):
                self._human_verification(book_id)
                logger.info("[%s] 人工核验完成", book_id)

            # 8. 第二次剂量校验
            self._second_dose_validation(book_id)
            logger.info("[%s] 第二次剂量校验完成", book_id)

            # 9. 阶段 CER 回填
            self._backfill_cer(book_id)
            logger.info("[%s] 阶段 CER 回填完成", book_id)

            # 10. 自动发现
            if self.config.get("enable_auto_discovery", True) and self.db_pg:
                _run_auto_discovery(book_id, self.current_db_book, self.db_pg)
                logger.info("[%s] 自动发现完成", book_id)

            # 11. 生成最终交付物
            output_dir = Path(self.config.get("output_dir", "/mnt/agents/output/tcm_ocr_results")) / book_id
            self._ensure_output_dir(output_dir)

            deliverables_result = export_final_outputs(
                book_id=book_id,
                output_dir=str(output_dir),
                db_book=self.current_db_book,
                runtime_db=self.db_pg,
            )
            result["outputs"] = deliverables_result
            logger.info("[%s] 交付物生成完成: %s", book_id, output_dir)

            # 12. 校验交付物
            is_valid = verify_deliverables(book_id, str(output_dir))
            result["deliverables_valid"] = is_valid

            # 13. 数据归档
            if self.config.get("enable_postgresql_archive", True) and self.db_pg:
                archive_to_postgresql(book_id, self.current_db_book, self.db_pg)
                logger.info("[%s] 数据归档完成", book_id)

            # 14. 清理
            cleanup_book_directory(
                book_id=book_id,
                book_library_dir=str(self.book_library_dir),
            )

            result["status"] = "completed"

        except Exception as e:
            logger.error("[%s] 书籍处理失败: %s", book_id, e, exc_info=True)
            result["status"] = "failed"
            result["error"] = str(e)

        elapsed = time.time() - start_time
        result["elapsed_seconds"] = round(elapsed, 1)

        logger.info(
            "[%s] 书籍处理结束: status=%s, 耗时=%.1f秒",
            book_id, result["status"], elapsed,
        )
        return result

    def finalize_book(self, book_id: str) -> None:
        """完成书籍处理，执行收尾工作。

        调用：
        - _run_auto_discovery
        - export_final_outputs
        - archive_to_postgresql
        - cleanup_book_directory

        Args:
            book_id: 书籍 ID
        """
        logger.info("[%s] 开始 finalize_book", book_id)

        # 连接数据库
        db_path = self.book_library_dir / f"{book_id}.db"
        if not db_path.exists():
            logger.error("[%s] 书籍数据库不存在", book_id)
            return

        db_book = sqlite3.connect(str(db_path))
        db_book.row_factory = sqlite3.Row

        try:
            # 1. 自动发现
            if self.config.get("enable_auto_discovery", True) and self.db_pg:
                _run_auto_discovery(book_id, db_book, self.db_pg)

            # 2. 导出交付物
            output_dir = Path(self.config.get("output_dir", "/mnt/agents/output/tcm_ocr_results")) / book_id
            self._ensure_output_dir(output_dir)

            export_final_outputs(
                book_id=book_id,
                output_dir=str(output_dir),
                db_book=db_book,
                runtime_db=self.db_pg,
            )

            # 3. 归档到 PostgreSQL
            if self.config.get("enable_postgresql_archive", True) and self.db_pg:
                archive_to_postgresql(book_id, db_book, self.db_pg)

            # 4. 清理
            cleanup_book_directory(
                book_id=book_id,
                book_library_dir=str(self.book_library_dir),
            )

            logger.info("[%s] finalize_book 完成", book_id)

        except Exception as e:
            logger.error("[%s] finalize_book 失败: %s", book_id, e, exc_info=True)
        finally:
            db_book.close()

    # =====================================================================
    # 子步骤实现
    # =====================================================================

    def _render_pdf(self, pdf_path: str, book_id: str) -> List[np.ndarray]:
        """将 PDF 渲染为图像列表。

        Args:
            pdf_path: PDF 文件路径
            book_id: 书籍 ID

        Returns:
            页面图像列表
        """
        images: List[np.ndarray] = []

        try:
            import fitz  # PyMuPDF

            doc = fitz.open(pdf_path)
            logger.info("[%s] PDF 共 %d 页", book_id, len(doc))

            for page_idx in range(len(doc)):
                page = doc[page_idx]
                mat = fitz.Matrix(DPI / 72, DPI / 72)
                pix = page.get_pixmap(matrix=mat)

                # 转换为 numpy 数组
                img = np.frombuffer(pix.samples, dtype=np.uint8)
                img = img.reshape(pix.height, pix.width, pix.n)

                if pix.n == 4:
                    # RGBA → RGB
                    import cv2

                    img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
                elif pix.n == 3:
                    img = img[:, :, ::-1]  # RGB → BGR

                images.append(img)

            doc.close()

        except ImportError:
            logger.error("PyMuPDF 未安装，无法渲染 PDF")
            raise
        except Exception as e:
            logger.error("PDF 渲染失败: %s", e)
            raise

        return images

    def _extract_metadata(
        self,
        page_images: List[np.ndarray],
        book_id: str,
    ) -> Dict[str, Any]:
        """从封面/扉页/版权页提取书籍元数据。

        Args:
            page_images: 页面图像列表
            book_id: 书籍 ID

        Returns:
            元数据字典
        """
        meta: Dict[str, Any] = {
            "book_id": book_id,
            "title": "",
            "author": "",
            "publisher": "",
            "pub_year": 0,
            "pub_month": 0,
            "isbn": "",
            "edition": "",
            "price": "",
            "category": "中医",
            "language": "zh-CN",
        }

        # 优先处理前几页（封面、版权页通常在前面）
        front_pages = page_images[:5] if len(page_images) > 5 else page_images

        # 使用 OCR 提取文本
        all_text = ""
        for page_img in front_pages:
            try:
                if "paddleocr" in self.engines:
                    text, _ = self.engines["paddleocr"].recognize(page_img)
                    all_text += text + "\n"
            except Exception:
                continue

        # 正则提取元数据
        import re

        # ISBN
        isbn_match = re.search(r"ISBN[\s:]?(?:978-?)?\d[\d\-]{8,17}\d", all_text)
        if isbn_match:
            meta["isbn"] = isbn_match.group(0)

        # 出版年份
        year_match = re.search(r"(\d{4})\s*年\s*(\d{1,2})?\s*月", all_text)
        if year_match:
            meta["pub_year"] = int(year_match.group(1))
            if year_match.group(2):
                meta["pub_month"] = int(year_match.group(2))

        # 出版社
        pub_match = re.search(r"(?:出版发行|出版|出版社|出版单位)[\s:]*([^\n]+)", all_text)
        if pub_match:
            meta["publisher"] = pub_match.group(1).strip()

        # 定价
        price_match = re.search(r"(?:定价|售价|价格)[\s:]*(\d+\.?\d*)\s*元?", all_text)
        if price_match:
            meta["price"] = price_match.group(1)

        # 版次
        edition_match = re.search(r"第(\d+)版", all_text)
        if edition_match:
            meta["edition"] = edition_match.group(1)

        # 尝试用 LLM 提取标题和作者
        if "shizhengpt" in self.engines or "cloud_llm" in self.engines:
            try:
                llm_meta = self._extract_metadata_with_llm(all_text)
                meta.update({k: v for k, v in llm_meta.items() if v})
            except Exception as e:
                logger.warning("LLM 元数据提取失败: %s", e)

        return meta

    def _extract_metadata_with_llm(self, text: str) -> Dict[str, str]:
        """使用 LLM 提取元数据。

        Args:
            text: 文本内容

        Returns:
            元数据字典
        """
        prompt = f"""从以下书籍版权页文本中提取元数据，输出 JSON 格式：

{text[:2000]}

请提取：title（书名）、author（作者）、publisher（出版社）、pub_year（出版年份）
只输出 JSON，不要解释。"""

        try:
            from kzocr.tcm_ocr.utils.common import parse_llm_json_with_retry

            engine = self.engines.get("shizhengpt") or self.engines.get("cloud_llm")
            if engine:
                output = engine.generate(prompt, max_tokens=512, temperature=0.1)
                parsed = parse_llm_json_with_retry(output, prompt)
                if parsed:
                    return {
                        "title": parsed.get("title", ""),
                        "author": parsed.get("author", ""),
                        "publisher": parsed.get("publisher", ""),
                        "pub_year": str(parsed.get("pub_year", "")),
                    }
        except Exception as e:
            logger.warning("LLM 元数据提取失败: %s", e)

        return {}

    def _save_book_metadata(
        self,
        book_id: str,
        meta: Dict[str, Any],
    ) -> None:
        """保存书籍元数据到数据库。

        Args:
            book_id: 书籍 ID
            meta: 元数据字典
        """
        if not self.current_db_book:
            return

        self.current_db_book.execute(
            """INSERT INTO book_metadata (
                book_id, title, author, publisher, pub_year, pub_month,
                isbn, edition, price, category, language, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(book_id) DO UPDATE SET
                title=COALESCE(EXCLUDED.title, title),
                author=COALESCE(EXCLUDED.author, author),
                publisher=COALESCE(EXCLUDED.publisher, publisher),
                pub_year=COALESCE(EXCLUDED.pub_year, pub_year),
                pub_month=COALESCE(EXCLUDED.pub_month, pub_month),
                isbn=COALESCE(EXCLUDED.isbn, isbn),
                edition=COALESCE(EXCLUDED.edition, edition),
                price=COALESCE(EXCLUDED.price, price),
                category=COALESCE(EXCLUDED.category, category),
                language=COALESCE(EXCLUDED.language, language),
                created_at=COALESCE(EXCLUDED.created_at, created_at)""",
            (
                book_id,
                meta.get("title", ""),
                meta.get("author", ""),
                meta.get("publisher", ""),
                meta.get("pub_year", 0),
                meta.get("pub_month", 0),
                meta.get("isbn", ""),
                meta.get("edition", ""),
                meta.get("price", ""),
                meta.get("category", "中医"),
                meta.get("language", "zh-CN"),
                datetime.now().isoformat(),
            ),
        )
        self.current_db_book.commit()

    def _process_toc(
        self,
        page_images: List[np.ndarray],
        book_id: str,
    ) -> List[Dict[str, Any]]:
        """处理目录页，构建 ContentNode 树。

        Args:
            page_images: 页面图像列表
            book_id: 书籍 ID

        Returns:
            目录节点列表
        """
        toc_nodes: List[Dict[str, Any]] = []

        # 检测目录页（通常在前 10 页内）
        for page_idx, page_img in enumerate(page_images[:10]):
            try:
                if "paddleocr" in self.engines:
                    text, _ = self.engines["paddleocr"].recognize(page_img)

                    # 检测目录关键词
                    toc_keywords = ["目录", "contents", "目次", "章节目录"]
                    if any(kw in text for kw in toc_keywords):
                        logger.info("[%s] 发现目录页: 第 %d 页", book_id, page_idx + 1)
                        nodes = self._parse_toc_text(text, page_idx + 1, book_id)
                        toc_nodes.extend(nodes)
            except Exception:
                continue

        # 写入数据库
        if toc_nodes and self.current_db_book:
            for i, node in enumerate(toc_nodes):
                self.current_db_book.execute(
                    """INSERT INTO content_node (
                        book_id, node_id, parent_id, node_type, node_level,
                        title, content, page_start, page_end, node_order, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_id) DO UPDATE SET
                        parent_id=COALESCE(EXCLUDED.parent_id, parent_id),
                        node_type=COALESCE(EXCLUDED.node_type, node_type),
                        node_level=COALESCE(EXCLUDED.node_level, node_level),
                        title=COALESCE(EXCLUDED.title, title),
                        content=COALESCE(EXCLUDED.content, content),
                        page_start=COALESCE(EXCLUDED.page_start, page_start),
                        page_end=COALESCE(EXCLUDED.page_end, page_end),
                        node_order=COALESCE(EXCLUDED.node_order, node_order)""",
                    (
                        book_id,
                        node.get("node_id", ""),
                        node.get("parent_id", ""),
                        node.get("node_type", "heading"),
                        node.get("node_level", 1),
                        node.get("title", ""),
                        "",
                        node.get("page_start", 0),
                        node.get("page_end", 0),
                        i,
                        datetime.now().isoformat(),
                    ),
                )
            self.current_db_book.commit()

        return toc_nodes

    def _parse_toc_text(
        self,
        text: str,
        page_num: int,
        book_id: str,
    ) -> List[Dict[str, Any]]:
        """解析目录文本为节点列表。

        Args:
            text: 目录页 OCR 文本
            page_num: 页码
            book_id: 书籍 ID

        Returns:
            目录节点列表
        """
        import re

        nodes: List[Dict[str, Any]] = []
        lines = text.split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 匹配 "第X章 标题 ... 页码" 模式
            match = re.match(
                r"(第[一二三四五六七八九十百\d]+[章节篇])\s*(.+?)\s*\.+\s*(\d+)",
                line,
            )
            if match:
                chapter_num, title, pg = match.groups()
                node_id = f"{book_id}_toc_{chapter_num}"
                nodes.append({
                    "node_id": node_id,
                    "parent_id": "",
                    "node_type": "heading",
                    "node_level": 1,
                    "title": f"{chapter_num} {title.strip()}",
                    "page_start": int(pg) if pg.isdigit() else 0,
                    "page_end": 0,
                })
                continue

            # 匹配 "X.X 标题 ... 页码" 模式
            match = re.match(r"(\d+\.\d+)\s+(.+?)\s*\.+\s*(\d+)", line)
            if match:
                section_num, title, pg = match.groups()
                parent_id = nodes[-1]["node_id"] if nodes else ""
                node_id = f"{book_id}_toc_{section_num}"
                level = section_num.count(".") + 1
                nodes.append({
                    "node_id": node_id,
                    "parent_id": parent_id,
                    "node_type": "heading",
                    "node_level": level,
                    "title": f"{section_num} {title.strip()}",
                    "page_start": int(pg) if pg.isdigit() else 0,
                    "page_end": 0,
                })

        return nodes

    def _human_verification(self, book_id: str) -> None:
        """人工核验流程。

        标记需要人工审核的行，生成审核清单。

        Args:
            book_id: 书籍 ID
        """
        if not self.current_db_book:
            return

        # 查询需要人工审核的行
        cursor = self.current_db_book.execute(
            """SELECT line_id, fused_text, dispute_reason, confidence
               FROM proofread_record
               WHERE book_id = ? AND disputed = 1 AND human_verified = 0""",
            (book_id,),
        )
        rows = cursor.fetchall()

        if not rows:
            logger.info("[%s] 无需人工核验", book_id)
            return

        # 生成人工审核清单文件
        review_dir = self.book_library_dir / f"{book_id}_review"
        review_dir.mkdir(parents=True, exist_ok=True)

        review_file = review_dir / "human_review_list.md"
        with open(review_file, "w", encoding="utf-8") as f:
            f.write(f"# 人工审核清单 - {book_id}\n\n")
            f.write(f"共 {len(rows)} 行需要人工审核\n\n")

            for i, row in enumerate(rows, 1):
                f.write(f"## {i}. {row['line_id']}\n")
                f.write(f"- **文本**: {row['fused_text']}\n")
                f.write(f"- **置信度**: {row['confidence']:.3f}\n")
                f.write(f"- **原因**: {row['dispute_reason']}\n\n")

        logger.info("[%s] 人工审核清单已生成: %s", book_id, review_file)

        # 注：实际人工审核需要外部系统介入
        # 这里标记为待审核状态

    def _second_dose_validation(self, book_id: str) -> None:
        """第二次剂量校验。

        在全书层面再次校验剂量信息，检查跨页一致性。

        Args:
            book_id: 书籍 ID
        """
        if not self.current_db_book:
            return

        # 查询全书方剂中的剂量
        cursor = self.current_db_book.execute(
            """SELECT fi.formula_id, fi.herb_name, fi.dosage, fi.dosage_unit,
                      fc.formula_name, fc.page_numbers
               FROM formula_ingredient fi
               JOIN formula_composition fc ON fi.formula_id = fc.formula_id
               WHERE fi.book_id = ? AND fi.dosage IS NOT NULL""",
            (book_id,),
        )
        rows = cursor.fetchall()

        dose_issues: List[Dict[str, Any]] = []

        for row in rows:
            dosage = row["dosage"]
            unit = row["dosage_unit"]

            # 合理性检查
            if unit in ("钱", "分") and (dosage <= 0 or dosage > 100):
                dose_issues.append({
                    "formula": row["formula_name"],
                    "herb": row["herb_name"],
                    "dosage": dosage,
                    "unit": unit,
                    "issue": "剂量超出合理范围",
                })
            elif unit in ("克", "两") and (dosage <= 0 or dosage > 500):
                dose_issues.append({
                    "formula": row["formula_name"],
                    "herb": row["herb_name"],
                    "dosage": dosage,
                    "unit": unit,
                    "issue": "剂量超出合理范围",
                })

        if dose_issues:
            logger.warning(
                "[%s] 第二次剂量校验发现 %d 个问题",
                book_id, len(dose_issues),
            )
            for issue in dose_issues:
                logger.warning(
                    "  - %s / %s: %.1f %s - %s",
                    issue["formula"],
                    issue["herb"],
                    issue["dosage"],
                    issue["unit"],
                    issue["issue"],
                )

    def _backfill_cer(self, book_id: str) -> None:
        """阶段 CER（Character Error Rate）回填。

        计算每行 OCR 的 CER 并回填到数据库。

        Args:
            book_id: 书籍 ID
        """
        if not self.current_db_book:
            return

        # 查询所有校对记录
        cursor = self.current_db_book.execute(
            """SELECT id, original_text, corrected_text, confidence
               FROM proofread_record
               WHERE book_id = ?""",
            (book_id,),
        )
        rows = cursor.fetchall()

        for row in rows:
            original = row["original_text"] or ""
            corrected = row["corrected_text"] or ""

            if not original:
                continue

            # 计算 CER
            cer = self._compute_cer(original, corrected)

            # 回填
            self.current_db_book.execute(
                """UPDATE proofread_record
                   SET cer_before = ?, cer_after = ?
                   WHERE id = ?""",
                (cer, cer, row["id"]),
            )

        self.current_db_book.commit()
        logger.info("[%s] CER 回填完成: %d 条记录", book_id, len(rows))

    def _compute_cer(self, reference: str, hypothesis: str) -> float:
        """计算字符错误率（CER）。

        使用编辑距离计算。

        Args:
            reference: 参考文本（正确文本）
            hypothesis: 假设文本（OCR 文本）

        Returns:
            CER 值 [0, 1]
        """
        if not reference:
            return 0.0

        import difflib

        # 使用 SequenceMatcher 计算编辑距离
        sm = difflib.SequenceMatcher(None, reference, hypothesis)
        sum(
            max(tag.count("replace") + tag.count("delete") + tag.count("insert"), 0)
            for tag, _, _, _, _ in sm.get_opcodes()
        )

        # 实际编辑距离
        def levenshtein(s1: str, s2: str) -> int:
            if len(s1) < len(s2):
                return levenshtein(s2, s1)
            if not s2:
                return len(s1)

            previous_row = range(len(s2) + 1)
            for i, c1 in enumerate(s1):
                current_row = [i + 1]
                for j, c2 in enumerate(s2):
                    insertions = previous_row[j + 1] + 1
                    deletions = current_row[j] + 1
                    substitutions = previous_row[j] + (c1 != c2)
                    current_row.append(min(insertions, deletions, substitutions))
                previous_row = current_row

            return previous_row[-1]

        distance = levenshtein(reference, hypothesis)
        return min(distance / len(reference), 1.0)

    def _ensure_output_dir(self, output_dir: Path) -> None:
        """确保输出目录存在。

        Args:
            output_dir: 输出目录路径

        Raises:
            OSError: 创建失败
        """
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            raise OSError(f"权限不足，无法创建目录 {output_dir}: {e}") from e
        except OSError as e:
            raise OSError(f"创建目录失败 {output_dir}: {e}") from e


class _EmptyTermKB:
    """空术语知识库（降级用）。"""

    def lookup(self, term: str) -> Optional[Dict[str, Any]]:
        """查找术语（空实现）。"""
        return None
