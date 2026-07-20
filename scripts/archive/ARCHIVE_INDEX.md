# scripts/archive/ 索引

探索/临时脚本，已不再活跃使用。保留以供历史追溯。

## 版心裁切系列（2026-07-12 ~ 07-15）

| 文件 | 用途 |
|------|------|
| `_build_master_db.py` | 构建版心裁剪主数据库（一次跑完，后续实验秒级复用） |
| `_build_inpaint_db.py` | 构建 inpainted 块数据库 |
| `_compare_layout.py` | 同页 cv2 版心框 vs PP-DocLayoutV3 版心框并排对比 |
| `_crop_preview_run.py` | 对 page_0031~0040 跑版心裁切，生成对比图与边界数值 |
| `_cut_all.py` | 把 22~992 页全部用 cv2 公式裁一遍，自动找异常页并出图 |
| `_diag_gap.py` | 排查偶数页"所有检测行 x1<=15"的缺口页 |
| `_diag_pages.py` | 对问题页算 dl 左界 / cv2 左界 / 是否切到正文 |
| `_doclayout_preview.py` | 在样本古籍页上跑 PP-DocLayoutV3，输出按类别着色的检测预览图 |
| `_dump_blocks.py` | 打印每一块的精确坐标 + 叠放到原图 |
| `_dump_layout.py` | 打印 cv2 文字行检测中间数据结构 + 叠加可视化 |
| `_eval_user_algo.py` | 评估用户给的「奇数页左侧眉」算法 |
| `_measure_inner.py` | 快检内侧（无侧眉）边的 diff 是否≈0 |
| `_measure_user_formula.py` | 用户公式多候选对比 PP-DocLayoutV3 |
| `_preview_even_blocks.py` | 偶数页 cv2 文字块可视化 |
| `_preview_even_cv2.py` | 偶数页 cv2 版心裁剪，打印边界数值并生成标注图 |
| `_preview_even_formula.py` | 用用户 right 公式跑偶数页版心，出图看效果 |
| `_preview_even_mirror.py` | 把奇数页规则镜像到偶数页，出图看效果 |
| `_preview_odd_formula.py` | 奇数页原公式 left_page_rule 渲染 |
| `_preview_odd.py` | 奇数页 cv2 块可视化（用生产 is_odd 分支） |
| `_render_abc_overcut.py` | 渲染 A/B/C 奇数页左侧眉算法的"过裁页" |
| `_render_compare.py` | 渲染全部 971 页，两种版心框叠加对比 |
| `_render_noB_pages.py` | 渲染 B=None 的页，看为何找不到 B |
| `_render_undertrim.py` | 渲染 inpaint+重调 v6 仍欠裁的页 |
| `_render_v4b_5.py` | 渲染 v4b 真过裁 5 页，目视确认 |
| `_render_v4c_5.py` | 渲染 v4c 于原 v4b 过裁页，目视确认是否消除 |
| `_rule_leftpage.py` | 验证用户提出的"左面页面"版心裁切规则 |
| `_test_inpaint_conditional.py` | 条件式 inpaint 预处理全量验证（485 奇数页） |
| `_test_preprocess_35.py` | page 35 单页实测图像预处理对边界的影响 |
| `_test_v4.py` | v4 公式秒级 dry-run |
| `_validate_fix.py` | min-safe 标定，确认不切正文、奇页裁侧眉 |
| `_validate_left.py` | 预验证新 left 公式 |
| `_validate_odd_c.py` | 验证奇数页侧眉感知 left (c) |
| `_verify_edge15_v4c.py` | band=15 边裁集成管线全量验证 |
| `_verify_edge15_v5.py` | v5：band=15 边裁 + 用户新左界公式 |
| `_verify_edge_clean.py` | 10px 自适应边裁原型验证 |
| `_verify_leftpage_rule.py` | 按新规则实现 cv2 版心，对照 doclayout 真值出图 |
| `_verify_production.py` | 端到端验证生产 cv2 路径 |
| `_verify_split_blocks.py` | 分块逻辑改写原型 v2 |
| `_verify_v4.py` | 全量验证 v4 奇数页左侧眉公式 |
| `_verify_v4b.py` | v4b 全量计算（不渲染） |
| `_verify_v4c.py` | v4c 全量验证 |
| `_analyze_eyebrow_width.py` | 统计侧眉（aside_text）宽度与左右缘 |
| `_analyze_from_db.py` | 从 crop_master.json 秒级评估任意左界公式 |
| `_analyze_inpaint_tune.py` | 在 inpainted 域重调 v6 系数 |
| `_analyze_v5_branches.py` | v5 问题页按公式分支统计 |

## ShizhenGPT 修复探针（2026-07-17）

| 文件 | 用途 |
|------|------|
| `shizhen_fix.py` | Stage1 修复：针对 ShizhenGPT 三个真问题 |
| `shizhen_maxtokens_diag.py` | 排查 ShizhenGPT 截断/失败根因 |
| `shizhen_page2_fix.py` | Stage1 page2 专项修复 |

## 真实引擎探针（2026-07-17）

| 文件 | 用途 |
|------|------|
| `real_cross_align_check.py` | 真实双引擎交叉验证（补齐 Stage4 未覆盖） |
| `real_stack_probe.py` | 真实引擎栈探针（验证编排层） |

## 数据文件

| 文件 | 说明 |
|------|------|
| `crop_master.json`（1.1 MB） | 版心裁剪主数据库（所有页的检测真值） |
| `crop_master_inpaint.json`（1.1 MB） | Inpaint 后重检测的块数据库 |
| `edge15_v4c_per_page.json` | 边裁 band=15 v4c 逐页结果 |
| `edge15_v5_per_page.json` | 边裁 band=15 v5 逐页结果 |
| `edge15_v6_per_page.json` | 边裁 band=15 v6 逐页结果 |
| `edge_clean_per_page.json` | 10px 自适应边裁逐页结果 |
| `split_blocks_per_page.json` | 分块逻辑改写逐页结果 |

## 子目录

| 目录 | 说明 |
|------|------|
| `crop_compare/` | 版心裁切对比图（12 项实验组合） |
| `crop_preview/` | 版心裁切预览标注图 |

## shell 脚本

| 文件 | 用途 |
|------|------|
| `_wait_measure.sh` | 仪表化等待辅助 |
| `_wait_measure_v2.sh` | 仪表化等待 v2 |
| `_wait_validate_odd_c.sh` | odd_c 验证等待 |
| `_wait_verify_production.sh` | 生产验证等待 |

## 其他

| 文件 | 说明 |
|------|------|
| `context_snapshot_2026-07-11.md` | 2026-07-11 对话上下文快照 |
