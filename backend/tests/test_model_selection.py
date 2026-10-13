"""模型选择的行为保证：显式选了 local/api/advanced 时**不得静默回退**到别的模型。

为什么重要：用户选"本地模型"的动机常常是**数据不出内网**。如果本地模型失败后
代码悄悄改用远程 API，就等于把数据发出去了——必须显式失败，由用户看到。
"""
import unittest

from app.agent.diagnostics.runner import _fallback_api_model


class ModelFallbackPolicyTests(unittest.TestCase):
    def test_local_never_falls_back_to_api(self) -> None:
        self.assertIsNone(
            _fallback_api_model("local"),
            "选了本地模型时不得回退到远程 API（数据出网的合规风险）",
        )

    def test_explicit_api_or_advanced_never_fall_back(self) -> None:
        for mode in ("api", "advanced", "default"):
            self.assertIsNone(_fallback_api_model(mode), f"{mode} 不应回退")

    def test_auto_may_fall_back(self) -> None:
        """auto 模式：本地模型失败时允许回退到 API（当前 llm_mode=local 时才有效）。"""
        from app.agent.llm import default_endpoint

        if default_endpoint().mode == "local":
            self.assertIsNotNone(_fallback_api_model("auto"))
            self.assertIsNotNone(_fallback_api_model(""))
        else:
            # 默认就是 API 时，没有可回退的目标
            self.assertIsNone(_fallback_api_model("auto"))


class LocalEndpointTests(unittest.TestCase):
    def test_local_endpoint_points_to_ollama_compatible_url(self) -> None:
        """本地端点必须是 OpenAI 兼容路径（我们按 OpenAI 协议调用 Ollama）。"""
        from app.agent.llm import endpoint_for_mode

        ep = endpoint_for_mode("local")
        self.assertEqual(ep.mode, "local")
        self.assertTrue(ep.base_url, "本地端点必须配置 base_url（如 http://localhost:11434/v1）")
        self.assertTrue(ep.model, "本地端点必须配置模型名")


if __name__ == "__main__":
    unittest.main()
