# KZOCR 测试报告

> 生成日期：2026-07-09
> 版本：main@527ac10

## 测试结果

**16/16 全部通过** ✅

| 测试文件 | 用例数 | 状态 |
|----------|--------|------|
| `tests/test_pipeline.py` | 5 | ✅ |
| `tests/test_pipeline.py::test_mock_engine_produces_expected_book` | — | 确认 is_mock 桩数据结构 |
| `tests/test_pipeline.py::test_push_to_zai_writes_all_tables` | — | 真实数据正常写入校对台 8 表 |
| `tests/test_pipeline.py::test_export_markdown_roundtrip` | — | Markdown 导出台白+足三里+三大永久范式库 |
| `tests/test_pipeline.py::test_push_to_zai_blocks_mock` | — | is_mock=True 阻断 publish |
| `tests/test_pipeline.py::test_adapter_export_markdown_from_object` | — | 从 BookResult 对象导出 Markdown |
| `tests/test_vlm.py` | 11 | ✅ |
| `tests/test_vlm.py::test_routes_to_vlm_when_use_vlm_is_true` | — | VLM 模式路由正确 |
| `tests/test_vlm.py::test_routes_to_real_when_use_vlm_is_false` | — | 真实模式路由正确 |
| `tests/test_vlm.py::test_mock_takes_precedence_over_vlm` | — | mock 优先于 VLM |
| `tests/test_vlm.py::test_vlm_failure_falls_back_to_mock` | — | VLM 失败降级 mock |
| `tests/test_vlm.py::test_vlm_failure_with_require_real_raises` | — | require_real 抛异常 |
| `tests/test_vlm.py::test_vlm_renders_pdf_pages_to_markdown` | — | PDF 渲染+VLM 识别 → Markdown |
| `tests/test_vlm.py::test_vlm_multi_line_page` | — | 多行页面识别 |
| `tests/test_vlm.py::test_vlm_handles_empty_pdf` | — | 空 PDF 处理 |
| `tests/test_vlm.py::test_vlm_markdown_to_pages_empty` | — | 空 Markdown → 空 pages |
| `tests/test_vlm.py::test_vlm_markdown_to_pages_normal` | — | 正常 Markdown → 结构化 pages |
| `tests/test_vlm.py::test_run_real_regression_unaffected` | — | 真实模式未受影响 |

## 覆盖率

**全局覆盖率：49%**（970 语句，493 未覆盖）

| 模块 | 覆盖率 | 说明 |
|------|--------|------|
| `kzocr/engine/types.py` | 100% | 数据结构全覆盖 |
| `kzocr/config.py` | 100% | 配置模块全覆盖 |
| `kzocr/engine/mock.py` | 100% | Mock 引擎全覆盖 |
| `kzocr/adapter/to_zai_prisma.py` | 90% | 写入适配器 |
| `kzocr/engine/run.py` | 49% | 引擎驱动（真实路径未执行） |
| `kzocr/export_zai.py` | 97% | 导出模块 |
| `kzocr/cli.py` | 0% | CLI 入口（需完整环境） |
| `kzocr/khub/client.py` | 0% | kHUB 客户端（需外部服务） |
| `kzocr/modelscope_pool.py` | 0% | ModelScope 池（需 API key） |

## 已知测试缺口

1. **CloudLLM 环境变量映射**（`run.py:98-103`）— KZOCR_LLM_* → GLM_* 自动映射无专用测试
2. **真实引擎 _run_real**（`run.py:96-128`）— 需真实 PDF + BookPipeline 环境，当前仅 mock 回归
3. **CLI 入口**（`cli.py`）— 130 语句全未覆盖
4. **kHUB 客户端**（`khub/client.py`）— 需外部服务

## 历史评审

- round2 (7 角色)：数据安全 / 运维 / 测试 / 客户 / 架构 / 网安 / PM / 软件工程
- round3 (8 角色)：可维护性 / 测试 / 安全 / 校对 UX / 性能 / 数据完整性 / 领域 / 架构
- round4 (多角色)：v0.2 方案评审，指出 glyph_status/ProbeResult/AdapterPageResult 转化等尚未落地的契约缺口
