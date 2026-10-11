"""诊断 agent 的 Python 沙箱：在隔离工作目录内执行一次性脚本，读取给定输入文件、
产出输出文件。用于"跑代码做分析并生成报告/图表文件"。

安全边界（尽力而为，非强隔离）：
  - 独立临时工作目录，执行后删除；
  - 单独子进程 + 超时；`-I` 隔离模式（忽略 PYTHON* 环境变量与 user site）；
  - 清空环境变量（不传代理/密钥），仅保留 PATH/HOME/LANG；
  - POSIX 资源限制：CPU 时间、地址空间、单文件大小、子进程数。

⚠️ 这不是安全沙箱（无法阻断本机网络/文件读取）。面向生产请把本文件换成容器/
gVisor/nsjail 执行。这里只用于受信环境下的数据分析。
"""
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Mapping

from app.core.config import settings

DEFAULT_TIMEOUT = 20
MAX_OUTPUT_BYTES = 200_000
MAX_OUTPUT_FILES = 20
MAX_FILE_BYTES = 5_000_000
_SCRIPT_NAME = "__main__.py"


def _preexec():
    """子进程资源限制（仅 POSIX）。"""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (DEFAULT_TIMEOUT, DEFAULT_TIMEOUT + 2))
        resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))  # 1 GiB
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        except (ValueError, OSError):
            pass
    except Exception:  # noqa: BLE001 - 非 POSIX 或受限环境，退化为仅超时
        pass


def _safe_name(name: str) -> str:
    base = os.path.basename(str(name or "input"))
    return base or "input"


def run_python(code: str, files: Mapping[str, bytes] | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """在工作目录内执行 code，返回 {ok, stdout, stderr, outputs, returncode}。

    outputs: {文件名: bytes}，即脚本新产生（不含输入文件与本脚本）的文件。
    """
    files = files or {}
    workdir = tempfile.mkdtemp(prefix="aiops-sbx-")
    try:
        used: set[str] = set()
        for name, data in files.items():
            target = _safe_name(name)
            while target in used:
                target = "_" + target
            used.add(target)
            with open(os.path.join(workdir, target), "wb") as fh:
                fh.write(data)
        script_path = os.path.join(workdir, _SCRIPT_NAME)
        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(code)

        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": workdir,
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "MPLBACKEND": "Agg",          # matplotlib 无界面后端，便于在沙箱里保存图片
        }
        try:
            proc = subprocess.run(
                [settings.sandbox_python or sys.executable, "-I", script_path],
                cwd=workdir,
                env=env,
                capture_output=True,
                timeout=timeout,
                preexec_fn=_preexec if os.name == "posix" else None,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "stdout": "", "stderr": f"执行超时（>{timeout}s）", "outputs": {}, "returncode": None}

        outputs: dict[str, bytes] = {}
        for name in sorted(os.listdir(workdir)):
            if name == _SCRIPT_NAME or name in used or len(outputs) >= MAX_OUTPUT_FILES:
                continue
            path = os.path.join(workdir, name)
            if os.path.isfile(path) and os.path.getsize(path) <= MAX_FILE_BYTES:
                with open(path, "rb") as fh:
                    outputs[name] = fh.read()

        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", "replace"),
            "stderr": proc.stderr[:MAX_OUTPUT_BYTES].decode("utf-8", "replace"),
            "outputs": outputs,
            "returncode": proc.returncode,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
