"""告警状态对齐回归测试。

背景：告警关闭此前依赖 `system.last_health` 的「上一轮→本轮」跃迁，而 last_health 有
多个写入方（采集器上报、定时巡检、外部系统上报、容器 OOM 巡检），任一写入方都会吞掉
跃迁，实际表现为：
  * 服务早已恢复，告警永久挂起（用户看到的「持续告警」）
  * 告警被误关后，故障不再产生新告警（故障静默）
现改为按当前状态对齐：恢复即关闭、异常且未被覆盖即重开。
"""

import tempfile
import unittest
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.messages import SystemMessage
from app.models.systems import MonitoredSystem
from app.services.notifications.alerts import reconcile_alerts


class AlertReconcileTests(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.engine = create_engine(
            f"sqlite:///{Path(tmpdir.name) / 'alerts.db'}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(self.engine)

    def _system(self, session: Session) -> MonitoredSystem:
        system = MonitoredSystem(org_id=1, key="algp", name="algp", local=False)
        session.add(system)
        session.commit()
        session.refresh(system)
        return system

    def _open_alert(self, session: Session, system: MonitoredSystem, services: list[str]) -> SystemMessage:
        message = SystemMessage(
            org_id=system.org_id,
            system_id=system.id,
            message_type="alert",
            severity="warning",
            title="系统异常",
            summary=f"异常服务：{'、'.join(services)}",
            related={"failed_services": services},
        )
        session.add(message)
        session.commit()
        session.refresh(message)
        return message

    def test_recovered_service_closes_stale_alert_without_transition(self) -> None:
        """服务已恢复 → 陈旧告警必须关闭（不依赖 last_health 跃迁）。"""
        with Session(self.engine) as session:
            system = self._system(session)
            message = self._open_alert(session, system, ["Spring Boot"])
            # last_health 里已经没有任何异常（模拟被其他写入方覆盖过）
            system.last_health = {"services": [{"name": "Spring Boot", "ok": True}]}
            session.add(system)

            uncovered = reconcile_alerts(
                system,
                [{"name": "Spring Boot", "ok": True}],
                session,
            )
            session.commit()
            session.refresh(message)

            self.assertEqual(uncovered, [])
            self.assertEqual(message.status, "resolved")
            self.assertIn("已恢复", message.summary)

    def test_failing_service_without_open_alert_is_reported(self) -> None:
        """当前异常但没有任何未解决告警覆盖 → 必须报告出来（修复故障静默）。"""
        with Session(self.engine) as session:
            system = self._system(session)
            # 历史上 MinIO 的告警被误关：现在没有任何未解决告警
            uncovered = reconcile_alerts(
                system,
                [{"name": "MinIO 对象存储", "ok": False},
                 {"name": "Spring Boot", "ok": True}],
                session,
            )
            self.assertEqual(uncovered, ["MinIO 对象存储"])

    def test_partially_recovered_alert_stays_open_and_covers_its_services(self) -> None:
        """一条告警含多个服务时，只有全部恢复才关闭；未恢复的仍算被覆盖。"""
        with Session(self.engine) as session:
            system = self._system(session)
            message = self._open_alert(session, system, ["DataFinder", "MinIO"])

            uncovered = reconcile_alerts(
                system,
                [{"name": "DataFinder", "ok": True}, {"name": "MinIO", "ok": False}],
                session,
            )
            session.commit()
            session.refresh(message)

            self.assertEqual(uncovered, [])            # MinIO 已被该告警覆盖
            self.assertEqual(message.status, "unread")  # 未全部恢复，保持打开

    def test_full_recovery_closes_multi_service_alert(self) -> None:
        with Session(self.engine) as session:
            system = self._system(session)
            message = self._open_alert(session, system, ["DataFinder", "MinIO"])

            uncovered = reconcile_alerts(
                system,
                [{"name": "DataFinder", "ok": True}, {"name": "MinIO", "ok": True}],
                session,
            )
            session.commit()
            session.refresh(message)

            self.assertEqual(uncovered, [])
            self.assertEqual(message.status, "resolved")

    def test_no_results_is_noop(self) -> None:
        with Session(self.engine) as session:
            system = self._system(session)
            message = self._open_alert(session, system, ["Redis"])
            self.assertEqual(reconcile_alerts(system, [], session), [])
            session.commit()
            session.refresh(message)
            self.assertEqual(message.status, "unread")


    def test_alert_closes_when_service_is_removed_from_monitoring(self) -> None:
        """服务被禁用/删除后不再出现在探测结果里 → 告警必须关闭，不能永久挂起。"""
        with Session(self.engine) as session:
            system = self._system(session)
            message = self._open_alert(session, system, ["MinIO 对象存储"])

            uncovered = reconcile_alerts(
                system,
                [{"name": "Spring Boot", "ok": True}],   # MinIO 已不在监控范围
                session,
            )
            session.commit()
            session.refresh(message)

            self.assertEqual(uncovered, [])
            self.assertEqual(message.status, "resolved")
