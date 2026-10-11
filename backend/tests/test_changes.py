"""变更事件：登记、按时间窗查询、生成诊断用的「最近变更」上下文。"""
import tempfile
import unittest
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.auth import Org
from app.models.systems import MonitoredSystem
from app.services.changes import list_changes, recent_changes_text, record_change


class ChangeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self._tmp.name) / 'test.db'}")
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(Org(name="测试组织"))
            session.commit()
            session.add(MonitoredSystem(org_id=1, key="algp", name="algp"))
            session.commit()
            self.system = session.exec(select(MonitoredSystem)).first()
            self.system_id = self.system.id

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_record_and_list(self) -> None:
        with Session(self.engine) as session:
            event = record_change(
                session, org_id=1, system_id=self.system_id,
                title="发布 algp-web v1.2.3", service="Spring Boot", kind="deploy",
            )
            self.assertIsNotNone(event.id)
            rows = list_changes(session, self.system_id, 1, hours=24)
            self.assertEqual(len(rows), 1)

    def test_recent_changes_text_contains_change_and_hint(self) -> None:
        with Session(self.engine) as session:
            record_change(session, org_id=1, system_id=self.system_id, title="发布 v1", kind="deploy")
            text = recent_changes_text(session, self.system_id, 1, hours=24)
            self.assertIn("发布 v1", text)
            self.assertIn("是不是刚发版导致", text)

    def test_no_changes_returns_empty(self) -> None:
        with Session(self.engine) as session:
            self.assertEqual(recent_changes_text(session, self.system_id, 1, hours=24), "")
