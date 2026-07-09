"""云端 LLM 大池测试。"""
from __future__ import annotations

import base64
import os
from unittest.mock import MagicMock, mock_open, patch

import pytest

from kzocr.modelscope_pool import (
    CloudLLMPool,
    ModelScopePool,
    ProviderSpec,
    _ProviderPool,
)


# ───────────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────────

@pytest.fixture
def text_spec():
    """非视觉 provider 规格。"""
    return ProviderSpec(
        name="test-provider",
        base_url="https://test.api/v1",
        api_key_env="TEST_API_KEY",
        models=["model-a", "model-b"],
        vision=False,
    )


@pytest.fixture
def text_vision_spec():
    """视觉 provider 规格。"""
    return ProviderSpec(
        name="test-vision",
        base_url="https://vision.api/v1",
        api_key_env="TEST_VISION_KEY",
        models=["vision-model-a", "vision-model-b"],
        vision=True,
    )


# ───────────────────────────────────────────────────────────────────────────
# ProviderSpec
# ───────────────────────────────────────────────────────────────────────────

class TestProviderSpec:
    """ProviderSpec 数据类测试。"""

    def test_dataclass_fields(self):
        """创建 ProviderSpec 并验证所有字段。"""
        spec = ProviderSpec(
            name="test",
            base_url="https://api.test/v1",
            api_key_env="TEST_KEY",
            models=["m1", "m2"],
        )
        assert spec.name == "test"
        assert spec.base_url == "https://api.test/v1"
        assert spec.api_key_env == "TEST_KEY"
        assert spec.models == ["m1", "m2"]
        assert spec.vision is False
        assert spec.api_key_fallback == ""

    def test_vision_provider(self):
        """设置 vision=True 的 provider。"""
        spec = ProviderSpec(
            name="vision-pro",
            base_url="https://vision.api/v1",
            api_key_env="VISION_KEY",
            models=["vm1"],
            vision=True,
        )
        assert spec.vision is True


# ───────────────────────────────────────────────────────────────────────────
# _ProviderPool
# ───────────────────────────────────────────────────────────────────────────

class TestProviderPool:
    """单 provider 内池测试。"""

    def test_disabled_when_no_key(self, text_spec):
        """未设置环境变量 → enabled=False。"""
        pool = _ProviderPool(text_spec)
        assert not pool.enabled

    def test_disabled_chat_returns_none(self, text_spec):
        """未设置 key → chat() 返回 None。"""
        pool = _ProviderPool(text_spec)
        assert pool.chat([{"role": "user", "content": "hi"}]) is None

    def test_enabled_with_key(self, text_spec):
        """设置环境变量 → enabled=True。"""
        with patch.dict(os.environ, {"TEST_API_KEY": "my-key"}):
            with patch("kzocr.modelscope_pool.OpenAI") as mock_openai:
                pool = _ProviderPool(text_spec)
                assert pool.enabled
                mock_openai.assert_called_once_with(
                    base_url="https://test.api/v1", api_key="my-key",
                )

    def test_chat_success(self, text_spec):
        """mock _do_call 返回响应 → chat() 返回该响应。"""
        with patch.dict(os.environ, {"TEST_API_KEY": "key"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = _ProviderPool(text_spec)
                with patch.object(pool, "_do_call", return_value="ok response"):
                    result = pool.chat([{"role": "user", "content": "hi"}])
                    assert result == "ok response"

    def test_chat_all_fail(self, text_spec):
        """所有模型都失败 → chat() 返回 None。"""
        with patch.dict(os.environ, {"TEST_API_KEY": "key"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = _ProviderPool(text_spec)
                with patch.object(pool, "_do_call", side_effect=Exception("fail")):
                    result = pool.chat([{"role": "user", "content": "hi"}])
                    assert result is None

    def test_chat_success_after_retry(self, text_spec):
        """第一个模型失败，第二个成功 → 返回第二个模型的响应。"""
        with patch.dict(os.environ, {"TEST_API_KEY": "key"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = _ProviderPool(text_spec)
                with patch.object(pool, "_do_call", side_effect=[
                    Exception("first fail"),
                    "second ok",
                ]):
                    result = pool.chat([{"role": "user", "content": "hi"}])
                    assert result == "second ok"
                    # _idx 已推进到 1（第二个模型）
                    assert pool.current_model == "model-b"

    def test_chat_disabled(self, text_spec):
        """不设置 key → chat() 返回 None（即使 mock _do_call）。"""
        pool = _ProviderPool(text_spec)
        with patch.object(pool, "_do_call", return_value="should-not-happen"):
            result = pool.chat([{"role": "user", "content": "hi"}])
            assert result is None

    def test_current_model(self, text_spec):
        """验证 current_model 属性。"""
        with patch.dict(os.environ, {"TEST_API_KEY": "key"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = _ProviderPool(text_spec)
                assert pool.current_model == "model-a"

    def test_vision_no_image(self, text_vision_spec):
        """既没有 image_path 也没有 image_data_url → chat_vision() 返回 None。"""
        with patch.dict(os.environ, {"TEST_VISION_KEY": "key"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = _ProviderPool(text_vision_spec)
                result = pool.chat_vision("描述图片")
                assert result is None


# ───────────────────────────────────────────────────────────────────────────
# _ProviderPool 视觉请求
# ───────────────────────────────────────────────────────────────────────────

class TestProviderPoolVision:
    """_ProviderPool 视觉请求测试。"""

    def test_chat_vision_with_image_path(self, text_vision_spec):
        """传 image_path → 转 data URL → 调 _vision_call 成功。"""
        with patch.dict(os.environ, {"TEST_VISION_KEY": "key"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = _ProviderPool(text_vision_spec)
                with patch.object(pool, "_image_to_data_url", return_value="data:image/jpeg;base64,fake"):
                    with patch.object(pool, "_vision_call", return_value="vision text"):
                        result = pool.chat_vision("识别", image_path="/fake/path.jpg")
                        assert result == "vision text"

    def test_chat_vision_with_data_url(self, text_vision_spec):
        """直接传 image_data_url → 不调 _image_to_data_url → 调 _vision_call 成功。"""
        with patch.dict(os.environ, {"TEST_VISION_KEY": "key"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = _ProviderPool(text_vision_spec)
                with patch.object(pool, "_vision_call", return_value="vision text"):
                    result = pool.chat_vision("识别", image_data_url="data:image/png;base64,fake")
                    assert result == "vision text"

    def test_chat_vision_file_not_found(self, text_vision_spec):
        """传不存在的 image_path → 抛出 FileNotFoundError。"""
        with patch.dict(os.environ, {"TEST_VISION_KEY": "key"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = _ProviderPool(text_vision_spec)
                with pytest.raises(FileNotFoundError):
                    pool.chat_vision("识别", image_path="/nonexistent/file.jpg")


# ───────────────────────────────────────────────────────────────────────────
# CloudLLMPool
# ───────────────────────────────────────────────────────────────────────────

class TestCloudLLMPool:
    """顶层大池测试。"""

    def test_init_empty(self):
        """无环境变量 → 无启用的 provider。"""
        with patch.dict(os.environ, {}, clear=True):
            pool = CloudLLMPool()
            assert pool.enabled_providers == []

    def test_init_with_only_filter(self):
        """指定 only 参数 → 只初始化指定 provider。"""
        with patch.dict(os.environ, {"MODELSCOPE_API_KEY": "key"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = CloudLLMPool(only=["modelscope"])
                assert len(pool._providers) == 1
                assert pool._providers[0].name == "modelscope"
                assert "modelscope" in pool.enabled_providers

    def test_chat_no_providers(self):
        """无文本 provider → chat() 返回 None。"""
        with patch.dict(os.environ, {}, clear=True):
            pool = CloudLLMPool()
            result = pool.chat(messages=[{"role": "user", "content": "hi"}])
            assert result is None

    def test_chat_with_provider(self):
        """有文本 provider 且 mock chat 成功 → 返回 mock 响应。"""
        with patch.dict(os.environ, {"MODELSCOPE_API_KEY": "key"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = CloudLLMPool()
                assert len(pool._text_providers) > 0
                with patch.object(pool._text_providers[0], "chat", return_value="mock response"):
                    result = pool.chat(messages=[{"role": "user", "content": "hi"}])
                    assert result == "mock response"

    def test_chat_all_fail(self):
        """所有文本 provider 的 chat 都返回 None → 最终返回 None。"""
        with patch.dict(os.environ, {"MODELSCOPE_API_KEY": "k1", "OFOX_API_KEY": "k2"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = CloudLLMPool()
                for p in pool._text_providers:
                    p.chat = MagicMock(return_value=None)
                result = pool.chat(messages=[{"role": "user", "content": "hi"}])
                assert result is None

    def test_chat_vision_no_providers(self):
        """无视觉 provider → chat_vision() 返回 None。"""
        with patch.dict(os.environ, {}, clear=True):
            pool = CloudLLMPool()
            result = pool.chat_vision("识别", image_data_url="data:image/jpeg;base64,fake")
            assert result is None

    def test_chat_vision_success(self):
        """有视觉 provider 且 mock chat_vision 成功 → 返回 OCR 文本。"""
        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "sk"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = CloudLLMPool()
                assert len(pool._vision_providers) > 0
                with patch.object(pool._vision_providers[0], "chat_vision", return_value="ocr result"):
                    result = pool.chat_vision("识别文字", image_data_url="data:image/jpeg;base64,fake")
                    assert result == "ocr result"

    def test_enabled_providers(self):
        """设置一个 key → enabled_providers 返回该 provider 名。"""
        with patch.dict(os.environ, {"MODELSCOPE_API_KEY": "key"}):
            with patch("kzocr.modelscope_pool.OpenAI"):
                pool = CloudLLMPool()
                eps = pool.enabled_providers
                assert "modelscope" in eps
                # 其它没有 key 的 provider 不应出现
                for name in ("siliconflow", "zai", "zhipu", "sensenova"):
                    assert name not in eps


# ───────────────────────────────────────────────────────────────────────────
# ModelScopePool 别名
# ───────────────────────────────────────────────────────────────────────────

class TestModelScopeAlias:
    """向后兼容别名测试。"""

    def test_alias(self):
        """ModelScopePool 就是 CloudLLMPool。"""
        assert ModelScopePool is CloudLLMPool


# ───────────────────────────────────────────────────────────────────────────
# _image_to_data_url
# ───────────────────────────────────────────────────────────────────────────

class TestImageToDataUrl:
    """_image_to_data_url 静态方法测试。"""

    def test_image_to_data_url(self):
        """mock 文件读取 → 验证 base64 输出格式。"""
        fake_data = b"\x89PNG\r\n\x1a\nfake-image-data"
        with patch("builtins.open", mock_open(read_data=fake_data)):
            result = _ProviderPool._image_to_data_url("/fake/path.png", mime="image/png")
            assert result.startswith("data:image/png;base64,")
            b64_part = result.split("base64,")[1]
            decoded = base64.b64decode(b64_part)
            assert decoded == fake_data

    def test_image_to_data_url_default_mime(self):
        """默认 mime 是 image/jpeg。"""
        with patch("builtins.open", mock_open(read_data=b"fake")):
            result = _ProviderPool._image_to_data_url("/fake/path")
            assert result.startswith("data:image/jpeg;base64,")
