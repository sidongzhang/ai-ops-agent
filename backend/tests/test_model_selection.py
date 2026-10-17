"""模型选择的行为保证：显式选了 local/api/advanced 时**不得静默回退**到别的模型。

为什么重要：用户选"本地模型"的动机常常是**数据不出内网**。如果本地模型失败后
代码悄悄改用远程 API，就等于把数据发出去了——必须显式失败，由用户看到。
"""
import unittest

from app.agent.diagnostics.fallback import FallbackMode, api_fallback_model, decide_fallback


class ModelFallbackPolicyTests(unittest.TestCase):
    def test_local_never_falls_back_to_api(self) -> None:
        self.assertIsNone(
            api_fallback_model("local"),
            "选了本地模型时不得回退到远程 API（数据出网的合规风险）",
        )

    def test_explicit_api_or_advanced_never_fall_back(self) -> None:
        for mode in ("api", "advanced", "default", "local"):
            self.assertIsNone(api_fallback_model(mode), f"{mode} 不应回退")

    def test_auto_may_fall_back(self) -> None:
        """auto 模式：本地模型失败时允许回退到 API（当前 llm_mode=local 时才有效）。"""
        from app.agent.llm import default_endpoint

        if default_endpoint().mode == "local":
            self.assertIsNotNone(api_fallback_model("auto"))
            self.assertIsNotNone(api_fallback_model(""))
        else:
            # 默认就是 API 时，没有可回退的目标
            self.assertIsNone(api_fallback_model("auto"))


class FallbackDecisionTests(unittest.TestCase):
    def test_images_never_downgrade_to_text(self) -> None:
        decision = decide_fallback(model_mode="auto", has_images=True)
        self.assertIs(decision.mode, FallbackMode.NONE)
        self.assertEqual(decision.reason, "image_input_requires_multimodal")

    def test_explicit_local_gets_text_retry_not_model_switch(self) -> None:
        decision = decide_fallback(model_mode="local", has_images=False)
        self.assertIs(decision.mode, FallbackMode.TEXT)
        self.assertIsNone(decision.model)

    def test_auto_local_with_key_switches_to_api(self) -> None:
        from unittest.mock import patch

        from app.agent.diagnostics import fallback as policy

        fake_api = object()
        with patch.object(policy, "default_endpoint", return_value=type("E", (), {"mode": "local"})()), \
             patch.object(policy, "endpoint_for_mode", return_value=type("E", (), {"api_key": "sk-test"})()), \
             patch.object(policy, "make_chat_model", return_value=fake_api):
            decision = policy.decide_fallback(model_mode="auto", has_images=False)

        self.assertIs(decision.mode, FallbackMode.API_MODEL)
        self.assertIs(decision.model, fake_api)
        self.assertEqual(decision.reason, "local_model_failed")


class LocalEndpointTests(unittest.TestCase):
    def test_local_endpoint_points_to_ollama_compatible_url(self) -> None:
        """本地端点必须是 OpenAI 兼容路径（我们按 OpenAI 协议调用 Ollama）。"""
        from app.agent.llm import endpoint_for_mode

        ep = endpoint_for_mode("local")
        self.assertEqual(ep.mode, "local")
        self.assertTrue(ep.base_url, "本地端点必须配置 base_url（如 http://localhost:11434/v1）")
        self.assertTrue(ep.model, "本地端点必须配置模型名")


class LocalModelPreflightTests(unittest.TestCase):
    """本地模型的**前置就绪检查**（避免"选完才发现跑不了/卡住"）。"""

    def _client(self, *, status: int = 200, payload=None, exc: Exception | None = None):
        import httpx

        def handler(request):  # noqa: ANN001
            if exc is not None:
                raise exc
            return httpx.Response(status, json=payload if payload is not None else {"data": []})

        return httpx.Client(transport=httpx.MockTransport(handler))

    def _local(self, model: str = "qwen2.5:3b"):
        from app.agent.llm import LLMEndpoint

        return LLMEndpoint(
            mode="local", model=model,
            base_url="http://localhost:11434/v1", api_key="ollama",
        )

    def test_ready_when_model_present(self) -> None:
        from app.agent.llm import local_model_status

        client = self._client(payload={"data": [{"id": "qwen2.5:3b"}]})
        self.assertIsNone(local_model_status(self._local(), client=client))

    def test_reason_when_model_missing(self) -> None:
        from app.agent.llm import local_model_status

        client = self._client(payload={"data": [{"id": "smollm2:135m"}]})
        reason = local_model_status(self._local("qwen2.5:7b"), client=client)
        self.assertIsNotNone(reason)
        self.assertIn("未安装", reason)
        self.assertIn("ollama pull qwen2.5:7b", reason, "要给出可直接照做的修复命令")

    def test_missing_tag_matches_latest(self) -> None:
        """Ollama 把没写 tag 的名字归一化为 `:latest`：不能因此误判"未安装"。

        真实踩过：配置 `aiops-qwen2.5-3b`（无 tag），`/v1/models` 返回
        `aiops-qwen2.5-3b:latest` → 严格比较判成"未安装"，连诊断都被 409 挡住。
        """
        from app.agent.llm import local_model_status

        client = self._client(payload={"data": [{"id": "aiops-qwen2.5-3b:latest"}]})
        self.assertIsNone(local_model_status(self._local("aiops-qwen2.5-3b"), client=client))
        # 反方向也要成立
        client2 = self._client(payload={"data": [{"id": "qwen2.5:3b"}]})
        self.assertIsNone(local_model_status(self._local("qwen2.5:3b:latest"), client=client2))

    def test_similar_names_do_not_false_match(self) -> None:
        from app.agent.llm import local_model_status

        client = self._client(payload={"data": [{"id": "qwen2.5:7b"}]})
        reason = local_model_status(self._local("qwen2.5:3b"), client=client)
        self.assertIsNotNone(reason, "不同尺寸的模型不能被判成同一个")

    def test_reason_when_endpoint_unreachable(self) -> None:
        import httpx

        from app.agent.llm import local_model_status

        client = self._client(exc=httpx.ConnectError("connection refused"))
        reason = local_model_status(self._local(), client=client)
        self.assertIsNotNone(reason)
        self.assertIn("连不上", reason)

    def test_reason_when_service_errors(self) -> None:
        from app.agent.llm import local_model_status

        reason = local_model_status(self._local(), client=self._client(status=500))
        self.assertIsNotNone(reason)
        self.assertIn("500", reason)

    def test_api_endpoint_is_not_probed(self) -> None:
        """远程 API 端点不做本地检查（否则每次诊断都多一次无谓的探测）。"""
        from app.agent.llm import LLMEndpoint, local_model_status

        api = LLMEndpoint(mode="api", model="deepseek-chat", base_url="https://x/v1", api_key="k")
        self.assertIsNone(local_model_status(api, client=self._client(status=500)))

    def test_ensure_model_ready_raises_with_reason(self) -> None:
        from unittest import mock

        from app.agent.llm import LocalModelUnavailable, ensure_model_ready

        client = self._client(payload={"data": [{"id": "smollm2:135m"}]})
        with mock.patch("app.agent.llm.endpoint_for_choice", return_value=self._local("qwen2.5:7b")):
            with self.assertRaises(LocalModelUnavailable) as ctx:
                ensure_model_ready("local", client=client)
        self.assertIn("本地模型未就绪", str(ctx.exception))

    def test_ensure_model_ready_skips_auto(self) -> None:
        """auto 不做检查（由路由按问题严重程度决定），也不应触发任何网络调用。"""
        from app.agent.llm import ensure_model_ready

        self.assertIsNone(ensure_model_ready("auto"))

    def test_local_model_client_has_bounded_read_timeout(self) -> None:
        """本地模型必须带**有界读超时**：卡死时变成可读失败，而不是无限挂起。

        （实测：7B 放不下内存时，没有超时的诊断会挂 >8 分钟 0 输出，用户以为平台坏了。）
        """
        from app.agent.llm import endpoint_for_mode, make_chat_model
        from app.core.config import settings

        model = make_chat_model(endpoint_for_mode("local"))
        client = getattr(getattr(model, "_provider", None), "_client", None)
        timeout = getattr(client, "timeout", None)
        self.assertIsNotNone(timeout, "本地模型应使用带超时的 OpenAI 客户端")
        read = getattr(timeout, "read", None)
        self.assertIsNotNone(read, "本地客户端应设置 read 超时")
        # 与配置一致，且必须"有界"（不可能是无限等待）
        self.assertEqual(float(read), float(settings.llm_local_timeout_seconds))
        self.assertGreater(float(read), 0.0)
        self.assertLess(float(read), 24 * 3600, "本地模型读超时必须是个有限值（防卡死）")

    def test_local_model_timeout_is_configurable(self) -> None:
        from app.agent.llm import endpoint_for_mode, make_chat_model
        from app.core.config import settings

        original = settings.llm_local_timeout_seconds
        try:
            settings.llm_local_timeout_seconds = 42.0
            model = make_chat_model(endpoint_for_mode("local"))
            client = getattr(getattr(model, "_provider", None), "_client", None)
            self.assertEqual(float(getattr(client.timeout, "read", 0)), 42.0)
        finally:
            settings.llm_local_timeout_seconds = original


if __name__ == "__main__":
    unittest.main()
