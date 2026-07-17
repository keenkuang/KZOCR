---
schema: context-save/v1
project: KZOCR
project_root: /home/keen/KZOCR
version: v0.6.0 (main)
captured_at: "2026-07-11"
context_type: standard
tags: [session-state, v0.7-layout-crop, pp-doclayoutv3, merged-main, ci-green, in-progress]
---

# KZOCR 会话上下文快照（更新版 · 已合并 main + CI 全绿）

> 覆盖早前 `context_snapshot_2026-07-11.md`。记录截至 2026-07-11 实际完成工作与下一步。
> 项目级决策另存记忆 `project_layout_pivot.md`；此文件为临时会话产物，可按需删除。

## 1. 当前状态总览
- **进行中工作**：v0.7 版心裁剪方案，由 cv2 投影法切换为 **PP-DocLayoutV3** 语义检测（优先）+ cv2 三级降级（行检测→纯投影）。
- **当前分支**：`feat/v0.7-layout-crop`（已推 origin，已并入 `origin/main` 新提交，领先 origin/main 一个 merge commit `20ea167`）。
- **本次会话新增进度**：
  1. 合并 PR #12（清理 CI 债务：ruff 193 处 + 补 `opencv-python-headless` 依赖）到 `main`。
  2. 将 `origin/main` 新提交 merge 进 `feat/v0.7-layout-crop`（merge commit `20ea167`），推送，CI 全绿（run `29158420366`：docker/lint/test(3.10/3.11/3.12) 全部 `success`）。

## 2. 关键决策与结论
- **放弃 cv2 投影法**：用户目视判定其"完全不靠谱"——靠"宽行不贴边"隐式推断版心，0031 等页因某行横跨侧眉导致左裁失效（left=0）。
- **采用 PP-DocLayoutV3**（paddlex 3.7.1，本机已装，权重缓存 `~/.paddlex/official_models/PP-DocLayoutV3`）：
  - 具显式语义类别：`text`/`vertical_text`/`doc_title`/`paragraph_title`（版心组成）；`aside_text`/`header`/`header_image`/`footer`/`footer_image`/`number`（需排除）。
  - 版心 = 正文类检测框并集，外扩 **padding 左右上 15px / 下 10px**（用户指定）。
  - 样本实测：每页正确排除侧眉/页眉/页脚/页码，body 框稳定（left≈133–155, right≈1262–1289，两侧各裁~150px）。
  - 推理开销：CPU 约 **2.0–2.6s/页**。

## 3. 代码位置与接口
- `kzocr/engine/layout_crop.py`：
  - `crop_by_doclayout(img, pad_lr_t=15, pad_b=10)` → 新版心后端；paddlex 不可用时返回 None。
  - `crop_by_layout(img, padding=10, page_num=0)` → 主入口，优先 doclayout，失败降级 cv2 三级方案（`padding` 仅作用于 cv2 降级路径）。
  - `_get_doclayout_model()` → 懒加载 + 全程容错（ImportError/加载失败/推理异常/无正文框 均降级）。
  - `import cv2` 已改为函数内懒导入，模块级 import 不再依赖 cv2（利好 CI/轻量部署）。
- `kzocr/engine/run.py::_crop_to_body`(L312)：调用 `crop_by_layout`，None 时 `_crop_to_body_fallback`。
- `kzocr/scheduler/orchestrator.py:60`（`render_pages`）：v0.7 编排路径也已调用 `_crop_to_body`（scheduler 模块在 main 已随"编排重构"落地，非纯设计阶段）。
- `tests/test_layout_crop_doclayout.py`：6 个测试，mock paddlex/cv2，CI 可跑；另有 `tests/test_layout_crop.py`。

## 4. 验证方式
- 单元：`python -m pytest tests/test_layout_crop.py tests/test_layout_crop_doclayout.py -v`（全过）。
- ruff：改动文件零报错（全量 `ruff check kzocr/ tests/` 仍含历史错误，非本次引入——已由 PR #12 清理）。
- 真实路径：实测 `(2055,1430,3) -> (1637,1159,3)`。
- CI：run `29158420366` 全绿（合并 main 后触发）。

## 5. 未跟踪的临时文件（未提交，仅验证用）
- `_doclayout_preview.py`（PP-DocLayoutV3 预览+耗时+裁切图）、`_dump_layout.py`（cv2 中间数据 dump）、`_crop_preview_run.py`（旧 cv2 预览）、`crop_preview/`（输出图）、本快照文件。
- 样本图：`/home/keen/Documents/OCR0625/mi-by-ppocrv6/images/page_00XX.png`。

## 6. 建议的下一步
- **收尾**：开 PR `feat/v0.7-layout-crop` → `main`（实现已完成 + CI 全绿 + 已同步 main）。
- 或在开 PR 前做**端到端视觉回看**（`python _doclayout_preview.py 31 32 ...` → `crop_preview/page_00XX_doclayout_crop.png`，目视检查裁切质量）。
- 集成注意事项：PP-DocLayoutV3 属重模型依赖（paddle/paddlex），不进 CI；保留 cv2 为无依赖降级。

## 7. 关键约束（供恢复时参考）
- 提交前 `ruff check` 改动文件无报错；测试外部依赖必须 mock。
- 真实 egress 路径：`kzocr/security/egress.py`。
- v0.7 已落地代码含 `scheduler/{registry,scheduler,verifier,orchestrator}.py` 与 `types.py` 的 `EngineRunner(Protocol)`/`AdapterMeta`。记忆 `project_layout_pivot.md` 中"v0.7 仅 types.py 落地、scheduler 不存在"的断言**已过时**（main 已含编排重构）。
