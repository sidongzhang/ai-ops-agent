"""
定时健康检查调度器
每隔 HEALTH_CHECK_INTERVAL 秒自动巡检，发现异常时向飞书发送简短告警卡片（含"立即修复"按钮）
告警卡片只列出问题和建议，详细修复报告在用户点击按钮后才生成。
"""
import threading
import logging
import os
import sys

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, 'agent'))

CHECK_INTERVAL = int(os.getenv('HEALTH_CHECK_INTERVAL', '1800'))


def _parse_issues(services_output: str, http_output: str) -> list[str]:
    """从快速检查结果中提取异常条目，返回简短问题描述列表"""
    issues = []
    for line in services_output.splitlines():
        if '❌' in line or '已停止' in line:
            # 例：'  producer    : 已停止 ❌' → 'producer 进程已停止'
            name = line.strip().split(':')[0].strip()
            if name:
                issues.append(f'{name} 进程已停止')
    for line in http_output.splitlines():
        if '❌' in line:
            issues.append(line.strip())
    return issues


def _suggest_fix(issues: list[str]) -> str:
    """根据问题列表给出一句话建议"""
    proc_issues = [i for i in issues if '进程已停止' in i]
    http_issues = [i for i in issues if '进程' not in i]
    parts = []
    if proc_issues:
        names = '、'.join(i.replace(' 进程已停止', '') for i in proc_issues)
        parts.append(f'重启 {names}')
    if http_issues:
        parts.append('检查对应服务的容器状态')
    return '；'.join(parts) if parts else '点击立即修复让 Agent 自动处理'


class HealthCheckScheduler:
    def __init__(self, feishu_client, execute_tool_fn, get_agent_fn, get_chat_id_fn):
        self.feishu = feishu_client
        self.execute_tool = execute_tool_fn
        self.get_agent_response = get_agent_fn
        self.get_chat_id = get_chat_id_fn
        self._stop = threading.Event()

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        logger.info(f'定时巡检已启动，间隔 {CHECK_INTERVAL} 秒')

    def _run(self):
        self._stop.wait(CHECK_INTERVAL)
        while not self._stop.is_set():
            self._do_check()
            self._stop.wait(CHECK_INTERVAL)

    def _do_check(self):
        chat_id = self.get_chat_id()
        if not chat_id:
            logger.info('无已知 chat_id，跳过本次巡检通知')
            return

        logger.info('开始定时巡检...')
        try:
            services = self.execute_tool('list_services', {})
            http = self.execute_tool('http_check', {})

            issues = _parse_issues(services, http)
            if not issues:
                logger.info('巡检完成：系统状态正常')
                return

            suggestion = _suggest_fix(issues)
            logger.info(f'发现 {len(issues)} 个异常，发送告警卡片')
            self.feishu.send_alert_card(chat_id, issues, suggestion)

        except Exception as e:
            logger.error(f'定时巡检出错: {e}', exc_info=True)
