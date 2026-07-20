# KZOCR 脚本索引

| 脚本 | 用途 |
|------|------|
| `bench_char_boxes.py` | 字符级 bbox 开启 vs 关闭的耗时基准 |
| `bench_engine_compare.py` | PaddleOCR vs RapidOCR 引擎性能基线对比 |
| `bench_paddle_perf.py` | PaddleOCR 多页连续性能基准 |
| `bench_page_concurrency.py` | 页级并发/并行估算（mock 引擎，顺序 vs 并行吞吐对比） |
| `build_confusion_resources.py` | 构建形近字混淆集资源文件 |
| `check_char_bbox.py` | 验证逐字框数据完整性（PaddleOCR predict 迁移验证） |
| `check_render_health.py` | PyMuPDF xref 告警捕获与渲染健康检测 |
| `e2e_cross_engine_realbook.py` | 真实古籍跨引擎分歧对齐端到端验证 |
| `e2e_expand_books.py` | 多古籍扩面分歧实测（增量合并、断点续跑） |
| `e2e_orchestrator.py` | orchestrator 全路径编排 e2e |
| `setup_submodules.sh` | Git 子模块初始化 |
| `smoke_adapters.py` | 适配器冒烟验证 |
| `validate_vl_glm.py` | GLM-4V-Flash 视觉回看真实链路验证 |

> `archive/` — 探索/临时脚本，不再活跃维护。
