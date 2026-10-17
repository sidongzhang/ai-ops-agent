"""回归：同步上下文里的远程采集器命令必须走 send_command_sync。

历史缺陷：data_analysis（业务数据只读查询）与 metrics（采集器侧 Prometheus/Redis/健康）
在同步线程里用 asyncio.run(manager.send_command(...)) 下发命令。asyncio.run 会新建
一个临时事件循环并把结果 Future 建在那个循环上，而采集器回包由平台主循环的
resolve() 投递，永远唤不醒临时循环的 Future → 每次必然卡满 30s 超时。
正确写法是 manager.send_command_sync()（回到主循环等待）。

这些用例把 send_command（async）设为“一调用就失败”，只允许 send_command_sync，
从而钉死这个语义，防止回退。
"""
import unittest
from types import SimpleNamespace

from app.services import data_analysis
from app.services.monitoring import metrics as metrics_mod


class _FakeCollector:
    id = 8


class _FakeManager:
    def __init__(self):
        self.sync_calls: list[tuple] = []

    def is_connected(self, collector_id: int) -> bool:
        return True

    def send_command_sync(self, collector_id, cmd, args=None, timeout=30.0):
        self.sync_calls.append((collector_id, cmd))
        return {"ok": True, "result": [{"n": 1}]}

    async def send_command(self, *args, **kwargs):  # pragma: no cover - 明确禁止
        raise AssertionError("同步上下文不应调用 async send_command")


class RemoteCommandSyncTests(unittest.TestCase):
    def test_readonly_executor_uses_sync_send(self) -> None:
        fake = _FakeManager()
        original_selector = data_analysis.select_online_collector
        original_manager = data_analysis.manager
        data_analysis.select_online_collector = lambda session, system_id: _FakeCollector()
        data_analysis.manager = fake
        try:
            executor = data_analysis._remote_query_executor(
                None, SimpleNamespace(id=1, local=False)
            )
            self.assertIsNotNone(executor)
            rows = executor("SELECT 1", {}, 60)
        finally:
            data_analysis.select_online_collector = original_selector
            data_analysis.manager = original_manager

        self.assertEqual(rows, [{"n": 1}])
        self.assertEqual(fake.sync_calls, [(8, "run_readonly_query")])

    def test_metrics_reader_uses_sync_send(self) -> None:
        fake = _FakeManager()
        original_selector = metrics_mod.select_online_collector
        original_manager = metrics_mod.manager
        metrics_mod.select_online_collector = lambda session, system_id: _FakeCollector()
        metrics_mod.manager = fake
        try:
            descriptor = {"services": [{"connector": "prometheus", "name": "Prom"}]}
            remote_query, _remote_range, _redis, _health = metrics_mod._remote_metric_readers(
                None, SimpleNamespace(id=1, local=False), descriptor
            )
            self.assertIsNotNone(remote_query)
            result = remote_query("up")
        finally:
            metrics_mod.select_online_collector = original_selector
            metrics_mod.manager = original_manager

        self.assertEqual(result, [{"n": 1}])
        self.assertEqual(fake.sync_calls, [(8, "query_prometheus")])


if __name__ == "__main__":
    unittest.main()
