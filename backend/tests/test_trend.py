"""趋势巡检：序列分析（趋势外推/容量逼近）+ 预警消息生成与自动解除。"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.auth import Org
from app.models.messages import SystemMessage
from app.models.monitoring import MetricSample
from app.models.systems import MonitoredSystem
from app.services.monitoring.metrics import PrometheusMetrics, SystemMetrics
from app.services.monitoring import trend


def _series(values, *, hours_apart=1.0):
    now = datetime.now(timezone.utc)
    return [
        (now - timedelta(hours=hours_apart * (len(values) - i - 1)), float(v))
        for i, v in enumerate(values)
    ]


class AnalyzeSeriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = trend.metric_specs()["prom_mem_used_pct"]

    def test_flat_series_no_warning(self) -> None:
        self.assertIsNone(trend.analyze_series(_series([50, 50, 50, 50]), self.spec))

    def test_rising_series_projects_eta(self) -> None:
        result = trend.analyze_series(_series([60, 64, 68, 72]), self.spec)
        self.assertIsNotNone(result)
        self.assertEqual(result["level"], "warning")
        self.assertLess(result["eta_hours"], 48)
        self.assertIn("触顶", result["reason"])

    def test_near_threshold_warns_even_without_trend(self) -> None:
        result = trend.analyze_series(_series([88, 88, 88, 88]), self.spec)
        self.assertIsNotNone(result)
        self.assertIn("上限的", result["reason"])

    def test_over_threshold_is_critical(self) -> None:
        result = trend.analyze_series(_series([97, 97, 97, 97]), self.spec)
        self.assertEqual(result["level"], "critical")

    def test_insufficient_samples_only_capacity_check(self) -> None:
        self.assertIsNone(trend.analyze_series(_series([60, 64]), self.spec))
        near = trend.analyze_series(_series([90]), self.spec)
        self.assertIsNotNone(near)

    def test_slow_rise_beyond_horizon_no_warning(self) -> None:
        # 每小时 +0.05%（24 小时才 +1.2），预测远超 48h 窗口 → 不打扰
        self.assertIsNone(trend.analyze_series(_series([40, 40.05, 40.1, 40.15]), self.spec))


class TrendPatrolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._tmp.name) / 'test.db'}")
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(Org(name="测试组织"))
            session.commit()
            session.add(MonitoredSystem(org_id=1, key="algp", name="algp", local=True))
            session.commit()
            self.system_id = session.exec(select(MonitoredSystem)).first().id

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, session: Session, values) -> None:
        for at, value in _series(values):
            session.add(
                MetricSample(
                    org_id=1, system_id=self.system_id, metric="prom_mem_used_pct", value=value, observed_at=at
                )
            )
        session.commit()

    @staticmethod
    def _metrics(mem_used_pct: float) -> SystemMetrics:
        return SystemMetrics(
            prometheus=PrometheusMetrics(available=True, mem_used_pct=mem_used_pct, cpu_usage_pct=12.0),
        )

    def test_patrol_creates_warning_and_auto_resolves(self) -> None:
        with patch("app.services.notifications.alerts.deliver_message_notifications") as deliver:
            with Session(self.engine) as session:
                self._seed(session, [60, 64, 68])
                system = session.get(MonitoredSystem, self.system_id)

                result = trend.run_trend_patrol(session, system, metrics=self._metrics(72.0), force=True)
                self.assertEqual(result["warnings"], 1)
                self.assertEqual(result["created"], 1)
                deliver.assert_called_once()

                messages = session.exec(
                    select(SystemMessage).where(SystemMessage.message_type == "warning")
                ).all()
                self.assertEqual(len(messages), 1)
                self.assertEqual((messages[0].related or {})["metric"], "prom_mem_used_pct")
                self.assertEqual(messages[0].status, "unread")

                # 第二轮回落到安全区 → 自动解除，不重复打扰
                system = session.get(MonitoredSystem, self.system_id)
                result2 = trend.run_trend_patrol(session, system, metrics=self._metrics(30.0), force=True)
                self.assertEqual(result2["warnings"], 0)
                self.assertEqual(result2["resolved"], 1)
                session.refresh(messages[0])
                self.assertEqual(messages[0].status, "resolved")

    def test_patrol_refreshes_existing_warning_without_renotify(self) -> None:
        with patch("app.services.notifications.alerts.deliver_message_notifications") as deliver:
            with Session(self.engine) as session:
                system = session.get(MonitoredSystem, self.system_id)
                trend.run_trend_patrol(session, system, metrics=self._metrics(90.0), force=True)
                self.assertEqual(deliver.call_count, 1)
                system = session.get(MonitoredSystem, self.system_id)
                result = trend.run_trend_patrol(session, system, metrics=self._metrics(93.0), force=True)
                self.assertEqual(result["updated"], 1)
                self.assertEqual(result["created"], 0)
                self.assertEqual(deliver.call_count, 1)  # 不重复外发

    def test_patrol_skips_when_not_due(self) -> None:
        with Session(self.engine) as session:
            self._seed(session, [10])
            system = session.get(MonitoredSystem, self.system_id)
            result = trend.run_trend_patrol(session, system, metrics=self._metrics(12.0))
            self.assertEqual(result.get("skipped"), "not_due")


if __name__ == "__main__":
    unittest.main()
