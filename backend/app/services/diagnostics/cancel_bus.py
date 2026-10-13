"""诊断取消总线：进程内注册表（快路径）+ DB 标志（真相来源）+ Redis 发布订阅（跨进程加速）。

三层职责不混淆：
  * 进程内注册表 `report_id → CancellationToken`：同进程零延迟；**未命中是正常情况**（跨进程/时序）。
  * DB `cancel_requested_at`：唯一真相来源，可审计、跨重启可见；不可用则取消直接报错（不假装成功）。
  * Redis pub/sub：只做"更快"，**丢消息不影响正确性**（轮询兜底）。

同时处理三种竞态：
  1. **取消早于 token 注册**：注册后立即自检一次 DB 标志；且官方语义保证"已取消的 token
     使该 run 不启动" → 零请求。
  2. **Redis 丢消息**：poller 是兜底，不依赖 pub/sub 正确性。
  3. **完成与取消同时发生**：终态写入一律用**条件更新**（`WHERE status='running'`），先到先赢；
     成功不会被覆盖成取消，取消也不会覆盖成功。
"""
import json
import logging
import os
import random
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field

from app.core.config import settings

log = logging.getLogger(__name__)

CANCEL_CHANNEL = "aiops:diagnosis:cancel"

# 本实例标识：写入报告行，便于"谁在跑"的可观测与排障
INSTANCE_ID = f"{socket.gethostname()[:24]}-{os.getpid()}-{uuid.uuid4().hex[:6]}"

# 执行相位（用于判定"用量是否完整"：取消发生在模型请求在途 → 该请求用量不可得）
PHASE_IDLE = "idle"
PHASE_MODEL = "model_request"
PHASE_TOOL = "tool_exec"


@dataclass
class CancelRegistration:
    report_id: int
    token: object                      # pydantic_ai.CancellationToken
    done: threading.Event = field(default_factory=threading.Event)
    phase: str = PHASE_IDLE
    cancelled_locally: bool = False


_registry: dict[int, CancelRegistration] = {}
_registry_lock = threading.Lock()
_shutting_down = False
_subscriber_started = False
_subscriber_lock = threading.Lock()


# ── 关停状态 ────────────────────────────────────────────────────────────
def mark_shutting_down() -> None:
    global _shutting_down
    _shutting_down = True


def is_shutting_down() -> bool:
    return _shutting_down


# ── 注册表 ──────────────────────────────────────────────────────────────
def register(report_id: int, token) -> CancelRegistration:
    """注册本轮运行的取消令牌；注册后**立即自检** DB 标志（堵"取消早于注册"）。"""
    reg = CancelRegistration(report_id=report_id, token=token)
    with _registry_lock:
        _registry[report_id] = reg
    ensure_subscriber()
    touch_heartbeat(report_id)          # 注册即打一次心跳（reaper 才能区分"活着的"与"失联的"）
    if is_cancel_requested(report_id):
        _cancel_token(reg)
    return reg


def unregister(report_id: int) -> None:
    with _registry_lock:
        reg = _registry.pop(report_id, None)
    if reg is not None:
        reg.done.set()


def get(report_id: int) -> CancelRegistration | None:
    with _registry_lock:
        return _registry.get(report_id)


def set_phase(report_id: int, phase: str) -> None:
    reg = get(report_id)
    if reg is not None:
        reg.phase = phase


def current_phase(report_id: int) -> str:
    reg = get(report_id)
    return reg.phase if reg is not None else PHASE_IDLE


def _cancel_token(reg: CancelRegistration) -> None:
    try:
        reg.token.cancel()          # 官方：幂等 + 线程安全
        reg.cancelled_locally = True
        log.info("[cancel] 已取消 report=%s（phase=%s）", reg.report_id, reg.phase)
    except Exception as exc:  # noqa: BLE001
        log.warning("[cancel] token.cancel 失败 report=%s: %s", reg.report_id, exc)


def request_local_cancel(report_id: int) -> bool:
    """本进程内取消（订阅到消息 / 轮询发现标志 / API 同进程直调）。"""
    reg = get(report_id)
    if reg is None:
        return False
    _cancel_token(reg)
    return True


def cancel_all_and_wait(grace_seconds: float | None = None) -> dict:
    """关停用：主动取消本进程所有在跑诊断，**有界等待**它们落库。

    返回 {"cancelled": n, "persisted": n, "unpersisted": [report_id...]}。
    """
    grace = settings.diagnosis_shutdown_grace_seconds if grace_seconds is None else grace_seconds
    with _registry_lock:
        regs = list(_registry.values())
    if not regs:
        return {"cancelled": 0, "persisted": 0, "unpersisted": []}

    for reg in regs:
        _cancel_token(reg)

    deadline = time.monotonic() + max(0.0, grace)
    unpersisted: list[int] = []
    persisted = 0
    for reg in regs:
        remaining = max(0.0, deadline - time.monotonic())
        if reg.done.wait(remaining):
            persisted += 1
        else:
            unpersisted.append(reg.report_id)
    if unpersisted:
        log.warning("[cancel] 关停宽限期 %.1fs 内未落库: %s", grace, unpersisted)
    return {"cancelled": len(regs), "persisted": persisted, "unpersisted": unpersisted}


# ── DB 标志（真相来源）──────────────────────────────────────────────────
def _session():
    """统一走 session 模块（单测可注入临时 SQLite，避免碰开发库）。"""
    from app.services.diagnostics import session as _session_module

    return _session_module.session()


def set_session_factory(factory) -> None:
    from app.services.diagnostics import session as _session_module

    _session_module.set_session_factory(factory)


def is_cancel_requested(report_id: int) -> bool:
    """读 DB 标志。读失败时**保守返回 False**（不误取消），错误交由调用方日志。"""
    from sqlmodel import select

    from app.models.diagnostics import DiagnosisReport

    try:
        with _session() as session:
            row = session.exec(
                select(DiagnosisReport.cancel_requested_at).where(DiagnosisReport.id == report_id)
            ).first()
        return row is not None
    except Exception as exc:  # noqa: BLE001
        log.warning("[cancel] 读取取消标志失败 report=%s: %s", report_id, exc)
        return False


def is_terminal(report_id: int) -> bool:
    from sqlmodel import select

    from app.models.diagnostics import DiagnosisReport

    try:
        with _session() as session:
            status = session.exec(
                select(DiagnosisReport.status).where(DiagnosisReport.id == report_id)
            ).first()
        return status not in (None, "running")
    except Exception:  # noqa: BLE001
        return False


def touch_heartbeat(report_id: int) -> None:
    """续期心跳（reaper 据此判定"是否还有活着的 worker 在推进"）。

    只更新仍为 running 的行；失败静默（心跳失败不该影响诊断）。
    """
    from datetime import datetime, timezone

    from sqlalchemy import text

    try:
        with _session() as session:
            session.execute(
                text(
                    "UPDATE diagnosis_reports SET runner_id = :rid, runner_heartbeat_at = :ts "
                    "WHERE id = :id AND status = 'running'"
                ),
                {"id": report_id, "rid": INSTANCE_ID, "ts": datetime.now(timezone.utc)},
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001
        log.debug("[cancel] 心跳续期失败 report=%s: %s", report_id, exc)


def set_cancel_requested(report_id: int) -> bool:
    """幂等置标志；返回是否**本次**写入成功（已置过或已终态 → False）。

    时间戳由 Python 传入（而非 SQL `now()`）：同时兼容 Postgres 与 SQLite。
    """
    from datetime import datetime, timezone

    from sqlalchemy import text

    try:
        with _session() as session:
            result = session.execute(
                text(
                    "UPDATE diagnosis_reports SET cancel_requested_at = :ts "
                    "WHERE id = :id AND cancel_requested_at IS NULL AND status = 'running'"
                ),
                {"id": report_id, "ts": datetime.now(timezone.utc)},
            )
            session.commit()
            return bool(result.rowcount)
    except Exception as exc:  # noqa: BLE001
        log.warning("[cancel] 写入取消标志失败 report=%s: %s", report_id, exc)
        raise


# ── Redis 加速通道（可丢）───────────────────────────────────────────────
def publish_cancel(report_id: int) -> None:
    try:
        import redis

        client = redis.Redis.from_url(settings.celery_broker_url, socket_connect_timeout=1, socket_timeout=1)
        client.publish(CANCEL_CHANNEL, json.dumps({"report_id": report_id}))
        client.close()
    except Exception as exc:  # noqa: BLE001 - 丢消息不影响正确性（有轮询兜底）
        log.debug("[cancel] 发布取消消息失败（不影响正确性）: %s", exc)


def ensure_subscriber() -> None:
    """进程级订阅线程（懒启动，只启动一次）。"""
    global _subscriber_started
    with _subscriber_lock:
        if _subscriber_started:
            return
        _subscriber_started = True
    threading.Thread(target=_subscribe_loop, name="diagnosis-cancel-subscriber", daemon=True).start()


def _subscribe_loop() -> None:
    import redis

    while True:
        try:
            client = redis.Redis.from_url(
                settings.celery_broker_url, socket_connect_timeout=2, socket_timeout=None
            )
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(CANCEL_CHANNEL)
            log.info("[cancel] 已订阅取消频道 %s", CANCEL_CHANNEL)
            for message in pubsub.listen():
                if not message or message.get("type") != "message":
                    continue
                try:
                    report_id = int(json.loads(message["data"]).get("report_id"))
                except Exception:  # noqa: BLE001
                    continue
                request_local_cancel(report_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("[cancel] 订阅中断，2s 后重连: %s", exc)
            time.sleep(2)


# ── 兜底轮询（正确性来源）──────────────────────────────────────────────
def start_poller(report_id: int, reg: CancelRegistration) -> threading.Thread:
    """启动兜底巡检线程：DB 标志一出现就取消 token；终态/超时则退出。

    单个循环只有一个出口：已取消 / 已落终态 / 超过单次运行上限（交 reaper）。
    """

    def _loop() -> None:
        interval = max(0.2, settings.diagnosis_cancel_poll_seconds)
        jitter = max(0.0, settings.diagnosis_cancel_poll_jitter)
        deadline = time.monotonic() + max(30, settings.diagnosis_max_run_seconds)
        failures = 0
        while not reg.done.is_set():
            time.sleep(interval + random.uniform(0, jitter))
            if reg.done.is_set():
                break
            try:
                if is_terminal(report_id):
                    break
                touch_heartbeat(report_id)      # 每轮续期：reaper 据此知道本 worker 还活着
                if is_cancel_requested(report_id):
                    _cancel_token(reg)
                    break
                failures = 0
            except Exception as exc:  # noqa: BLE001 - 单次异常不退出循环
                failures += 1
                log.warning("[cancel] 轮询异常(%s) report=%s: %s", failures, report_id, exc)
                if failures >= 3:            # 连续失败降级为低频，避免打爆 DB
                    interval = 30.0
            if time.monotonic() > deadline:
                log.warning("[cancel] report=%s 超过单次运行上限，停止巡检（交 reaper）", report_id)
                break

    thread = threading.Thread(target=_loop, name=f"diagnosis-cancel-poll-{report_id}", daemon=True)
    thread.start()
    return thread
