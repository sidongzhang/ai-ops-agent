#!/usr/bin/env python3
"""
飞书机器人 Webhook 服务器
飞书消息 → Agent 分析 → 回复飞书
"""
import json
import os
import sys
import threading
import logging
from flask import Flask, request, jsonify
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, '.env'))

sys.path.insert(0, os.path.join(_ROOT, 'agent'))
from feishu_client import FeishuClient
from agent import get_agent_response

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [飞书Bot] %(message)s'
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


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json or {}

    # ── URL 验证（配置 Webhook 时飞书会发这个）──
    challenge = (data.get('challenge')
                 or data.get('event', {}).get('challenge'))
    if challenge:
        return jsonify({'challenge': challenge})

    # ── Token 验证 ──
    token = (data.get('token')
             or data.get('header', {}).get('token', ''))
    if VERIFICATION_TOKEN and token != VERIFICATION_TOKEN:
        logger.warning(f"Token 不匹配，忽略")
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
        # 去掉 @机器人 标记
        for part in text.split():
            if part.startswith('@'):
                text = text.replace(part, '').strip()

        chat_id = message.get('chat_id', '')
        message_id = message.get('message_id', '')
        if text and chat_id:
            logger.info(f"收到提问: {text[:80]} [message_id={message_id}]")
            threading.Thread(
                target=_handle,
                args=(text, chat_id, message_id),
                daemon=True
            ).start()

    # 飞书要求 3 秒内返回 200
    return jsonify({'code': 0})


def _handle(question: str, chat_id: str, message_id: str = ''):
    reaction_id = ''
    try:
        # 在用户消息上加 🤔 reaction 表示处理中
        if message_id:
            reaction_id = feishu.add_reaction(message_id, 'OK')

        result = get_agent_response(question)

        # 分析完成：移除 reaction，再发卡片
        if message_id and reaction_id:
            feishu.delete_reaction(message_id, reaction_id)
        feishu.send_card(chat_id, result)
    except Exception as e:
        logger.error(f"Agent 出错: {e}", exc_info=True)
        feishu.send_text(chat_id, f"❌ 出错了：{e}")


if __name__ == '__main__':
    port = int(os.getenv('FEISHU_BOT_PORT', '8080'))
    logger.info(f"Webhook 服务启动，端口 {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
