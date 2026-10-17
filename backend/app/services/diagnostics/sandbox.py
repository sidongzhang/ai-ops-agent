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
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from typing import Mapping

from app.core import metrics
from app.core.config import settings

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20
MAX_OUTPUT_BYTES = 200_000
MAX_OUTPUT_FILES = 20
MAX_FILE_BYTES = 5_000_000
_SCRIPT_NAME = "__main__.py"


def blocked_reason() -> str | None:
    """返回"禁止执行"的原因；None 表示可以执行。

    此前 docker 不可用时会静默退回在 API 进程内执行模型生成的代码——那是
    读得到 `.env`、连得上内网和数据库的进程。宁可让工具失败，也不静默降级。
    """
    image = (settings.sandbox_container_image or "").strip()
    if image and not shutil.which("docker"):
        return (
            "已配置 SANDBOX_CONTAINER_IMAGE，但本机找不到 docker CLI；"
            "拒绝退回在 API 进程内执行模型代码"
        )
    if settings.is_production and not image:
        return "生产环境未配置 SANDBOX_CONTAINER_IMAGE，拒绝在 API 进程内执行模型代码"
    return None


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


def _docker_env() -> dict:
    """docker CLI 用的环境：未设 DOCKER_HOST 时自动指向 colima 的 socket。"""
    env = dict(os.environ)
    if not env.get("DOCKER_HOST"):
        sock = os.path.expanduser("~/.colima/default/docker.sock")
        if os.path.exists(sock):
            env["DOCKER_HOST"] = f"unix://{sock}"
    return env


def _docker_argv(workdir: str, name: str) -> list[str] | None:
    """配置了沙箱镜像且 docker 可用时，返回一次性容器执行命令；否则 None。"""
    image = (settings.sandbox_container_image or "").strip()
    if not image or not shutil.which("docker"):
        return None
    return [
        "docker", "run", "--rm", "--name", name,
        "--network", "none",                 # 断网：读不到平台密钥、连不上内网/数据库
        "--memory", "1g", "--cpus", "1", "--pids-limit", "256",
        "-v", f"{workdir}:/work", "-w", "/work",
        image, "python", "-I", f"/work/{_SCRIPT_NAME}",
    ]


def _cleanup_container(argv: list[str] | None) -> None:
    """超时/结束后强制移除容器（subprocess 超时只杀 docker 客户端，容器会残留）。"""
    if not argv or "--name" not in argv:
        return
    name = argv[argv.index("--name") + 1]
    try:
        subprocess.run(["docker", "rm", "-f", name], env=_docker_env(), capture_output=True, timeout=20)
    except Exception:  # noqa: BLE001
        pass


def _new_workdir() -> str:
    """在 $HOME 下（colima 共享目录内）创建一次性工作目录，确保容器 bind mount 可见。"""
    base = os.path.join(settings.repo_root, ".dev-stack", "sandbox-work")
    try:
        os.makedirs(base, exist_ok=True)
        return tempfile.mkdtemp(prefix="run-", dir=base)
    except Exception:  # noqa: BLE001 - 兜底用系统临时目录（容器模式下可能不可见）
        return tempfile.mkdtemp(prefix="aiops-sbx-")


def run_python(code: str, files: Mapping[str, bytes] | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """在工作目录内执行 code，返回 {ok, stdout, stderr, outputs, returncode}。

    outputs: {文件名: bytes}，即脚本新产生（不含输入文件与本脚本）的文件。
    """
    reason = blocked_reason()
    if reason:
        log.error("[sandbox] 拒绝执行：%s", reason)
        metrics.inc("aiops_sandbox_blocked_total")
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"沙箱不可用：{reason}",
            "outputs": {},
            "returncode": None,
        }

    files = files or {}
    workdir = _new_workdir()
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
        docker_argv = _docker_argv(workdir, f"aiops-sbx-{uuid.uuid4().hex[:12]}")
        argv = docker_argv or [settings.sandbox_python or sys.executable, "-I", script_path]
        try:
            proc = subprocess.run(
                argv,
                cwd=workdir,
                env=(_docker_env() if docker_argv else env),
                capture_output=True,
                timeout=timeout,
                preexec_fn=_preexec if (os.name == "posix" and not docker_argv) else None,
            )
        except subprocess.TimeoutExpired:
            _cleanup_container(docker_argv)
            return {"ok": False, "stdout": "", "stderr": f"执行超时（>{timeout}s）", "outputs": {}, "returncode": None}
        finally:
            _cleanup_container(docker_argv)

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
