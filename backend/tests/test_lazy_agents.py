"""回归：import agent 模块不应要求 LLM 凭证（CI / 纯离线评测会直接 import）。

历史问题：runner.py / workflows/models.py 在模块级 `Agent(default_model())`，
构造时即建立 OpenAI 客户端，没有凭证就在 import 阶段抛
`openai.OpenAIError: Missing credentials`（eval-gate 冒烟门禁因此失败）。
现在改为 LazyAgent 延迟构造。
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


class LazyAgentImportTests(unittest.TestCase):
    def test_module_level_agents_are_lazy(self) -> None:
        from app.agent.diagnostics import runner
        from app.agent.lazy_agent import LazyAgent
        from app.agent.workflows import models as workflow_models

        for agent in (
            runner.diagnose_agent,
            runner.text_fallback_agent,
            workflow_models.analysis_agent,
        ):
            self.assertIsInstance(agent, LazyAgent)

    def test_import_without_credentials_succeeds(self) -> None:
        env = {**os.environ, "DEEPSEEK_API_KEY": "", "LLM_API_KEY": ""}
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import app.agent.diagnostics, app.agent.workflows.models; print('ok')",
            ],
            cwd=str(BACKEND_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
