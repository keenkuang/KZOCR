"""kzocr/modelscope_pool.py 纯逻辑补充单测（零网络，仅构造无 key 池验证空分支）。

现有 test_modelscope_pool.py 已覆盖启用/故障转移路径；此处补「无文本/视觉 provider 时
current_model/current_vision_model 返回空串、chat/chat_vision 返回 None」分支。
"""

from __future__ import annotations

from kzocr.modelscope_pool import CloudLLMPool


def test_current_model_empty_when_no_text_provider(monkeypatch) -> None:
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    # only 全为视觉 provider → 无文本 provider → current_model 空、chat 返回 None
    pool = CloudLLMPool(only=["zai", "siliconflow"])
    assert pool.current_model == ""
    assert pool.chat([{"role": "user", "content": "x"}]) is None


def test_current_vision_model_empty_when_no_vision_provider(monkeypatch) -> None:
    monkeypatch.delenv("MODELSCOPE_API_KEY", raising=False)
    # only 全为文本 provider → 无视觉 provider → current_vision_model 空、chat_vision 返回 None
    pool = CloudLLMPool(only=["modelscope"])
    assert pool.current_vision_model == ""
    assert pool.chat_vision("识别", image_data_url="data:image/jpeg;base64,xxx") is None
