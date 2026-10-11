"""沙箱出图：装了 numpy/matplotlib 的沙箱解释器能生成 PNG 产物。

未配置沙箱解释器或缺 numpy 时自动跳过（保证无依赖环境下 CI 仍绿）。
"""
import subprocess
import sys

import pytest

from app.core.config import settings
from app.services.diagnostics.sandbox import run_python


def _sandbox_has_numpy() -> bool:
    interp = settings.sandbox_python or sys.executable
    try:
        proc = subprocess.run([interp, "-c", "import numpy, matplotlib"], capture_output=True, timeout=60)
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _sandbox_has_numpy(), reason="沙箱解释器未安装 numpy/matplotlib")
def test_sandbox_generates_png_chart():
    code = (
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "import numpy as np\n"
        "x = np.arange(6)\n"
        "plt.plot(x, x * x)\n"
        "plt.title('demo')\n"
        "plt.savefig('chart.png', dpi=80)\n"
        "print('saved')\n"
    )
    result = run_python(code, timeout=120)
    assert result["ok"], result["stderr"][:400]
    assert "saved" in result["stdout"]
    assert "chart.png" in result["outputs"]
    assert result["outputs"]["chart.png"][:8] == b"\x89PNG\r\n\x1a\n"
