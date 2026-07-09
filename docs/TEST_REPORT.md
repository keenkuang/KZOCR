# KZOCR 测试报告

> 生成日期：2026-07-09
> 版本：main@d94ec0e + round4 残留修复

## 测试结果

**41/41 全部通过** ✅

| 测试文件 | 用例数 | 状态 |
|----------|--------|------|
| `tests/test_cloudllm_env.py` | 5 | ✅ CloudLLM 环境变量映射（full mapping, no-overwrite, no-op, partial, mixed） |
| `tests/test_common.py` | 7 | ✅ `adapter_to_line_result` 转换函数（basic, char_conf->json, crop_img, mismatch truncation, None, engine_result, engine_name） |
| `tests/test_pipeline.py` | 5 | ✅ 全链路回归（mock→zai→导出, isMock 阻断） |
| `tests/test_types.py` | 13 | ✅ 契约冻结类型（GlyphStatus×6, ProbeResult×2, AdapterMeta×3, AdapterPageResult×2） |
| `tests/test_vlm.py` | 11 | ✅ VLM 直接集成（路由/降级/PDF渲染/空处理/回归） |

## 覆盖率

**全局覆盖率：增**（types.py 100%, _common.py 100%, 新增 2 模块全覆盖）

| 模块 | 覆盖率 | 说明 |
|------|--------|------|
| `kzocr/engine/types.py` | 100% | 数据结构全覆盖（含新增 GlyphStatus/ProbeResult/AdapterMeta/AdapterPageResult） |
| `kzocr/engines/_common.py` | 100% | 新增转换函数全覆盖（7 测试） |
| `kzocr/config.py` | 100% | |
| `kzocr/engine/mock.py` | 100% | |
| `kzocr/adapter/to_zai_prisma.py` | 90% | |
| `kzocr/engine/run.py` | 49% | 真实引擎路径未执行（需真实 PDF + kimi 环境） |
| `kzocr/export_zai.py` | 97% | |
| `kzocr/cli.py` | 0% | CLI 入口（需完整环境） |
| `kzocr/khub/client.py` | 0% | kHUB 客户端（需外部服务） |
| `kzocr/modelscope_pool.py` | 0% | ModelScope 池（需 API key） |

## round4 评审闭合度

| 问题 | 状态 | 落地 |
|------|------|------|
| K1 `glyph_status` 字段 | ✅ 已冻结 | `types.py: GlyphStatus + LineResult.glyph_status` |
| K3 `ProbeResult` 字段表 | ✅ 已冻结 | `types.py: ProbeResult` dataclass |
| K5 `run_engine` 薄门面 | ✅ 已承诺 | `v0.3-FREEZE.md: K5 补充裁定` + 15 测试迁移表 |
| N1 AdapterPageResult→LineResult | ✅ 已落地 | `engines/_common.py: adapter_to_line_result()` |
| N2 ProbeResult 字段 | ✅ 已冻结 | `types.py: ProbeResult` |
| N3 crop_img 落点 | ✅ 已落地 | `LineResult.crop_img_path` (存引用不存像素 per B7) |
| B2 转换责任 | ✅ 已落地 | `_common.py` 唯一入口 |

## 已知测试缺口

1. **真实引擎 _run_real**（`run.py:96-128`）— 需真实 PDF + BookPipeline 环境，当前仅 mock 回归
2. **CLI 入口**（`cli.py`）— 130 语句全未覆盖
3. **kHUB 客户端**（`khub/client.py`）— 需外部服务
4. **ModelScope 池**（`modelscope_pool.py`）— 需 API key
