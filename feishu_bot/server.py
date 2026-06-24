#!/usr/bin/env python3
"""
飞书机器人 Webhook 服务器
飞书消息 → Agent 分析 → 回复飞书
支持：主动消息处理、卡片按钮回调（立即修复）、定时健康巡检
"""
import json
import os
import sys
import time
import threading
import logging
import itertools
from datetime import datetime
from flask import Flask, request, jsonify
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, '.env'))

sys.path.insert(0, os.path.join(_ROOT, 'agent'))
from feishu_client import FeishuClient
from agent import get_agent_response, get_agent_response_stream, summarize_incident
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

# 处理消息时轮流使用的 reaction emoji
_reaction_cycle = itertools.cycle(['ANGRY', 'Typing', 'OneSecond'])

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


def _warmup():
    """服务启动时预热 embedding 模型 + 建好向量索引，避免首次请求延迟"""
    try:
        sys.path.insert(0, os.path.join(_ROOT, 'agent'))
        from knowledge_base import get_relevant_context
        get_relevant_context('预热')
        logger.info('RAG embedding 模型预热完成')
    except Exception as e:
        logger.warning(f'RAG 预热失败（忽略）: {e}')

threading.Thread(target=_warmup, daemon=True).start()


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



_STEP_LABELS = {
    'list_services':    '🔍 检查服务状态',
    'check_process':    '🔍 确认进程状态',
    'read_logs':        '📋 读取日志',
    'search_logs':      '🔎 搜索异常日志',
    'restart_service':  '🔧 重启服务',
    'query_database':   '🗃️ 查询数据库',
    'get_kafka_status': '📊 检查 Kafka',
    'http_check':       '🌐 HTTP 检查',
    'get_system_info':  '💻 获取系统信息',
    'check_port':       '🔌 检查端口',
    'docker_logs':      '🐳 读取容器日志',
    'restart_docker':   '🐳 重启 Docker 容器',
    'query_redis':      '🔴 查询 Redis',
    'get_metrics':      '📈 获取指标',
}


def _do_fix(chat_id: str, alert_message_id: str = ''):
    """流式修复：立即发占位卡片，边执行边更新，最终替换为完整格式化卡片"""
    import time

    # 1. 立即发占位卡片，给用户即时反馈
    stream_msg_id = feishu.send_processing_card(chat_id, '🔧 正在修复中...')
    _start_time = time.time()

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

        # 2. on_step：每调用工具时更新进度，相同步骤去重
        steps_done: list = []
        _current_step = ['']

        def on_step(name, args):
            label = _STEP_LABELS.get(name, f'⚙️ {name}')
            prev = _current_step[0]
            if prev and prev != label and prev not in steps_done:
                steps_done.append(prev)
            _current_step[0] = label
            feishu.update_progress_card(stream_msg_id, steps_done, label, '🔧 正在修复中...', 'blue', _start_time)

        # 3. on_chunk 首次触发 → 工具全部执行完，LLM 开始生成结论
        _generating = [False]

        def on_chunk(text):
            if not _generating[0]:
                _generating[0] = True
                prev = _current_step[0]
                if prev and prev not in steps_done:
                    steps_done.append(prev)
                feishu.update_progress_card(stream_msg_id, steps_done, '正在生成修复报告...', '🔧 正在修复中...', 'blue', _start_time)

        logger.info('交由 Agent 执行修复...')
        result = get_agent_response_stream(question, on_chunk=on_chunk, on_step=on_step)

        # 4. 最终替换为完整格式化卡片
        feishu.finalize_fix_card(stream_msg_id, result)

        # 5. 更新原告警卡片状态
        if alert_message_id:
            summary_lines = [l for l in result.splitlines() if l.strip()][:2]
            summary = '\n'.join(summary_lines) or '所有服务已恢复正常'
            feishu.update_alert_card(alert_message_id, 'fixed', summary)

        logger.info('修复完成，流式卡片已更新')
        threading.Thread(target=_append_runbook_entry, args=(question, result), daemon=True).start()
    except Exception as e:
        logger.error(f'修复过程出错: {e}', exc_info=True)
        if stream_msg_id:
            feishu.update_streaming_card(stream_msg_id, f'❌ 修复过程出错：{e}', '❌ 修复失败', 'red')
        else:
            feishu.send_text(chat_id, f'❌ 修复过程出错：{e}')
    finally:
        with _fix_lock:
            _fix_in_progress.discard(alert_message_id)


# ── 普通消息处理 ──────────────────────────────────────────────────────────────

def _handle(question: str, chat_id: str, message_id: str = ''):
    reaction_id = ''
    try:
        if message_id:
            reaction_id = feishu.add_reaction(message_id, next(_reaction_cycle))

        stream_msg_id = feishu.send_processing_card(chat_id, '🤖 AI 运维分析')
        _start_time = time.time()

        steps_done: list = []
        _current_step = ['']
        _generating = [False]

        def on_step(name, args):
            label = _STEP_LABELS.get(name, f'⚙️ {name}')
            prev = _current_step[0]
            if prev and prev != label and prev not in steps_done:
                steps_done.append(prev)
            _current_step[0] = label
            feishu.update_progress_card(stream_msg_id, steps_done, label, '🤖 AI 运维分析', 'indigo', _start_time)

        def on_chunk(text):
            if not _generating[0]:
                _generating[0] = True
                prev = _current_step[0]
                if prev and prev not in steps_done:
                    steps_done.append(prev)
                feishu.update_progress_card(stream_msg_id, steps_done, '正在生成分析报告...', '🤖 AI 运维分析', 'indigo', _start_time)

        result = get_agent_response_stream(question, on_chunk=on_chunk, on_step=on_step)

        if message_id and reaction_id:
            feishu.delete_reaction(message_id, reaction_id)
            feishu.add_reaction(message_id, 'CheckMark')

        feishu.finalize_claude_card(stream_msg_id, result)
    except Exception as e:
        logger.error(f'Agent 出错: {e}', exc_info=True)
        feishu.send_text(chat_id, f'❌ 出错了：{e}')


if __name__ == '__main__':
    port = int(os.getenv('FEISHU_BOT_PORT', '8080'))
    logger.info(f'Webhook 服务启动，端口 {port}')
    app.run(host='0.0.0.0', port=port, debug=False)
