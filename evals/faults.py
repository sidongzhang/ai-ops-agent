"""故障注入驱动：docker_exec / docker_stop / docker_start / http / manual。

设计约束（见 evals/README.md）：
  * 只依赖标准库 + 仓库已有依赖（httpx）；
  * Docker 不可用时抛 `DockerUnavailable`，由 CLI 翻译成清晰提示并以非零码退出，**不打印 traceback**；
  * 每次注入/回滚都写一条 JSON 记录到 journal（默认 evals/results/injections.jsonl），
    使 `--teardown` 可以幂等重放；
  * 用例字段约定：
      - `inject.command`  机器可执行的注入命令（docker_exec 时在 target 容器内用 sh -c 执行）
      - `setup`           人工可读的准备说明（只打印，不执行）
      - `teardown`        docker_exec/docker_start 用例的机器可执行回滚命令列表
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlparse

EVALS_DIR = Path(__file__).resolve().parent
DEFAULT_JOURNAL = EVALS_DIR / "results" / "injections.jsonl"

DOCKER_ACTIONS = ("docker_exec", "docker_stop", "docker_start")

DOCKER_HINT = (
    "Docker 不可用。请先启动本地容器运行时：\n"
    "  colima start          # 或启动 Docker Desktop\n"
    "  docker compose up -d  # 在仓库根目录拉起 mysql/redis/kafka/prometheus 等\n"
    "然后重试。若只想验证评测脚手架本身，请使用不需要 Docker 的：\n"
    "  python evals/run_eval.py --validate\n"
    "  python evals/run_eval.py --dry-run"
)


class DockerUnavailable(RuntimeError):
    """本机没有可用的 Docker/Colima。"""


class FaultInjectionError(RuntimeError):
    """注入动作本身失败（Docker 可用但命令报错）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def docker_available(timeout: int = 8) -> bool:
    """探测 docker CLI + daemon 是否可用（`docker info` 成功才算可用）。"""
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def require_docker() -> None:
    if not docker_available():
        raise DockerUnavailable(DOCKER_HINT)


def case_needs_docker(case: dict) -> bool:
    return (case.get("inject") or {}).get("action") in DOCKER_ACTIONS


def preflight(cases: Sequence[dict]) -> None:
    """批量注入前一次性检查 Docker，避免做到一半才失败。"""
    if any(case_needs_docker(case) for case in cases):
        require_docker()


def _run(command: list[str], *, timeout: int = 90, cwd: str | Path | None = None) -> dict:
    started = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        output = (result.stdout or "") + (result.stderr or "")
        return {
            "command": command,
            "returncode": result.returncode,
            "ok": result.returncode == 0,
            "output": output.strip()[:4000],
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "returncode": -1,
            "ok": False,
            "output": f"命令超时（{timeout}s）",
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
    except OSError as exc:
        return {
            "command": command,
            "returncode": -1,
            "ok": False,
            "output": f"命令无法执行: {exc}",
            "duration_ms": round((time.monotonic() - started) * 1000),
        }


def docker_exec(target: str, command: str, *, timeout: int = 90) -> dict:
    """在容器内执行一条 shell 命令（等价于 `docker exec <target> sh -c <command>`）。"""
    if not target:
        raise FaultInjectionError("docker_exec 需要 inject.target（容器名）")
    if not command:
        raise FaultInjectionError("docker_exec 需要 inject.command")
    return _run(["docker", "exec", target, "sh", "-c", command], timeout=timeout)


def docker_stop(target: str, *, timeout: int = 90) -> dict:
    if not target:
        raise FaultInjectionError("docker_stop 需要 inject.target（容器名）")
    return _run(["docker", "stop", target], timeout=timeout)


def docker_start(target: str, *, timeout: int = 90) -> dict:
    if not target:
        raise FaultInjectionError("docker_start 需要 inject.target（容器名）")
    return _run(["docker", "start", target], timeout=timeout)


def docker_restart(target: str, *, timeout: int = 120) -> dict:
    """回滚用：让容器重新加载被改回的配置（不是 inject 动作之一）。"""
    if not target:
        raise FaultInjectionError("docker_restart 需要 target（容器名）")
    return _run(["docker", "restart", target], timeout=timeout)


def http_probe(target: str, command: str, *, timeout: int = 15) -> dict:
    """对目标 URL 发一次请求触发故障。

    `inject.command` 约定为 `"<METHOD> [JSON body]"`，例如 `"POST {\"flood\": true}"`；
    省略时按 GET 处理。
    """
    import httpx

    method = "GET"
    body = None
    text = (command or "").strip()
    if text:
        parts = text.split(" ", 1)
        method = parts[0].upper() or "GET"
        if len(parts) > 1 and parts[1].strip():
            try:
                body = json.loads(parts[1])
            except json.JSONDecodeError:
                body = {"raw": parts[1]}
    parsed = urlparse(target or "")
    if parsed.scheme not in ("http", "https"):
        raise FaultInjectionError(f"http 动作需要绝对 URL，收到: {target!r}")
    started = time.monotonic()
    try:
        response = httpx.request(method, target, json=body, timeout=timeout)
        return {
            "command": f"{method} {target}",
            "returncode": 0,
            "ok": response.status_code < 500,
            "output": f"HTTP {response.status_code} ({len(response.content)} bytes)",
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - 注入失败要记录而不是崩
        return {
            "command": f"{method} {target}",
            "returncode": -1,
            "ok": False,
            "output": f"HTTP 请求失败: {exc}",
            "duration_ms": round((time.monotonic() - started) * 1000),
        }


def _default_rollback(case: dict) -> list[str]:
    """没有显式 teardown 时给出的逆操作提示。"""
    action = (case.get("inject") or {}).get("action")
    target = (case.get("inject") or {}).get("target", "")
    if action == "docker_stop":
        return [f"docker start {target}"]
    if action == "docker_start":
        return [f"docker stop {target}"]
    if action == "docker_exec":
        return [f"docker exec {target} sh -c '<见 inject.command 的逆操作>'"]
    return []


def _write_journal(journal_path: str | Path | None, record: dict) -> None:
    if journal_path is None:
        journal_path = DEFAULT_JOURNAL
    path = Path(journal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _echo(message: str, echo: Callable[[str], None] | None) -> None:
    if echo:
        echo(message)


def inject(
    case: dict,
    *,
    journal_path: str | Path | None = None,
    echo: Callable[[str], None] | None = print,
) -> dict:
    """注入一条用例的故障。返回记录 dict（同时写入 journal）。"""
    spec = case.get("inject") or {}
    action = spec.get("action") or "manual"
    target = spec.get("target", "")
    command = spec.get("command", "")
    steps: list[dict] = []
    notes: list[str] = []

    if action == "manual":
        notes = list(case.get("setup") or []) or ["（用例未提供 setup 说明）"]
        _echo(f"[manual] 用例 {case.get('id')} 需要人工注入，请按下列步骤操作：", echo)
        for line in notes:
            _echo(f"    - {line}", echo)
    elif action == "http":
        steps.append(http_probe(target, command))
    elif action in DOCKER_ACTIONS:
        require_docker()
        if action == "docker_exec":
            steps.append(docker_exec(target, command))
        elif action == "docker_stop":
            steps.append(docker_stop(target))
        else:
            steps.append(docker_start(target))
    else:
        raise FaultInjectionError(f"未知 inject.action: {action!r}")

    ok = all(step.get("ok") for step in steps) if steps else True
    record = {
        "ts": _now(),
        "phase": "inject",
        "case_id": case.get("id", ""),
        "fault_type": case.get("fault_type", ""),
        "action": action,
        "target": target,
        "command": command,
        "ok": ok,
        "steps": steps,
        "notes": notes,
        "rollback": list(case.get("teardown") or []) or _default_rollback(case),
    }
    _write_journal(journal_path, record)
    if steps:
        for step in steps:
            _echo(f"[inject:{action}] {step['command']} -> rc={step['returncode']} ok={step['ok']}", echo)
            if step.get("output"):
                _echo(f"    {step['output'].splitlines()[0][:200]}", echo)
    return record


def teardown(
    case: dict,
    *,
    journal_path: str | Path | None = None,
    echo: Callable[[str], None] | None = print,
) -> dict:
    """回滚一条用例的故障（幂等：重复执行不会报错）。"""
    spec = case.get("inject") or {}
    action = spec.get("action") or "manual"
    target = spec.get("target", "")
    teardown_commands = list(case.get("teardown") or [])
    steps: list[dict] = []
    notes: list[str] = []

    if action == "manual":
        notes = teardown_commands or ["（用例未提供 teardown 说明）"]
        _echo(f"[manual] 用例 {case.get('id')} 需要人工恢复，请按下列步骤操作：", echo)
        for line in notes:
            _echo(f"    - {line}", echo)
    elif action == "http":
        # http 注入无配置级回滚，但 dataset 可给出 docker 命令兜底
        # （如 Prometheus /-/quit 后需 docker restart 恢复进程）。
        for line in teardown_commands:
            parts = line.strip().split()
            if parts and parts[0] == "docker":
                require_docker()
                if parts[1] == "restart" and len(parts) > 2:
                    steps.append(docker_restart(parts[2]))
                elif parts[1] in ("start", "stop") and len(parts) > 2:
                    steps.append(_run(parts))
                else:
                    notes.append(f"（http teardown 暂只支持 docker restart/start/stop，收到: {line}）")
            else:
                notes.append(line)
        if not steps and not notes:
            notes = ["（http 注入无自动回滚，按 setup 说明人工恢复）"]
        for line in notes:
            _echo(f"[teardown:http] {line}", echo)
    elif action in DOCKER_ACTIONS:
        require_docker()
        if action == "docker_stop":
            # 容器先起来，才能执行容器内的配置回滚
            steps.append(docker_start(target))
            for line in teardown_commands:
                steps.append(docker_exec(target, line))
        elif action == "docker_start":
            # 注入时容器是在坏配置下被拉起的：先恢复配置，再重启使配置生效
            for line in teardown_commands:
                steps.append(docker_exec(target, line))
            steps.append(docker_restart(target))
        else:  # docker_exec
            if not teardown_commands:
                notes = [f"（用例未提供 teardown，按需手工恢复 {target}）"]
            for line in teardown_commands:
                steps.append(docker_exec(target, line))
    else:
        raise FaultInjectionError(f"未知 inject.action: {action!r}")

    ok = all(step.get("ok") for step in steps) if steps else True
    record = {
        "ts": _now(),
        "phase": "teardown",
        "case_id": case.get("id", ""),
        "fault_type": case.get("fault_type", ""),
        "action": action,
        "target": target,
        "command": "",
        "ok": ok,
        "steps": steps,
        "notes": notes,
        "rollback": [],
    }
    _write_journal(journal_path, record)
    for step in steps:
        _echo(f"[teardown:{action}] {step['command']} -> rc={step['returncode']} ok={step['ok']}", echo)
    return record


def inject_all(
    cases: Sequence[dict],
    *,
    journal_path: str | Path | None = None,
    echo: Callable[[str], None] | None = print,
) -> list[dict]:
    """批量注入；Docker 相关用例会先做一次 preflight（不可用直接抛 DockerUnavailable）。"""
    preflight(cases)
    records = []
    for case in cases:
        try:
            records.append(inject(case, journal_path=journal_path, echo=echo))
        except DockerUnavailable:
            raise
        except FaultInjectionError as exc:
            _echo(f"[inject] 用例 {case.get('id')} 失败: {exc}", echo)
            records.append({"case_id": case.get("id"), "phase": "inject", "ok": False, "error": str(exc)})
    return records


def teardown_all(
    cases: Sequence[dict],
    *,
    journal_path: str | Path | None = None,
    echo: Callable[[str], None] | None = print,
) -> list[dict]:
    preflight(cases)
    records = []
    for case in cases:
        try:
            records.append(teardown(case, journal_path=journal_path, echo=echo))
        except DockerUnavailable:
            raise
        except FaultInjectionError as exc:
            _echo(f"[teardown] 用例 {case.get('id')} 失败: {exc}", echo)
            records.append({"case_id": case.get("id"), "phase": "teardown", "ok": False, "error": str(exc)})
    return records


def journal_summary(journal_path: str | Path | None = None) -> dict:
    """汇总 journal：哪些用例目前处于「已注入」状态（供 --list / 人工检查）。"""
    path = Path(journal_path) if journal_path else DEFAULT_JOURNAL
    summary: dict[str, str] = {}
    if not path.exists():
        return {"path": str(path), "injected": summary}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        case_id = record.get("case_id")
        if not case_id:
            continue
        summary[case_id] = record.get("phase", "")
    injected = {case_id: phase for case_id, phase in summary.items() if phase == "inject"}
    return {"path": str(path), "injected": injected}
