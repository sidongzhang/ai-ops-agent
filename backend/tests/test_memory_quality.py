"""记忆质量治理：有效性权重、近重复阈值、结论/建议抽取。"""
from app.services.knowledge import memory as mem


def test_validity_weight_excludes_failed():
    assert mem.VALIDITY_WEIGHT["failed"] == 0.0            # 无效处置直接排除
    assert mem.VALIDITY_WEIGHT["confirmed_success"] == 1.0  # 回查成功权重最高


def test_dedup_and_decay_constants_sane():
    assert 0.0 < mem.DEDUP_SIMILARITY <= 1.0
    assert mem.DECAY_HALF_LIFE_DAYS > 0
    assert 0.0 < mem.DECAY_FLOOR < 1.0


def test_extract_conclusion_and_remedy():
    answer = "**结论**：MySQL 连接池耗尽导致服务超时。\n**建议**：- 扩大 max_connections\n- 重连验证"
    assert "连接池耗尽" in mem._extract_conclusion(answer)
    assert "max_connections" in mem._extract_remedy(answer)
