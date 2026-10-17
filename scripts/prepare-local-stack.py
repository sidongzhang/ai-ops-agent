#!/usr/bin/env python3
"""Refresh the local stack's locked dependencies, static UI and pre-migration backup."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.dev-stack'

def run(command, cwd=ROOT):
    subprocess.run(command, cwd=cwd, check=True)


def main():
    STATE.mkdir(exist_ok=True)
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('uv is required to sync the locked backend dependencies')
    print('同步后端锁定依赖…', flush=True)
    run([uv, 'sync', '--directory', str(ROOT/'backend'), '--frozen'])
    frontend = ROOT/'frontend'
    digest = hashlib.sha256((frontend/'package.json').read_bytes() + (frontend/'package-lock.json').read_bytes()).hexdigest()
    stamp = STATE/'frontend-deps.sha256'
    if not stamp.exists() or stamp.read_text() != digest or not (frontend/'node_modules/dompurify/package.json').exists() or not (frontend/'node_modules/.bin/vite').exists():
        print('同步控制台锁定依赖…', flush=True)
        run(['npm', 'ci', '--ignore-scripts', '--no-fund'], frontend)
        stamp.write_text(digest)
    source = hashlib.sha256()
    files = sorted([*(frontend/'src').rglob('*'), *(frontend/'public').rglob('*'),
        frontend/'index.html', frontend/'vite.config.js', frontend/'package.json', frontend/'package-lock.json'])
    for path in files:
        if path.is_file():
            source.update(str(path.relative_to(frontend)).encode()); source.update(path.read_bytes())
    build_stamp = STATE/'frontend-build.sha256'
    target = frontend/'dist-prod'
    if not build_stamp.exists() or build_stamp.read_text() != source.hexdigest() or not (target/'index.html').exists():
        print('构建最新控制台页面（成功后替换 dist-prod）…', flush=True)
        staging = Path(tempfile.mkdtemp(prefix='frontend-build-', dir=STATE))
        try:
            run(['npm', 'run', 'build', '--', '--outDir', str(staging)], frontend)
            if not (staging/'index.html').exists():
                raise RuntimeError('Build did not produce index.html')
            previous = STATE/('frontend-previous-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
            if target.exists(): target.rename(previous)
            try: staging.rename(target)
            except Exception:
                if previous.exists() and not target.exists(): previous.rename(target)
                raise
            build_stamp.write_text(source.hexdigest())
        finally:
            if staging.exists(): shutil.rmtree(staging)
    else:
        print('控制台页面已是当前源码版本。', flush=True)

    sys.path.insert(0, str(ROOT/'backend'))
    from app.core.config import settings
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import make_url
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(ROOT/'backend/alembic.ini'))
    cfg.set_main_option('script_location', str(ROOT/'backend/migrations'))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    url = make_url(settings.database_url)
    if url.drivername.startswith('postgresql'):
        engine = create_engine(url)
        try:
            with engine.connect() as conn:
                revision = conn.execute(text('SELECT version_num FROM alembic_version')).scalar() if inspect(conn).has_table('alembic_version') else None
            if revision != head:
                if url.host not in ('localhost','127.0.0.1') or url.database != 'controlplane':
                    raise RuntimeError('Automatic backup is configured only for the local controlplane database')
                folder = STATE/'backups'; folder.mkdir(mode=0o700, exist_ok=True); folder.chmod(0o700)
                backup = folder/('controlplane-before-'+str(head)+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.dump')
                print('数据库版本待升级；先保存本地备份…', flush=True)
                fd = os.open(backup, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        subprocess.run(['docker','exec','deploy-platform-db-1','pg_dump','-U',url.username or 'ops','-d',url.database,'-Fc'], stdout=stream, check=True)
                    if not backup.stat().st_size: raise RuntimeError('Empty database backup')
                    print('数据库备份完成；后端启动时执行增量迁移。', flush=True)
                except Exception:
                    backup.unlink(missing_ok=True)
                    raise
            else:
                print('数据库已是最新迁移版本。', flush=True)
        finally: engine.dispose()
    else:
        print('SQLite 开发模式：由后端启动时适配结构。', flush=True)
    print('本地启动准备完成。', flush=True)


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(f'启动准备失败：{type(exc).__name__}；未继续启动应用，请查看准备日志。', file=sys.stderr)
        sys.exit(1)
