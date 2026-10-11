"""变更事件：记录与查询，并为诊断提供「最近变更」上下文（判断"是不是刚发版导致的"）。"""
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, desc, select

from app.models.changes import ChangeEvent
from app.services.systems.service import require_system


def record_change(
    session: Session,
    *,
    org_id: int,
    system_id: int,
    title: str,
    service: str = "",
    kind: str = "change",
    description: str = "",
    actor: str = "",
    external_id: str = "",
    occurred_at: datetime | None = None,
    details: dict | None = None,
) -> ChangeEvent:
    require_system(session, system_id, org_id)
    event = ChangeEvent(
        org_id=org_id,
        system_id=system_id,
        title=(str(title or "").strip()[:256] or "变更"),
        service=str(service or "").strip()[:128],
        kind=(str(kind or "change").strip()[:32] or "change"),
        description=str(description or ""),
        actor=str(actor or "")[:128],
        external_id=str(external_id or "")[:128],
        details=dict(details or {}),
        occurred_at=occurred_at or datetime.now(timezone.utc),
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def list_changes(session: Session, system_id: int, org_id: int, *, hours: int = 72, limit: int = 50) -> list[ChangeEvent]:
    require_system(session, system_id, org_id)
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, min(int(hours or 72), 24 * 30)))
    rows = session.exec(
        select(ChangeEvent)
        .where(ChangeEvent.system_id == system_id, ChangeEvent.occurred_at >= since)
        .order_by(desc(ChangeEvent.occurred_at))
        .limit(max(1, min(int(limit or 50), 200)))
    ).all()
    return list(rows)


def recent_changes_text(session: Session, system_id: int, org_id: int, *, hours: int = 24, limit: int = 8) -> str:
    """生成注入 prompt 的「最近变更」块；无变更/失败返回空串。"""
    try:
        rows = list_changes(session, system_id, org_id, hours=hours, limit=limit)
    except Exception:  # noqa: BLE001 - 变更上下文获取失败绝不能影响诊断
        return ""
    if not rows:
        return ""
    lines = [f"## 最近 {int(hours)} 小时内的变更（判断「是不是刚发版导致的」时参照）"]
    for row in rows:
        ts = row.occurred_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if row.occurred_at else "-"
        target = f" @{row.service}" if row.service else ""
        desc = f"；{row.description[:120]}" if row.description else ""
        lines.append(f"- {ts}｜{row.kind}{target}：{row.title}（{row.actor or '未知操作人'}）{desc}")
    lines.append("若问题时间与上述变更接近，请优先怀疑该变更，并在结论中讲清时间关系。")
    return "\n".join(lines)
