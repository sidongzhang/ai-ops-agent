"""控制面 FastAPI 入口。"""
import logging
import signal
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agent.workflows.runner import init_persistent_checkpointer, shutdown_checkpointer
from .api import ROUTERS
from .core.config import settings
from .core.database import init_db

# uvicorn 会先配置自己的日志，root logger 上已有 handler 时 basicConfig 是空操作，
# 导致应用层 log.info（诊断耗时、token、缓存命中、工具链路）全部被吞掉。
# 这里直接给 app 命名空间挂 handler，不与 uvicorn 抢配置，也避免重复输出。
_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
_LOG_LEVEL = getattr(logging, settings.log_level.upper(), logging.INFO)
log = logging.getLogger(__name__)


def _setup_app_logging() -> None:
    """给 `app.*` 命名空间挂 handler。

    必须在 **uvicorn 配置完日志之后** 也要再调用一次：uvicorn 用 `dictConfig` 且默认
    `disable_existing_loggers=True`，会把启动前就存在的 `app` logger 置为 disabled，
    导致应用侧日志（诊断耗时、token、缓存命中、工具链路、成本）全部消失。
    这里显式解除 disabled，幂等，不抢 uvicorn 的配置。
    """
    app_logger = logging.getLogger("app")
    app_logger.disabled = False
    app_logger.setLevel(_LOG_LEVEL)
    if app_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    app_logger.addHandler(handler)
    app_logger.propagate = False


_setup_app_logging()


def _install_shutdown_signal_handlers() -> None:
    """在 uvicorn 之前挂上链式信号处理器：收到 SIGTERM/SIGINT 时**立刻**取消在跑诊断。

    为什么不能只靠 lifespan 的 finally：uvicorn 的关停顺序是"停止接收 → **等 BackgroundTask
    跑完** → 关闭 lifespan"。而我们要取消的正是它在等的那个 BackgroundTask —— 等它自己结束
    再取消就没意义了（实测：SIGTERM 后诊断还会继续跑 30s+ 才被处理）。

    做法：保留 uvicorn 原有的 handler，先在我们这里触发取消（放后台线程，信号处理器里不能阻塞），
    再调用原 handler 让 uvicorn 正常走关停流程。
    """
    from .services.diagnostics import cancel_bus

    def _make_handler(previous):
        def _handler(signum, frame):
            # 先让 uvicorn 置退出标志（它自己的处理器），再同步做**有界**清理。
            # 不另开线程：守护线程会被解释器直接掐掉；非守护线程在 uvicorn 的退出路径上
            # 也不保证被等待（实测进程 1s 就退了，终态没写进去）。同步做最多阻塞 grace 秒，
            # 对 SIGTERM 是可接受代价，换来的是"报告绝不停留在 running"的确定性。
            if callable(previous):
                previous(signum, frame)
            try:
                from .services.diagnostics import cancel_bus
                from .services.diagnostics.shutdown import shutdown_sync

                cancel_bus.mark_shutting_down()
                log.info("[shutdown] 收到信号 %s，同步取消在跑诊断（最多等待 5s 落库）", signum)
                shutdown_sync(grace_seconds=5.0)
            except Exception as exc:  # noqa: BLE001 - 信号处理器内绝不抛
                log.warning("[shutdown] 关停清理失败: %s", exc)

        return _handler

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous = signal.getsignal(sig)
            signal.signal(sig, _make_handler(previous))
        except Exception:  # noqa: BLE001 - 非主线程等场景忽略
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # uvicorn 已用 dictConfig 配置过日志，这里再挂一次确保 app.* 不被禁用（见函数注释）。
    _setup_app_logging()
    _install_shutdown_signal_handlers()
    init_db()
    # 收尸：上次进程被硬杀/关停超时留下的 running 报告，启动时收成终态（可恢复性如实标注）
    from .services.diagnostics.reaper import reap_stale_diagnoses

    reap_stale_diagnoses(minutes=1)
    # 审批工作流 checkpointer：PG 环境下持久化（重启不丢挂起的审批），失败降级内存版
    await init_persistent_checkpointer()
    try:
        yield
    finally:
        # 关停：先拒绝新诊断，再主动取消在跑诊断、有界等待它们把可恢复历史落库。
        # （硬杀 SIGKILL / 宽限期耗尽 → 由 reaper 标 failed 且 resumable=false，不谎称可恢复）
        from .services.diagnostics.shutdown import graceful_shutdown_diagnoses

        await graceful_shutdown_diagnoses()
        await shutdown_checkpointer()


app = FastAPI(
    title="智能运维平台 · 控制面",
    description="多租户 AIOps 控制面：注册系统 → 连接器探活 → AI 诊断",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in ROUTERS:
    app.include_router(router)


@app.get("/healthz", tags=["meta"])
def healthz():
    return {"status": "ok"}
