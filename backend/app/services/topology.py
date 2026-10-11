"""服务依赖拓扑：把「谁依赖谁」建成有向图，供 agent 判断**影响面**与**根因链**。

边方向约定：`{from: A, to: B}` 表示 **A 依赖 B**（B 故障会影响 A）。
来源：运维在 `system.infra.topology.edges` 里声明；节点默认取该系统已启用服务。

为什么有用：平台此前只能平铺看指标/日志；有了拓扑，agent 能回答
「某服务挂了会影响哪些下游」「根因可能在哪一环」，把并列现象收敛成一条链。
"""
from app.core.security import decrypt_sensitive_fields


def load_edges(system) -> list[dict]:
    infra = decrypt_sensitive_fields(system.infra or {})
    cfg = infra.get("topology") or {}
    edges: list[dict] = []
    for item in cfg.get("edges") or []:
        if not isinstance(item, dict):
            continue
        frm, to = str(item.get("from") or "").strip(), str(item.get("to") or "").strip()
        if frm and to:
            edges.append({"from": frm, "to": to, "kind": str(item.get("kind") or "").strip()})
    return edges


def build_graph(system, services) -> dict:
    """返回 {nodes, edges}；节点=已启用服务 + 边里出现的名字。"""
    edges = load_edges(system)
    nodes = [str(getattr(s, "name", s)) for s in (services or [])]
    seen = set(nodes)
    for edge in edges:
        for name in (edge["from"], edge["to"]):
            if name not in seen:
                seen.add(name)
                nodes.append(name)
    return {"nodes": nodes, "edges": edges}


def describe(graph: dict) -> str:
    edges = graph.get("edges") or []
    if not edges:
        return ""
    lines = ["## 服务依赖拓扑（A → B 表示「A 依赖 B」，B 故障会影响 A）"]
    for edge in edges:
        kind = f"（{edge['kind']}）" if edge.get("kind") else ""
        lines.append(f"- {edge['from']} → {edge['to']}{kind}")
    lines.append("判断影响面/根因时请参照上述依赖方向，不要凭空推测。")
    return "\n".join(lines)


def neighbors(graph: dict, name: str) -> str:
    """给定服务：列出它依赖的下游、以及依赖它的上游（受影响面）。"""
    name = str(name or "").strip()
    if not name:
        return "请提供要查询的服务名。"
    edges = graph.get("edges") or []
    downstream = [e["to"] for e in edges if e["from"] == name]
    upstream = [e["from"] for e in edges if e["to"] == name]
    if not downstream and not upstream:
        return f"「{name}」在拓扑中没有依赖关系（可能未声明，或它不依赖任何服务）。"
    return (
        f"「{name}」的依赖关系：\n"
        f"- 它依赖（下游，这些故障会影响它）：{'、'.join(downstream) or '无'}\n"
        f"- 依赖它的（上游，它故障会影响它们）：{'、'.join(upstream) or '无'}"
    )
