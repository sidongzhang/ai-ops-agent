"""
连接器基类（v1 只读）。每个被注册的服务通过描述符声明用哪种连接器，
连接器把「探活 / 读日志 / 搜日志」这些能力以统一接口暴露给 agent 工具层。
"""


class Connector:
    kind = 'base'

    def __init__(self, service: dict, system: dict):
        self.service = service or {}
        self.system = system or {}
        self.name = self.service.get('name', '')

    def health(self) -> tuple:
        """返回 (ok: bool, detail: str)。"""
        return (False, '该连接器未实现健康检查')

    def read_logs(self, lines: int = 50) -> str:
        return f'服务 {self.name} 未配置日志来源'

    def search_logs(self, keyword: str, lines: int = 200) -> str:
        return f'服务 {self.name} 未配置日志来源'
