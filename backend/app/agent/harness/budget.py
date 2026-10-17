"""Admission for model rounds, including failed and concurrent child requests."""
import threading
from ..runtime.contracts import RuntimeBudgetExceeded


class AdmissionBudget:
    error_message = '运行预算已用完'

    def __init__(self, limit, used=0):
        self.limit=limit
        self.used=used
        self._lock=threading.Lock()

    def admit(self):
        with self._lock:
            if self.limit is not None and self.used>=self.limit:
                raise RuntimeBudgetExceeded(self.error_message)
            self.used+=1


class RequestBudget(AdmissionBudget):
    error_message = '诊断模型请求预算已用完，不启动下一次请求'

    def __init__(self, limit, used=0, *, reported_requests_offset=0, tool_budget=None):
        super().__init__(limit, used)
        self.reported_requests_offset=reported_requests_offset
        self.tool_budget=tool_budget


class ToolBudget(AdmissionBudget):
    error_message = '诊断工具调用预算已用完，不执行下一次工具调用'

    def __init__(self, limit, used=0, *, reported_tool_calls_offset=0):
        super().__init__(limit, used)
        self.reported_tool_calls_offset=reported_tool_calls_offset
