"""控制面 FastAPI 入口。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db
from .routers import agent, auth, collector_agent, collector_exec, collectors, monitoring, systems
from .routers import ws as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="智能运维平台 · 控制面",
    description="多租户 AIOps 控制面：注册系统 → 连接器探活 → AI 诊断",
    version="0.1.0",
    lifespan=lifespan,
)

# 开发期放开 CORS，方便 Vue 前端本地联调；生产收敛到具体域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(systems.router)
app.include_router(monitoring.router)
app.include_router(agent.router)
app.include_router(collectors.router)
app.include_router(collector_agent.router)
app.include_router(collector_exec.router)
app.include_router(ws_router.router)


@app.get("/healthz", tags=["meta"])
def healthz():
    return {"status": "ok"}
