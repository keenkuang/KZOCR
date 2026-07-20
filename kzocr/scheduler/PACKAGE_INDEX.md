# scheduler/ — v0.7 引擎编排层

| 文件 | 说明 |
|------|------|
| `orchestrator.py` | 编排主循环（orchestrate_book：Tier1→Tier2→Tier3→HumanGate） |
| `registry.py` | 引擎注册表（EngineRegistry、EngineRegistration） |
| `scheduler.py` | 自适应调度器（EngineScheduler、Budget、EngineOverrides） |
| `verifier.py` | 字形验证（GlyphVerifier）+ VL 视觉仲裁（VisionRecheckAdapter） |
| `cross_align.py` | 跨引擎分歧对齐（align_engines、Divergence、混淆集） |
| `review_manifest.py` | 校对清单/分歧高亮 HTML/bbox 可视化/审核回写 |
| `concurrency.py` | 并发执行（run_engines_concurrent、AdaptiveController） |
| `vl_budget.py` | VL 视觉仲裁预算跟踪（VLBudgetTracker） |
