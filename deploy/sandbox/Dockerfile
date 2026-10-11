# 代码沙箱镜像：一次性容器里跑 agent 生成的 Python（含科学栈）。
# 构建：docker build -t aiops-sandbox:latest deploy/sandbox
FROM python:3.12-slim

ENV MPLBACKEND=Agg \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir numpy matplotlib

WORKDIR /work
