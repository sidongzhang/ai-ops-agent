"""审计日志保留期：只删过期行，<=0 关闭。"""
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, SQLModel, create_engine, select

from app.models.audit import AuditLog
from app.services.audit import purge_old_audit_logs


def _engine():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return engine


def test_purges_only_rows_older_than_retention() -> None:
    engine = _engine()
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(AuditLog(org_id=1, event_type="old", created_at=now - timedelta(days=200)))
        session.add(AuditLog(org_id=1, event_type="fresh", created_at=now))
        session.commit()

        removed = purge_old_audit_logs(session, retention_days=180)

        assert removed == 1
        remaining = session.exec(select(AuditLog)).all()
        assert [row.event_type for row in remaining] == ["fresh"]


def test_retention_zero_disables_purge() -> None:
    engine = _engine()
    with Session(engine) as session:
        session.add(
            AuditLog(
                org_id=1,
                event_type="ancient",
                created_at=datetime.now(timezone.utc) - timedelta(days=9999),
            )
        )
        session.commit()

        assert purge_old_audit_logs(session, retention_days=0) == 0
        assert session.exec(select(AuditLog)).all()
