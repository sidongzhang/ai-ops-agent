"""服务依赖拓扑：建图、描述、上下游查询。"""
from types import SimpleNamespace

from app.services import topology


def _system(edges):
    return SimpleNamespace(infra={"topology": {"edges": edges}})


def test_build_graph_includes_edge_nodes():
    system = _system([{"from": "A", "to": "B", "kind": "db"}])
    graph = topology.build_graph(system, [SimpleNamespace(name="A")])
    assert set(graph["nodes"]) == {"A", "B"}


def test_describe_lists_edges():
    graph = topology.build_graph(_system([{"from": "A", "to": "B"}]), [])
    text = topology.describe(graph)
    assert "A → B" in text


def test_describe_empty_when_no_edges():
    assert topology.describe(topology.build_graph(_system([]), [])) == ""


def test_neighbors_upstream_and_downstream():
    graph = topology.build_graph(
        _system([{"from": "A", "to": "B"}, {"from": "C", "to": "B"}]), []
    )
    text = topology.neighbors(graph, "B")
    assert "A" in text and "C" in text   # 上游：依赖 B 的
    assert "下游" in text


def test_neighbors_unknown_service():
    graph = topology.build_graph(_system([{"from": "A", "to": "B"}]), [])
    assert "没有依赖关系" in topology.neighbors(graph, "Z")
