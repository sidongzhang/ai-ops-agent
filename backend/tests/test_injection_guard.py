"""提示注入防护：不可信内容包裹 + 行动意图闸门。"""
from app.agent.diagnostics.tools import (
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    _RUN_HINTS,
    _SEND_HINTS,
    _untrusted,
    _wants,
)


def test_untrusted_wraps_nonempty_string():
    @_untrusted
    def fake():
        return "log line with data"

    out = fake()
    assert out.startswith(UNTRUSTED_OPEN)
    assert out.rstrip().endswith(UNTRUSTED_CLOSE)
    assert "log line with data" in out


def test_untrusted_leaves_empty_untouched():
    @_untrusted
    def fake():
        return "   "

    assert fake().strip() == ""


def test_send_intent_detection():
    assert _wants("把 result_x.fits 发给我下载", _SEND_HINTS)
    assert _wants("download the file", _SEND_HINTS)
    assert not _wants("ALGP 现在整体健康吗？", _SEND_HINTS)


def test_run_intent_detection():
    assert _wants("分析这个文件并生成报告", _RUN_HINTS)
    assert _wants("帮我统计一下字段", _RUN_HINTS)
    assert not _wants("Kafka 有没有积压？", _RUN_HINTS)
