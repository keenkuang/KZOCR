"""v0.7 自适应引擎编排层（已落地）。

本包承载 E1–E4：EngineRegistry / EngineScheduler / GlyphVerifier / Orchestrator，
均已实现并合入 main（当前 v0.25.0）。
- E1（registry.py）：注册中心、统计、候选选择、benchmark 持久化、状态位、探测。
- E2（scheduler.py）：EngineScheduler 完整候选选择流程（覆盖/层级/竖排跳过 T1/
  allow_cloud_vision/资源过滤/预算/加权排序×衰减×领域权重/Top-N/5% 轮询）。
- E3（verifier.py）：跨引擎分歧检测（cross_align）+ 共识错误分流 + 形近字黑名单自学习。
- E4（orchestrator.py）：合并 Tier1 字符框 + conf≤0.90 门控 + 高分歧页送 Box-Guided
  VL 仲裁；并接入 review_manifest 校对反馈闭环。
"""
