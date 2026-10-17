"""Shared domain harness contract (Pydantic primary, Pi read-only trial).

Authority: platform-bound RunScope, catalog schemas and ToolGateway; prompts,
documents and model arguments cannot widen scope. MCP exports use this gateway.
Lifecycle: child_context shares cancellation, executor tracking, audit and atomic
request/tool budgets. Stop requests are not stop acknowledgements. Unknown
execution disables resume and retains conservative accounting.
Context: context/context_data project references and omit connector credential
maps; original native histories stay in platform storage. Engine-specific codecs
validate restoration without cross-engine conversion or a fresh zero baseline.
Evidence: freshness is shared with resume. claims checks only bounded explicit
text consistency; evidence availability never means the complete answer is true.
Accounting/terminal state: services.diagnostics owns reservation/ledger writes,
history durability, conditional finalization, shutdown and stale-owner recovery.
Engines run their native loops; the harness does not create another agent loop.
"""
