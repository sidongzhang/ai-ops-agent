"""Create child contexts without widening authority or losing run controls."""
from dataclasses import replace
from uuid import uuid4


def child_context(parent, task: str, allowed_tools):
    inherited = getattr(parent, 'allowed_tools', None)
    permitted = frozenset(allowed_tools)
    if inherited is not None:
        permitted &= inherited
    from .performance import CALL
    active = CALL.get()
    return replace(
        parent, question=task, intent_text=parent.intent_text or parent.question,
        image_count=0, allowed_tools=permitted,
        tool_call_namespace=f'child_{uuid4().hex}:',
        performance_agent=f'child_{uuid4().hex[:12]}',
        performance_parent=active[2] if active else getattr(parent,'performance_parent',None),
    )
