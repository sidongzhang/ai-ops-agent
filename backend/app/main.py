"""控制面 FastAPI 入口。"""
import logging
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


def _setup_app_logging() -> None:
    app_logger = logging.getLogger("app")
    app_logger.setLevel(_LOG_LEVEL)
    if app_logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    app_logger.addHandler(handler)
    app_logger.propagate = False


_setup_app_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # 审批工作流 checkpointer：PG 环境下持久化（重启不丢挂起的审批），失败降级内存版
    await init_persistent_checkpointer()
    try:
        yield
    finally:
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
