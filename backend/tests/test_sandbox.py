"""Python 沙箱：执行、产物收集、输入注入、超时与错误处理。"""
from app.services.diagnostics.sandbox import run_python


def test_captures_output_and_output_files():
    result = run_python("print('hello')\nopen('report.txt','w').write('done')\n")
    assert result["ok"] is True
    assert "hello" in result["stdout"]
    assert result["outputs"].get("report.txt") == b"done"


def test_input_files_are_staged_and_excluded_from_outputs():
    result = run_python("data=open('in.txt').read().upper()\nprint(data)\n", {"in.txt": b"abc"})
    assert "ABC" in result["stdout"]
    assert "in.txt" not in result["outputs"]  # 输入文件不算产物


def test_timeout_is_reported():
    result = run_python("while True:\n    pass\n", timeout=2)
    assert result["ok"] is False
    assert "超时" in result["stderr"]


def test_exception_returns_stderr():
    result = run_python("raise ValueError('boom')\n")
    assert result["ok"] is False
    assert "boom" in result["stderr"]
