"""
定时健康检查调度器（多系统）。
遍历注册表中的每个系统，跑各自的健康探测；发现异常时把告警卡片发到
**该系统自己的 notify 渠道**（system.notify.chat_id），互不串台。
每个系统可在描述符里用 check_interval 覆盖全局巡检间隔。
告警卡片只列出问题和建议，详细修复报告在用户点击「立即修复」后才生成。
"""
import threading
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, 'agent')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import collect_health
import registry

CHECK_INTERVAL = int(os.getenv('HEALTH_CHECK_INTERVAL', '1800'))
# 巡检粒度：取全局间隔与 60s 的较小值，保证每系统的 check_interval 能较及时生效
_TICK = max(15, min(CHECK_INTERVAL, 60))


def _suggest_fix(issues: list, system: dict) -> str:
    names = '、'.join(i.split(' ')[0] for i in issues if i.strip())
    if system.get('local'):
        return f'点击「立即修复」让 Agent 自动诊断并修复（涉及：{names}）'
    return f'该系统为远程只读接入，请人工处置：{names}'


class HealthCheckScheduler:
    def __init__(self, feishu_client, get_chat_id_fn):
        self.feishu = feishu_client
        self.get_chat_id = get_chat_id_fn
        self._stop = threading.Event()
        self._last_run: dict = {}   # system_id -> monotonic 时间戳

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        logger.info(f'多系统定时巡检已启动，巡检粒度 {_TICK} 秒（全局间隔 {CHECK_INTERVAL} 秒）')

    def _run(self):
        self._stop.wait(_TICK)
        while not self._stop.is_set():
            self._do_check()
            self._stop.wait(_TICK)

    def _do_check(self):
        now = time.monotonic()
        for system in registry.list_systems():
            sid = system.get('id', '')
            interval = int(system.get('check_interval', CHECK_INTERVAL))
            last = self._last_run.get(sid, 0)
            if last and (now - last) < interval:
                continue
            self._last_run[sid] = now
            try:
                self._check_one(system)
            except Exception as e:
                logger.error(f'[{sid}] 定时巡检出错: {e}', exc_info=True)

    def _check_one(self, system: dict):
        sid = system.get('id', '')
        health = collect_health(system)
        issues = [f'{name} 异常：{detail}' for name, ok, detail in health if not ok]
        if not issues:
            logger.info(f'[{sid}] 巡检完成：系统状态正常')
            return

        chat_id = (system.get('notify') or {}).get('chat_id') or self.get_chat_id()
        if not chat_id:
            logger.info(f'[{sid}] 无可用 chat_id，跳过本次告警通知')
            return

        suggestion = _suggest_fix(issues, system)
        logger.info(f'[{sid}] 发现 {len(issues)} 个异常，发送告警卡片 → {chat_id}')
        self.feishu.send_alert_card(chat_id, issues, suggestion)
