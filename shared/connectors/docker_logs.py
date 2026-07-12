"""Read logs from Docker containers by container name."""
import subprocess


def read_container_logs(container: str, lines: int = 50) -> str:
    container = (container or "").strip()
    if not container:
        return ""
    line_count = max(1, min(int(lines), 500))
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(line_count), container],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return f"获取容器 {container} 日志超时"
    except FileNotFoundError:
        return "未找到 docker 命令，无法读取容器日志"
    except Exception as exc:  # noqa: BLE001
        return f"获取容器日志失败: {exc}"

    chunks: list[str] = []
    if result.stdout:
        chunks.append(result.stdout.rstrip("\n"))
    if result.stderr:
        chunks.append(result.stderr.rstrip("\n"))
    combined = "\n".join(part for part in chunks if part)
    if combined:
        return combined
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        return err or f"容器 {container} 日志读取失败 (exit {result.returncode})"
    return f"容器 {container} 无日志输出"


def search_container_logs(container: str, keyword: str, lines: int = 200) -> str:
    keyword = (keyword or "").strip()
    if not keyword:
        return read_container_logs(container, lines)
    out = read_container_logs(container, lines)
    if out.startswith(("获取容器", "未找到 docker", "容器 ")) and "\n" not in out:
        return out
    matches = [line for line in out.splitlines() if keyword.lower() in line.lower()]
    if not matches:
        return f"在 {container} 最近 {lines} 行日志中未找到 '{keyword}'"
    return "\n".join(matches[-50:])
