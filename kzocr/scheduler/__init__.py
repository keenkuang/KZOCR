"""v0.7 自适应引擎编排层（落地中）。

本包承载 E1–E4：EngineRegistry / EngineScheduler / GlyphVerifier / Orchestrator。
- E1（registry.py）：注册中心、统计、候选选择、benchmark 持久化、状态位、探测。
- E2（scheduler.py）：EngineScheduler 完整候选选择流程（覆盖/层级/竖排跳过 T1/
  allow_cloud_vision/资源过滤/预算/加权排序×衰减×领域权重/Top-N/5% 轮询）。
- E3（verifier.py）/ E4（orchestrator.py）：待实现。
"""
