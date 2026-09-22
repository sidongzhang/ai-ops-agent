"""Workflow agent package.

approval_graph 在 lifespan 里可能被重建为持久化 checkpointer 版本，
因此这里不复制引用，统一通过 runner 模块属性访问最新实例。
"""
from . import runner
from .runner import resume_workflow, start_workflow

def __getattr__(name: str):
    if name == "approval_graph":
        return runner.approval_graph
    raise AttributeError(name)

__all__ = ["approval_graph", "start_workflow", "resume_workflow"]
