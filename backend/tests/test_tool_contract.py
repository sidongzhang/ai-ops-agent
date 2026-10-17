"""工具契约护栏：注册的工具 ↔ 统一目录 ↔ 各类白名单必须一致。

为什么需要（架构文档 §6 第 4、7 条）：
  • `tool_catalog()` 由 `TOOL_CLASSES` 反向 `getattr(tools, name)` 生成，
    ToolGateway 只放行"目录已登记"的工具——**注册了但没归类 = 永远不可用**；
  • 目录里有、`tools.py` 里没有 → 构建目录时 AttributeError（启动即炸）；
  • 新增工具忘记归类 → 落到默认 `metrics` 类，**证据时效 TTL 会算错**；
  • 各类白名单（取证工具 / Pi 只读集）必须是目录的子集，否则网关拒绝执行。

这些测试不碰 DB / 网络，可在 CI 里跑。
"""
from pydantic_ai import Agent

from app.agent.diagnostics import tools as tools_module
from app.agent.diagnostics.tools import register_tools
from app.agent.harness.catalog import EVIDENCE_TOOLS, TOOL_CLASSES, tool_catalog


def _registered_tools() -> set[str]:
    agent = Agent("test")
    register_tools(agent)
    return set(getattr(agent._function_toolset, "tools", {}) or {})


def test_every_registered_tool_is_catalogued() -> None:
    missing = _registered_tools() - set(TOOL_CLASSES)
    assert missing == set(), f"有工具注册了但没在 TOOL_CLASSES 归类（网关会拒绝执行）: {sorted(missing)}"


def test_every_catalog_entry_resolves_to_an_implementation() -> None:
    missing = [name for name in TOOL_CLASSES if not callable(getattr(tools_module, name, None))]
    assert missing == [], f"目录里的工具在 tools.py 中不存在: {missing}"


def test_catalog_names_match_registry() -> None:
    catalog = tool_catalog()
    assert {spec.name for spec in catalog} == set(TOOL_CLASSES)
    assert len(catalog) == len(_registered_tools())


def test_input_models_reject_unknown_fields() -> None:
    """入参模型必须是 strict + extra=forbid：模型乱塞字段不能落到实现里。"""
    for spec in tool_catalog():
        assert spec.input_model is not None
        try:
            spec.input_model(**{"__not_a_field__": 1})
        except Exception:
            continue
        raise AssertionError(f"{spec.name} 的入参模型未拒绝未知字段")


def test_whitelists_are_subset_of_catalog() -> None:
    catalog = set(TOOL_CLASSES)
    assert EVIDENCE_TOOLS <= catalog, f"取证白名单不在目录里: {sorted(EVIDENCE_TOOLS - catalog)}"

    from app.agent.harness.bridge import PI_READ_TOOLS

    assert PI_READ_TOOLS <= catalog, f"Pi 只读白名单不在目录里: {sorted(PI_READ_TOOLS - catalog)}"


def test_every_tool_class_is_known() -> None:
    """归类值必须落在已知类别里（新增类别要同时更新 TTL 映射）。"""
    assert set(TOOL_CLASSES.values()) <= {"health", "metrics", "business", "static"}
