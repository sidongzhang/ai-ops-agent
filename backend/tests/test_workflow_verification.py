"""执行后验证的文案：区分「已恢复」与「执行完成但未恢复」并给建议。"""
from app.services.workflows.execution import verification_summary


def test_recovered_message():
    text = verification_summary(True, "Spring Boot: HTTP 200")
    assert "已恢复" in text
    assert "HTTP 200" in text


def test_unrecovered_has_next_step():
    text = verification_summary(False, "Spring Boot: 连接被拒绝")
    assert "未恢复" in text
    assert "建议" in text
    assert "重新诊断" in text


def test_empty_detail_placeholder():
    assert "（无回查详情）" in verification_summary(False, "")
