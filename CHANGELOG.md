# KZOCR 变更日志

> 文档版本：v2026-07-10T19:52+08
> 最后更新：2026-07-10 19:52 CST

---

## v2026-07-10（补）— v0.6.1 CI 修复与文档一致性

### 说明
修复 GitHub Actions CI 持续失败的根因（两层问题叠加），并同步文档一致性。

| PR | 模块 | 说明 |
|----|------|------|
| #5 | CI | `test.yml` 第 35 行 `run: echo "Tests: ✅"` 在严格 YAML 下非法，改为单引号包裹含冒号命令，使 workflow 可被 GitHub 解析并创建 job |
| #6 | `kzocr/modelscope_pool.py` | `openai` 由顶层硬 `import` 改为可选导入（`try/except ImportError` → `OpenAI = None`），缺依赖时对应 provider 在初始化时自动禁用，消除 CI 最小环境下 `ImportError: No module named 'openai'` |
| #4 | 文档 | 修正 egress 路径过时记录（概览 §4.5 已于 PR #1 更正，SEC-2 标记已修复） |
| #3 | 文档 | 修正 CODEBUDDY.md 过时条目（`ProbeResult.keys` 已同步为 `dict[str, bool]`；round 计数 round1→round9） |
| #7 | 文档 | README 新增 CI 小节，记录上述修复与根因 |

修复后 CI 全绿：`lint` + `test`（Python 3.10 / 3.11 / 3.12）均 `success`，本地 268 测试通过。

---

## v2026-07-10 — v0.6 测试覆盖与项目基础设施

### 日期：2026-07-10

### 新增/修改

| 提交 | 模块 | 说明 |
|------|------|------|
| `5c813d1` | CI | GitHub Actions CI 工作流（3 Python 版本 + ruff lint） |
| `f6e2f7d` | 文档 | README（项目概述/快速开始/架构/配置参考） |
| `2aeed98` | 测试 | CLI 入口测试 23 例 |
| `ce0d544` | 配置 | 新增 `_safe_int` 安全类型解析 + config validation 15 测试 |
| `1b30562` | 测试 | modelscope_pool 测试 23 例 |
| `1a6a349` | 测试 | kHUB 客户端测试 22 例 |
| `0c5d88f` | 文档 | CONTRIBUTING.md + issue/PR templates |
| `a0a2a8a` | 测试 | 真实引擎 mock 测试 9 例（路由 + 内部） |
| `66ae7aa` | 版本 | pyproject.toml 0.2.0 → 0.6.0 |

**总计：268 测试全通过 ✅（~15s）**

---

## v2026-07-10 — v0.5 AMEND 异常处理体系改进实施完成

### 日期：2026-07-10 19:52 CST

实施提交（自 d6e4845 起，HEAD `1f52052`）：

| Commit | 模块 | 说明 |
|--------|------|------|
| `c4120cd` | D0+D1 | Config扩展 (`kzocr_output_dir`, `cache_ttl_seconds`) + errors.py (5异常类 + retry_with_policy) |
| `dd9b76f` | D2 | VLM主循环重试 (_process_vlm_page, 降DPI重试, failed_pages追踪) |
| `cc6f52a` | D4 | 层级异常检测 (HierarchyAnomaly + check_hierarchy_anomaly) |
| `1f52052` | D3 | VLM断点续跑缓存 (config_hash + TTL + KZOCR_CLEAR_CACHE=1) |
| — | 冲突-2 | 移除leakage.py L3日志标记（由D2取代） |

### 评审历程

| 轮次 | 时间 | 角色数 | 结果 |
|------|------|--------|------|
| round6 | 2026-07-10 | 5角色（架构/软件工程/测试/安全/领域） | APPROVED |
| round7 | 2026-07-10 | 5角色再评审 | APPROVED |
| round8 | 2026-07-10 | 6角色（+性能工程师） | APPROVED |
| round9 | 2026-07-10 | 7角色（+运维+产品经理） | APPROVED |
| round10 | 2026-07-10 | 7角色终签 | 全部APPROVED ✅ |

评审报告存档：`docs/reviews/2026-07-10-round{6,7,8,9,10}/`

### 新增测试

| 文件 | 用例数 | 覆盖内容 |
|------|--------|----------|
| `tests/test_config.py` | 6 | D0: 默认值、环境变量覆盖、类型校验 |
| `tests/test_errors.py` | 24 | D1: 异常继承、retry_with_policy、回调、backoff配置 |
| `tests/test_hierarchy.py` | 17 | D4: 邻居窗口、异常检测、严重度缩放 |
| `tests/test_vlm.py` | +16 | D2: 8重试测试 + D3: 8缓存测试 |

**总计：177 测试全通过 ✅（0.94s）**

### 新增/修改文件

| 文件 | 状态 | 行数 |
|------|------|------|
| `kzocr/config.py` | 修改 | +7 |
| `kzocr/engine/run.py` | 修改 | +199 |
| `kzocr/engine/types.py` | 修改 | +1 |
| `kzocr/engines/errors.py` | 新增 | 109 |
| `kzocr/engines/hierarchy.py` | 新增 | 134 |
| `kzocr/engines/leakage.py` | 修改 | -7（L3移除）|
| `kzocr/engines/__init__.py` | 修改 | +22 |
| `tests/test_config.py` | 新增 | 51 |
| `tests/test_errors.py` | 新增 | 217 |
| `tests/test_hierarchy.py` | 新增 | 125 |
| `tests/test_vlm.py` | 修改 | +535 |

---

## v2026-07-07 — 全链路打通

### 日期：2026-07-07 18:00 ~ 23:30 CST

#### KZOCR 仓库（`/home/keen/KZOCR`）

| 时间 | 改动 | 文件 | 说明 |
|------|------|------|------|
| 18:00 | 修复 cli ↔ engine 调用断点 | `cli.py`, `engine/run.py`, `config.py` | `write_book_to_zai` → `push_book_to_zai`；`Config.from_env()` → `load_config()`；pipeline/smoke 默认使用隔离 DB |
| 18:10 | 对齐 engine 到真实 BookPipeline 接口 | `engine/run.py` | `run_book` → `run_engine`，加 `book_code`/`config` 参数；`_run_real` 调用 `BookPipeline(config).process_book(pdf, book_id)`；新增 `_build_engine_config()` 从环境变量构造配置字典 |
| 18:20 | 补齐 engine_configs 结构 | `engine/run.py` | 传入完整的 `engine_configs`（paddleocr / shizhengpt / mineru / tesseract / cloud_llm） |
| 18:30 | 测试 & git 初始化 | `tests/test_pipeline.py`, `.gitignore` | 新增 4 个回归测试；忽略 `*.db` 运行时产物 |
| 18:40 | smoke 冒烟通过 ✅ | — | `python -m kzocr.cli smoke --skip-push` 全流程通过 |

#### kimi 引擎仓库（`/home/keen/kimi_agent_ocr/tcm_ocr_system_v1.1`）

| 时间 | 改动 | 文件 | 说明 |
|------|------|------|------|
| 19:20 | 修复 PaddleOCR 引擎 import | `tcm_ocr/pipeline/book_pipeline.py` | `_init_engines`: `tcm_ocr.ocr.paddle_engine.PaddleOCREngine` → `tcm_ocr.core.engines.paddleocr_adapter.PaddleOCRAdapter`（构造参数 `device`） |
| 19:20 | 修复 MinerU 引擎 import | `tcm_ocr/pipeline/book_pipeline.py` | `tcm_ocr.ocr.mineru_engine.MinerUEngine` → `tcm_ocr.core.engines.mineru_adapter.MinerUAdapter` |
| 19:20 | 修复 云端 LLM 引擎 import | `tcm_ocr/pipeline/book_pipeline.py` | `tcm_ocr.ocr.cloud_llm_engine.CloudLLMEngine` → `tcm_ocr.llm.cloud.cloud_llm.CloudLLMClient`（无参构造） |
| 19:20 | 补 page_pipeline 缺失 import | `tcm_ocr/pipeline/page_pipeline.py` | 添加 `import json`（`json` 未定义 bug） |
| 19:25 | 补 page_pipeline 缺失 datetime import | `tcm_ocr/pipeline/page_pipeline.py` | `import datetime` → `from datetime import datetime`（`module 'datetime' has no attribute 'now'` bug） |
| 19:30 | 修 engine.recognize 返回值解包 | `tcm_ocr/pipeline/page_pipeline.py` | 适配器返回 `str`，原代码要求 `(text, confidence)` tuple，改为兼容两者 |
| 19:30 | 防止本地 LLM 模型在线下载 | `tcm_ocr/pipeline/deliverables.py` | `_call_local_llm` 增加模型目录存在性检查，不存在则立即报错，不触发 HuggingFace 下载 |
| 19:45 | 修复 PaddleOCR 初始化参数 | `tcm_ocr/core/engines/paddleocr_adapter.py` | 去掉 `show_log`、`use_gpu`、`gpu_id`、`enable_mkldnn`、`use_angle_cls`（PaddleOCR v3.7 不支持）；改用 `paddle.set_device()` + `PaddleOCR(lang='ch')` |
| 20:00 | 适配 PaddleOCR v3.7 API | `tcm_ocr/core/engines/paddleocr_adapter.py` | `ocr.ocr(img, det=False, cls=False)` → `ocr.predict(img, use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False, text_det_limit_side_len=0)` |
| 20:00 | 限制裁剪行宽度防止 OneDNN 崩溃 | `tcm_ocr/core/engines/paddleocr_adapter.py` | 添加 `_max_rec_width = 2048`，对超宽行等比缩放 |
| 20:10 | CloudLLMClient 兼容 page_pipeline 调用 | `tcm_ocr/llm/cloud/cloud_llm.py` | 添加 `generate(prompt, max_tokens, temperature)` 方法；`_call_glm` 中 `GLM_MODEL` 环境变量覆写模型名 |
| 20:15 | 交付物内容回退机制 | `tcm_ocr/pipeline/deliverables.py` | `_build_final_doc_from_book_db`: `content_node` 为空时从 `proofread_record` 读行级文本 |
| 20:15 | 修复 image_path 列名 | `tcm_ocr/pipeline/deliverables.py` | `_load_disputed_lines` SQL 中删除不存在的 `image_path` 列 |
| 20:15 | 修复 formula_ingredient Row.get | `tcm_ocr/pipeline/deliverables.py` | `ing_cursor.fetchall()` 的 `r.get()` → 先 `dict(r)` 再 `.get()` |
| 20:20 | 云端 LLM 模型名环境变量 | `tcm_ocr/pipeline/deliverables.py` | `_call_cloud_llm` 中 hardcode `"qwen-max"` → `os.environ.get("TCM_OCR_CLOUD_LLM_MODEL", "qwen-max")` |

#### 运行验证记录

| 时间 | 验证 | 结果 |
|------|------|------|
| 18:50 | `kzocr smoke --skip-push`（mock 全链路） | ✅ 通过 |
| 19:28 | 真实引擎首次调通（样本 PDF） | ✅ 导入正确，PaddleOCR 识别 3 行 |
| 20:20 | 本地 LLM 快速降级验证 | ✅ body.md 写出（空内容，因无 LLM 争议未解决） |
| 20:29 | 云端 LLM（agnes-2.0-flash）HTTP 200 | ✅ 云端 LLM 连接成功 |
| 21:00 | 真实 TCM 书页 `page_0969.webp` 识别 | ✅ body.md 有内容（81 行原始 OCR 文本） |
| 21:30 | 内容回退 proofread_record → body.md | ✅ 49 行收录，39 行入 final_doc content |

### 后续验证（2026-07-07 23:35）

| 项目 | 结果 | 原因 |
|------|------|------|
| MinerU v3（已安装 `mineru 3.2.3`） | ❌ 无法运行 | 适配器 import 旧包名 `magic_pdf`，但 MinerU v3 改用 `mineru` 且无 GPU；layout 模型需要 HuggingFace 下载，当前环境无 GPU + 无 HF token |
| Tesseract | ❌ 已从 `book_pipeline._init_engines` 删除 | 项目 `SPEC.md` 中该引擎不存在，是重构前遗留的死代码 |

### 修正（2026-07-08 04:55）

| 项目 | 变更 |
|------|------|
| **PP-OCRv6 速度** | 发现 4 分钟/页是 PaddleOCR v3.7 `predict()` 错误用法所致。改用 **MinerU 的 PytorchPaddleOCR** 后端，`ocr(img, det=False)` 每行 **0.05 秒**，一页 50 行约 **2.5 秒**（非 4 分钟） |
| **paddleocr_adapter.py** | 重写 `_init_engine`：优先走 MinerU shared model pool（`custom_model_init`），降级走 standalone PaddleOCR；`_init_standalone` 备用；`recognize` 改用 `PytorchPaddleOCR.ocr()` 解析格式 |

### 云端 API 配置（2026-07-08 04:58）

硅基流动 API key 已测试可用。云端 API 配置速查见下文。

### ModelScope（`https://api-inference.modelscope.cn/v1`）

| 模型 ID | 类型 | 每日限额 | 状态 |
|---------|------|---------|------|
| `ZhipuAI/GLM-5.2` | 文本 | 45次 | ✅ | 
| `ZhipuAI/GLM-4.7-Flash` | 文本 | 45次 | ✅ |
| `ZhipuAI/GLM-4.7:DashScope` | 文本 | 45次 | ✅ |
| `Qwen/Qwen3.5-35B-A3B` | 文本 | 45次 | ✅ |
| `Qwen/Qwen3.5-27B` | 文本 | 45次 | ✅ |
| `Qwen/Qwen3.5-122B-A10B` | 文本 | 45次 | ✅ |
| `Qwen/Qwen3.5-397B-A17B` | 文本 | 45次 | ✅ |
| `deepseek-ai/DeepSeek-V4-Pro` | 推理 | 45次 | ✅ |
| `deepseek-ai/DeepSeek-V4-Flash` | 推理 | 45次 | ✅ |
| `moonshotai/Kimi-K2.6:DashScope` | 文本 | 45次 | ✅ |
| Key: `ms-40d78a2b-f786-433a-92e3-8e5f4049f602`

**自动故障转移客户端**: `kzocr/modelscope_pool.py` — `ModelScopePool` 类，10 个模型逐个重试，失败自动切换下一个。 | | |
| 说明 | 注册地址：`modelscope.cn/my/accountsettings`，完成实名后在 `api-inference.modelscope.cn/v1` 使用 |

| 平台 | 端点 | 模型名 | Key 状态 |
|------|------|--------|---------|
| **Ofox AI** | `https://api.ofox.io/v1` | `z-ai/glm-4.7-flash:free` | ✅ `ofox.ai` 国内受限，换 `ofox.io` 后连通（429 限流，key 有效） |
| 说明 | 订阅帖子提到为免费第三方聚合，需解决网络访问后再测 |

### RapidOCR 适配器（2026-07-08 06:50）

| 项目 | 说明 |
|------|------|
| 新文件 | `tcm_ocr/core/engines/rapidocr_adapter.py` |
| 引擎注册 | `book_pipeline._init_engines` + `KZOCR _build_engine_config` |
| 模型 | ONNX PP-OCRv4（自动缓存） |

### UniRec 适配器（2026-07-08 06:55）

| 项目 | 说明 |
|------|------|
| 新文件 | `tcm_ocr/core/engines/unirec_adapter.py` |
| 模型 | `unirec_encoder.onnx` + `unirec_decoder.onnx`（`/home/keen/unirec_0_1b_onnx/`） |
| 状态 | 结构正确，推理耗时 2.8s/行；预处理需调参（当前输出为语言先验幻觉，图片特征未正确传入） |

### ShizhenGPT-7B-VL 适配器（2026-07-08 09:05）

| 项目 | 说明 |
|------|------|
| 新文件 | `tcm_ocr/core/engines/shizhengpt_adapter.py` |
| 模型 | `ShizhenGPT-7B-VL.i1-Q4_K_M.gguf` (4.4GB) + `mmproj` (817MB) |
| 默认 | 禁用（`enabled: False`） |
| 端口 | 18083 |

### MinerU v3 适配器接入

| 项目 | 说明 |
|------|------|
| 文件 | `tcm_ocr/core/engines/mineru_adapter.py`（重写） |
| Layout | 全页 PytorchPaddleOCR 检测，45 blocks，5s/页（CPU） |
| OCR 识别 | 共享 MinerU 模型池，0.076s/行 |
| KZOCR 配置 | 默认启用 (`enabled: True`) |

| 项目 | 说明 |
|------|------|
| 新文件 | `tcm_ocr/core/engines/paddleocr_vl16_adapter.py` |
| 后端 | llama-server（`/home/keen/llama.cpp/build/bin/llama-server`） |
| 模型 | `PaddleOCR-VL-1.6-GGUF.gguf` (893MB) + `mmproj` (841MB) |
| 默认 | 禁用（`enabled: False`），需显式启用 |
| 启用方式 | 环境变量 `KZOCR_PADDLE_VL16_ENABLED=1` + `engine_configs` |

---

## 配置速查

```bash
# === 最小运行（mock）===
kzocr smoke --skip-push

# === 真实 PaddleOCR（CPU，~4 分/页）===
KZOCR_PADDLE_GPU=0 KZOCR_USE_MOCK=0 kzocr pipeline <pdf>

# === 真实 PaddleOCR + 云端 LLM 校对 ===
KZOCR_PADDLE_GPU=0 KZOCR_USE_MOCK=0 \
  KZOCR_LLM_ENABLED=1 \
  GLM_API_KEY=sk-xxx \
  GLM_API_BASE=https://your-api/v1 \
  GLM_MODEL=agnes-2.0-flash \
  kzocr pipeline <pdf>

# === 环境变量参考 ===
KZOCR_PADDLE_GPU        # 1=GPU，0=CPU（默认 0）
KZOCR_ENGINE_LIB_DIR    # 引擎工作目录（默认 /home/keen/kzocr_engine_lib）
KZOCR_ENGINE_OUTPUT_DIR # 交付物输出目录
KZOCR_PG_DSN            # PostgreSQL DSN（空则禁用）
KZOCR_LLM_ENABLED       # 1 启用 LLM 校对
KZOCR_LLM_API_KEY       # 云端 LLM API Key
KZOCR_LLM_BASE_URL      # 云端 LLM Base URL
KZOCR_LLM_MODEL         # 云端 LLM 模型名
GLM_API_KEY / GLM_API_BASE / GLM_MODEL / CLOUD_LLM_PRIMARY  # 引擎内部 LLM 配置
TCM_OCR_CLOUD_LLM_API_KEY / TCM_OCR_CLOUD_LLM_BASE_URL / TCM_OCR_CLOUD_LLM_MODEL  # 交付物 LLM 配置

## 云端 API 配置

### 硅基流动（`https://api.siliconflow.cn/v1`）

| 类型 | 模型名（严格大小写） | 用途 |
|------|---------------------|------|
| 视觉 VLM | `Qwen/Qwen3.5-4B` | 通用图文理解 |
| 文档 OCR | `deepseek-ai/DeepSeek-OCR` | 文字提取 |
| 文档 OCR | `PaddlePaddle/PaddleOCR-VL-1.5` | 飞桨文档 VL |
| 纯文本 | `THUDM/GLM-4-9B-0414` | 文字校对 |
| 纯文本 | `THUDM/GLM-Z1-9B-0414` | 文字校对 |
| 纯文本 | `Qwen/Qwen2.5-7B-Instruct` | 文字校对 |
| 纯文本 | `Qwen/Qwen3-8B` | 文字校对 |
| 纯文本 | `deepseek-ai/DeepSeek-R1-0528-Qwen3-8B` | 推理强化 |
| 纯文本 | `PaddlePaddle/PaddleOCR-VL-1.5` | 文字校对 |

### z.ai（`https://api.z.ai/api/paas/v4`，模型名全小写）

| 类型 | 模型名 | 上下文 | 用途 | Key 状态 |
|------|--------|--------|------|---------|
| 视觉 VLM | `glm-4.6v-flash` | — | 免费图文识别 | ✅ `78184ed8…BrliYI3atOniwJzc` |
| 纯文本 | `glm-4.7-flash` | — | 免费校对 | ✅ 同上 |
| 纯文本 | `glm-4.5-flash` | 128K | 免费校对 | ✅ 同上 |

### 智谱主站（`https://open.bigmodel.cn/api/paas/v4`）

| 类型 | 模型名（大写标准） | 用途 | Key 状态 |
|------|------------------|------|---------|
| 视觉 VLM | `GLM-4.6V-Flash` | 图文识别 | ✅ `77313fa0…D7gxdga59tzqnwfB` 已测通 |
| 纯文本 | `GLM-4.7-Flash` | 文本校对 | ✅ 同上 |
```
