"""规程（runbook）—— Pi 的 SKILL.md 思路：按需加载的流程文档。

放在知识库文档目录下的 `runbooks/` 子目录，格式为带 YAML frontmatter 的 Markdown：

    ---
    name: 归档文件解读
    description: 用户询问某次触发/爆发(TCat)的归档目录、文件清单或文件内容时使用。
    ---
    （正文：步骤、注意事项、验证方式）

诊断时**只把「名称 + 描述」目录注入 system prompt**（省 token），
agent 判断相关时再用 `load_runbook(name)` 取**完整步骤**。
"""
import re
from pathlib import Path

from .store import _docs_dir

MAX_BODY_CHARS = 6000
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_UNSAFE = re.compile(r"[^\w\u4e00-\u9fff.\-]+")


def _parse(text: str) -> tuple[dict, str]:
    meta: dict[str, str] = {}
    body = text or ""
    matched = _FRONTMATTER.match(body)
    if matched:
        body = body[matched.end():]
        for line in matched.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    return meta, body.strip()


def _runbooks_dir(system_id) -> Path:
    return _docs_dir(str(system_id)) / "runbooks"


def _safe(name: str) -> str:
    return _UNSAFE.sub("_", str(name or "").strip())[:128]


def list_runbooks(system_id) -> list[dict]:
    """返回 [{name, description, file}]。"""
    directory = _runbooks_dir(system_id)
    items: list[dict] = []
    if not directory.is_dir():
        return items
    for path in sorted(directory.glob("*.md")):
        try:
            meta, _body = _parse(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        items.append({
            "name": meta.get("name") or path.stem,
            "description": meta.get("description", ""),
            "file": path.name,
        })
    return items


def describe_catalog(system_id) -> str:
    """生成注入 prompt 的「规程目录」（只有名称+描述）。"""
    items = list_runbooks(system_id)
    if not items:
        return ""
    lines = [
        "## 可用规程（runbook）",
        "遇到下列场景时，先用 load_runbook(name) 取完整步骤再作答，不要凭经验臆造流程：",
    ]
    for item in items:
        line = f"- {item['name']}"
        if item["description"]:
            line += f"：{item['description']}"
        lines.append(line)
    return "\n".join(lines)


def load_runbook(system_id, name: str) -> str | None:
    """按名称加载规程正文（去掉 frontmatter），找不到返回 None。"""
    directory = _runbooks_dir(system_id)
    if not directory.is_dir():
        return None
    wanted = str(name or "").strip()
    target = _safe(wanted)
    for path in sorted(directory.glob("*.md")):
        try:
            meta, body = _parse(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if wanted in {meta.get("name"), path.stem, target} or path.name == target or path.stem == target:
            return body[:MAX_BODY_CHARS]
    return None
