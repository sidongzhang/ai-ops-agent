"""连接器工厂：未知类型必须显式失败，不能静默回退到 LocalConnector。

拼错一个字母（例如 `connector: "tcpp"`）以前会拿 127.0.0.1 去探活，
把"本机端口不通"写成诊断结论——比报错危险得多。
"""
import pytest

from app.services.descriptors.runtime import get_connector  # 触发 shared 路径注入
from connectors import LocalConnector, TcpConnector


def test_known_connector_is_built() -> None:
    connector = get_connector(
        {"connector": "tcp", "host": "127.0.0.1", "port": 1},
        {"id": 1},
    )
    assert isinstance(connector, TcpConnector)


def test_missing_connector_defaults_to_local() -> None:
    connector = get_connector({"name": "svc"}, {"id": 1})
    assert isinstance(connector, LocalConnector)


def test_unknown_connector_raises_with_available_types() -> None:
    with pytest.raises(ValueError, match="未知的连接器类型") as excinfo:
        get_connector({"connector": "tcpp"}, {"id": 1})
    message = str(excinfo.value)
    assert "tcpp" in message
    assert "tcp" in message  # 提示可用类型
