import sys
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel
from alembic import context

# 确保 backend/ 包在 import 路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.models import Org, User, MonitoredSystem, Collector, Service, ActionWorkflow  # noqa: E402, F401  # 注册所有 table 到 metadata

config = context.config

# alembic 官方约定：调用方可用 `config.attributes["configure_logger"] = False`
# 声明"我自己管日志"。默认的 fileConfig 会以 disable_existing_loggers=True 行事，
# 把启动前已存在的 uvicorn / app.* logger 全部禁用 —— 表现为服务起来后
# 应用日志（诊断耗时、token、缓存命中、成本、工具链）与 uvicorn 访问日志集体消失。
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

# SQLModel 统一 metadata（所有 table=True 模型自动注册）
target_metadata = SQLModel.metadata

# 用 settings 覆盖 alembic.ini 里的占位 URL
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite 不支持 batch_mode；Postgres 不需要
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=settings.database_url.startswith("sqlite"),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
