"""Application services for collector CRUD and agent reporting."""
import shlex
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from sqlmodel import Session

from app.core.config import settings
from app.core.security import generate_collector_key, hash_collector_key
from app.models.collectors import Collector
from app.repositories.collectors import list_collectors_for_system
from app.repositories.systems import list_services_for_system
from app.schemas import CollectorConfig, CollectorCreate, CollectorCreated, CollectorOut, CollectorReport
from app.services.descriptors.builder import system_to_descriptor
from app.services.notifications.alerts import alert_if_needed
from app.services.systems.service import get_monitoring_config
from app.services.systems.service import require_system


def create_collector(session: Session, system_id: int, org_id: int, body: CollectorCreate) -> CollectorCreated:
    system = require_system(session, system_id, org_id)
    key = generate_collector_key()
    collector = Collector(
        org_id=org_id,
        system_id=system.id,
        name=body.name,
        token_hash=hash_collector_key(key),
    )
    session.add(collector)
    session.commit()
    session.refresh(collector)
    return CollectorCreated(
        id=collector.id,
        name=collector.name,
        system_id=system.id,
        collector_key=key,
    )


def list_collectors(session: Session, system_id: int, org_id: int) -> list[CollectorOut]:
    system = require_system(session, system_id, org_id)
    stale_after = get_monitoring_config(system).interval_seconds * 2
    now = datetime.now(timezone.utc)
    return [
        CollectorOut(
            id=collector.id,
            name=collector.name,
            system_id=collector.system_id,
            last_seen=collector.last_seen.isoformat() if collector.last_seen else None,
            online=bool(collector.last_seen and (now - collector.last_seen.replace(tzinfo=timezone.utc)).total_seconds() < stale_after),
        )
        for collector in list_collectors_for_system(session, system.id)
    ]


def get_collector_config(session: Session, collector: Collector) -> CollectorConfig:
    system = require_system(session, collector.system_id, collector.org_id)
    # 远程系统的草稿服务也要下发，采集器连接后才能替平台完成首次测试。
    services = list_services_for_system(session, system.id)
    descriptor = system_to_descriptor(system, services)
    return CollectorConfig(
        system_id=system.id,
        name=system.name,
        local=system.local,
        infra=descriptor.get("infra", {}),
        services=descriptor["services"],
    )


def record_collector_report(session: Session, collector: Collector, body: CollectorReport) -> dict:
    now = datetime.now(timezone.utc)
    system = require_system(session, collector.system_id, collector.org_id)
    registered_services = list_services_for_system(session, system.id)
    if registered_services:
        enabled_names = {service.name for service in registered_services if service.enabled}
        results = [service.model_dump() for service in body.services if service.name in enabled_names]
    else:
        # 兼容早期只创建采集器、尚未登记服务的远程系统。
        results = [service.model_dump() for service in body.services]
    # Remote collectors report more frequently than the scheduler. Respect
    # the same per-system switch so pausing monitoring behaves identically
    # for local and remote systems.
    if get_monitoring_config(system).enabled:
        alert_if_needed(system, results, session)
    system.last_health = {"services": results}
    system.last_report_at = now
    collector.last_seen = now
    session.add(system)
    session.add(collector)
    session.commit()
    return {"ok": True, "received": len(body.services)}


def delete_collector(
    session: Session,
    collector_id: int,
    org_id: int,
    *,
    system_id: int | None = None,
) -> None:
    collector = session.get(Collector, collector_id)
    if (
        not collector
        or collector.org_id != org_id
        or (system_id is not None and collector.system_id != system_id)
    ):
        from fastapi import HTTPException, status
        raise HTTPException(status.HTTP_404_NOT_FOUND, "采集器不存在")
    session.delete(collector)
    session.commit()


def build_collector_bundle(
    session: Session,
    system_id: int,
    collector_id: int,
    org_id: int,
    collector_key: str,
    platform_url: str,
) -> bytes:
    system = require_system(session, system_id, org_id)
    collector = session.get(Collector, collector_id)
    if not collector or collector.system_id != system.id or collector.org_id != org_id:
        raise LookupError("采集器不存在")
    if not collector_key or hash_collector_key(collector_key) != collector.token_hash:
        raise ValueError("采集器密钥不正确，请使用刚创建时显示的明文密钥")

    root = Path(settings.repo_root)
    files = (
        "collector/run.py",
        "collector/ws_client.py",
        "collector/requirements.txt",
        "collector/Dockerfile",
        "collector/README.md",
        "shared/connectors/__init__.py",
        "shared/connectors/base.py",
        "shared/connectors/docker_logs.py",
        "shared/connectors/http.py",
        "shared/connectors/k8s.py",
        "shared/connectors/local.py",
        "shared/connectors/prometheus.py",
        "shared/connectors/ssh.py",
        "shared/connectors/tcp.py",
    )
    missing = [path for path in files if not (root / path).is_file()]
    if missing:
        raise RuntimeError(f"采集器文件缺失：{', '.join(missing)}")

    run_script = (
        "#!/bin/sh\n"
        "set -eu\n"
        f"PLATFORM_URL={shlex.quote(platform_url.rstrip('/'))} \\\n"
        f"COLLECTOR_KEY={shlex.quote(collector_key)} \\\n"
        "COLLECTOR_INTERVAL=30 python collector/run.py\n"
    )
    bat_script = (
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        f"set \"PLATFORM_URL={platform_url.rstrip('/')}\" \r\n"
        f"set \"COLLECTOR_KEY={collector_key}\" \r\n"
        "set \"COLLECTOR_INTERVAL=30\" \r\n"
        "python collector\\run.py\r\n"
        "if errorlevel 1 pause\r\n"
    )
    ps1_script = (
        "$ErrorActionPreference = 'Stop'\r\n"
        "Set-Location $PSScriptRoot\r\n"
        f"$env:PLATFORM_URL = '{platform_url.rstrip('/')}'\r\n"
        f"$env:COLLECTOR_KEY = '{collector_key}'\r\n"
        "$env:COLLECTOR_INTERVAL = '30'\r\n"
        "python collector/run.py\r\n"
        "if ($LASTEXITCODE -ne 0) { Read-Host '按 Enter 退出' }\r\n"
    )
    install_bat = (
        "@echo off\r\n"
        "cd /d \"%~dp0\"\r\n"
        "echo === AIOps 采集器 Windows 一键安装 ===\r\n"
        "python --version >nul 2>&1 || (\r\n"
        "  echo 未找到 Python，请先安装 Python 3.12+ 并勾选 Add to PATH\r\n"
        "  pause & exit /b 1\r\n"
        ")\r\n"
        "python -m pip install -r collector\\requirements.txt\r\n"
        "if errorlevel 1 pause & exit /b 1\r\n"
        "call run_collector.bat\r\n"
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as bundle:
        for relative in files:
            bundle.write(root / relative, relative)
        bundle.writestr("run_collector.sh", run_script)
        bundle.writestr("run_collector.bat", bat_script)
        bundle.writestr("run_collector.ps1", ps1_script)
        bundle.writestr("install_and_run_windows.bat", install_bat)
        bundle.writestr(
            "BUNDLE_README.txt",
            "=== AIOps 采集器安装包 ===\n\n"
            "【Windows 推荐】\n"
            "1. 解压到任意目录\n"
            "2. 双击 install_and_run_windows.bat（会自动安装依赖并启动）\n"
            "   或在 PowerShell 中执行：.\\run_collector.ps1\n"
            "   注意：不要用 Linux 的 PLATFORM_URL=xxx python ... 写法，PowerShell 不支持。\n\n"
            "【Linux / macOS】\n"
            "1. python -m pip install -r collector/requirements.txt\n"
            "2. sh run_collector.sh\n\n"
            "【Docker】\n"
            "见 collector/README.md\n\n"
            "服务地址必须能从采集器所在网络访问；127.0.0.1 指采集器所在主机，不是平台主机。\n",
        )
    return output.getvalue()
