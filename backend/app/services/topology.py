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


def analyze(graph: dict, failing) -> dict:
    """按依赖方向推断根因：异常服务里「不依赖任何其它异常服务」的即疑似根因。

    返回 {failing, roots, chains:[{root, affected}]}。
    """
    edges = graph.get("edges") or []
    fail = {str(f).strip() for f in (failing or []) if str(f).strip()}
    deps: dict[str, set] = {}
    consumers: dict[str, set] = {}
    for edge in edges:
        deps.setdefault(edge["from"], set()).add(edge["to"])
        consumers.setdefault(edge["to"], set()).add(edge["from"])

    roots = sorted(n for n in fail if not (deps.get(n, set()) & fail))

    def affected_from(root: str) -> set[str]:
        seen: set[str] = set()
        stack = list(consumers.get(root, set()))
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(consumers.get(node, set()))
        return seen

    chains = []
    for root in roots:
        chains.append({"root": root, "affected": sorted(n for n in affected_from(root) if n in fail)})
    return {"failing": sorted(fail), "roots": roots, "chains": chains, "has_edges": bool(edges)}


def failure_context(graph: dict, failing) -> str:
    """生成注入 prompt 的「异常根因推断」块；无异常返回空串。"""
    analysis = analyze(graph, failing)
    if not analysis["failing"]:
        return ""
    if not analysis["has_edges"]:
        return (
            "## 当前异常服务\n- " + "、".join(analysis["failing"])
            + "\n（未配置服务依赖拓扑，无法判断依赖关系；如需可让运维补充拓扑）"
        )
    lines = ["## 异常根因推断（按服务依赖拓扑）"]
    if analysis["roots"]:
        for chain in analysis["chains"]:
            if chain["affected"]:
                lines.append(f"- 疑似根因：{chain['root']}；受影响：{'、'.join(chain['affected'])}")
            else:
                lines.append(f"- 异常服务：{chain['root']}（无下游异常，可能即根因）")
    lines.append("结论中以「疑似根因」为核心，受影响服务作为影响面；不要平铺罗列。")
    return "\n".join(lines)
