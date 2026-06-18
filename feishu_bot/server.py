#!/usr/bin/env python3
"""
飞书机器人 Webhook 服务器
飞书消息 → Agent 分析 → 回复飞书
支持：主动消息处理、卡片按钮回调（立即修复）、定时健康巡检
"""
import json
import os
import sys
import threading
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, '.env'))

sys.path.insert(0, os.path.join(_ROOT, 'agent'))
from feishu_client import FeishuClient
from agent import get_agent_response, summarize_incident
from tools import execute_tool
from scheduler import HealthCheckScheduler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [飞书Bot] %(message)s',
    force=True,  # 覆盖 Flask/Werkzeug 提前设置的 handler
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

VERIFICATION_TOKEN = os.getenv('FEISHU_VERIFICATION_TOKEN', '')
feishu = FeishuClient(
    app_id=os.getenv('FEISHU_APP_ID', ''),
    app_secret=os.getenv('FEISHU_APP_SECRET', ''),
)

# 防止飞书重试导致同一消息处理两次
_processed = set()
_processed_lock = threading.Lock()

# 正在修复中的告警卡片 message_id，防止重复点击"立即修复"
_fix_in_progress: set[str] = set()
_fix_lock = threading.Lock()

# 记录最近一个活跃的 chat_id，用于定时巡检通知
# 优先使用 .env 中配置的固定 chat_id
_last_chat_id = os.getenv('FEISHU_ALERT_CHAT_ID', '')
_chat_id_lock = threading.Lock()


def _get_chat_id() -> str:
    with _chat_id_lock:
        return _last_chat_id


def _set_chat_id(chat_id: str):
    global _last_chat_id
    with _chat_id_lock:
        _last_chat_id = chat_id


# ── 启动定时巡检调度器 ────────────────────────────────────────────────────────

scheduler = HealthCheckScheduler(
    feishu_client=feishu,
    execute_tool_fn=execute_tool,
    get_agent_fn=get_agent_response,
    get_chat_id_fn=_get_chat_id,
)
scheduler.start()


# ── Webhook 路由 ──────────────────────────────────────────────────────────────

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json or {}

    # ── URL 验证（配置 Webhook 时飞书会发这个）──
    challenge = (data.get('challenge')
                 or data.get('event', {}).get('challenge'))
    if challenge:
        return jsonify({'challenge': challenge})

    # ── 卡片按钮回调 ──
    event_type = (data.get('header', {}).get('event_type', '')
                  or data.get('type', ''))
    if event_type in ('card.action.trigger', 'card_action'):
        return _handle_card_action(data)

    # ── Token 验证 ──
    token = (data.get('token')
             or data.get('header', {}).get('token', ''))
    if VERIFICATION_TOKEN and token != VERIFICATION_TOKEN:
        logger.warning('Token 不匹配，忽略')
        return jsonify({'code': 0})

    # ── 去重 ──
    event_id = data.get('header', {}).get('event_id', '')
    if event_id:
        with _processed_lock:
            if event_id in _processed:
                return jsonify({'code': 0})
            _processed.add(event_id)

    # ── 解析消息 ──
    message = data.get('event', {}).get('message', {})
    if message.get('message_type') == 'text':
        content = json.loads(message.get('content', '{}'))
        text = content.get('text', '').strip()
        for part in text.split():
            if part.startswith('@'):
                text = text.replace(part, '').strip()

        chat_id = message.get('chat_id', '')
        message_id = message.get('message_id', '')
        if text and chat_id:
            # 记录 chat_id 供定时巡检使用
            _set_chat_id(chat_id)
            logger.info(f'收到提问: {text[:80]} [message_id={message_id}]')
            threading.Thread(
                target=_handle,
                args=(text, chat_id, message_id),
                daemon=True
            ).start()

    return jsonify({'code': 0})


# ── 卡片按钮回调处理 ──────────────────────────────────────────────────────────

def _handle_card_action(data: dict):
    """处理飞书卡片按钮点击事件"""
    event = data.get('event', data)
    action_value = event.get('action', {}).get('value', {})
    action = action_value.get('action', '')

    # 提取 chat_id（兼容新旧格式）
    context = event.get('context', {})
    chat_id = (context.get('open_chat_id')
               or data.get('open_chat_id', '')
               or _get_chat_id())

    if action == 'fix_issues':
        alert_message_id = context.get('open_message_id', '')
        with _fix_lock:
            if alert_message_id in _fix_in_progress:
                return jsonify({'toast': {'type': 'warning', 'content': '⏳ 修复正在进行中，请勿重复点击'}})
            _fix_in_progress.add(alert_message_id)
        logger.info(f'收到修复请求，chat_id={chat_id}, alert_msg={alert_message_id}')
        threading.Thread(
            target=_do_fix,
            args=(chat_id, alert_message_id),
            daemon=True
        ).start()
        return jsonify({'toast': {'type': 'info', 'content': '🔧 正在修复，请稍候...'}})

    if action == 'dismiss':
        alert_message_id = context.get('open_message_id', '')
        issues = action_value.get('issues', [])
        body = ('已忽略以下问题：\n' + '\n'.join(f'• {i}' for i in issues)) if issues else ''
        if alert_message_id:
            threading.Thread(
                target=feishu.update_alert_card,
                args=(alert_message_id, 'dismissed', body),
                daemon=True
            ).start()
        return jsonify({'toast': {'type': 'info', 'content': '已忽略本次告警'}})

    return jsonify({})


def _append_runbook_entry(question: str, result: str):
    """后台调用：把本次修复经验结构化后追加到 runbook.md（最新在前）"""
    try:
        summary = summarize_incident(question, result)
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
        title = next((l.strip() for l in result.splitlines() if l.strip()), '故障修复')[:40]
        entry = f"\n## [{timestamp}] {title}\n\n{summary}\n"

        runbook = os.path.join(_ROOT, 'agent', 'docs', 'runbook.md')
        with open(runbook, 'r', encoding='utf-8') as f:
            content = f.read()

        # 在第一个空行之后（header 注释之后）插入新条目，保持最新在前
        header_end = content.find('\n\n')
        if header_end == -1:
            new_content = content + entry
        else:
            new_content = content[:header_end + 2] + entry + content[header_end + 2:]

        with open(runbook, 'w', encoding='utf-8') as f:
            f.write(new_content)

        logger.info(f'已归档故障记录到 runbook.md：{title}')
    except Exception as e:
        logger.warning(f'归档 runbook 失败（忽略）: {e}')



def _do_fix(chat_id: str, alert_message_id: str = ''):
    """调用 Agent 修复：预先收集现场信息作为上下文，减少 Agent 自己探测的轮次"""
    try:
        logger.info('收集现场信息...')
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f_svc = ex.submit(execute_tool, 'list_services', {})
            f_http = ex.submit(execute_tool, 'http_check', {})
            services_snapshot = f_svc.result()
            http_snapshot = f_http.result()

        question = (
            '系统定时巡检发现异常，请立即修复并验证所有服务恢复正常。\n\n'
            f'【当前服务状态】\n{services_snapshot}\n\n'
            f'【HTTP 可用性】\n{http_snapshot}\n\n'
            '请根据以上信息执行修复操作，并给出最终状态报告。'
        )

        logger.info('交由 Agent 执行修复...')
        result = get_agent_response(question)

        if alert_message_id:
            summary_lines = [l for l in result.splitlines() if l.strip()][:2]
            summary = '\n'.join(summary_lines) or '所有服务已恢复正常'
            feishu.update_alert_card(alert_message_id, 'fixed', summary)
        feishu.send_fix_result_card(chat_id, result)
        logger.info('修复完成，结果卡片已发送')
        # 后台归档，不阻塞主流程
        threading.Thread(target=_append_runbook_entry, args=(question, result), daemon=True).start()
    except Exception as e:
        logger.error(f'修复过程出错: {e}', exc_info=True)
        feishu.send_text(chat_id, f'❌ 修复过程出错：{e}')
    finally:
        with _fix_lock:
            _fix_in_progress.discard(alert_message_id)


# ── 普通消息处理 ──────────────────────────────────────────────────────────────

def _handle(question: str, chat_id: str, message_id: str = ''):
    reaction_id = ''
    try:
        if message_id:
            reaction_id = feishu.add_reaction(message_id, 'OK')
        result = get_agent_response(question)
        if message_id and reaction_id:
            feishu.delete_reaction(message_id, reaction_id)
        feishu.send_card(chat_id, result)
    except Exception as e:
        logger.error(f'Agent 出错: {e}', exc_info=True)
        feishu.send_text(chat_id, f'❌ 出错了：{e}')


if __name__ == '__main__':
    port = int(os.getenv('FEISHU_BOT_PORT', '8080'))
    logger.info(f'Webhook 服务启动，端口 {port}')
    app.run(host='0.0.0.0', port=port, debug=False)
