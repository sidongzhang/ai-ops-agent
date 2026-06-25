#!/usr/bin/env python3
"""
Frontend: Flask 展示数据统计，Chart.js 折线图，JavaScript 轮询刷新
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

import socket
import requests as _req
from flask import Flask, jsonify, render_template, request
import mysql.connector
from mysql.connector import Error as MySQLError

app = Flask(__name__)

MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'user': os.getenv('MYSQL_USER', 'opsuser'),
    'password': os.getenv('MYSQL_PASSWORD', 'opspass'),
    'database': os.getenv('MYSQL_DB', 'opsdb'),
}


def _connect():
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    conn.cursor().execute("SET time_zone = '+08:00'")
    return conn


_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PIDS_DIR = os.path.join(_ROOT, 'pids')


def _kafka_alive() -> bool:
    try:
        s = socket.create_connection(('localhost', 9092), timeout=1)
        s.close()
        return True
    except Exception:
        return False


def _proc_alive(name: str) -> bool:
    try:
        with open(os.path.join(_PIDS_DIR, f'{name}.pid')) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def get_stats():
    producer_ok = _proc_alive('producer')
    consumer_ok = _proc_alive('consumer')
    kafka_ok = _kafka_alive()
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sensor_data")
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT sensor_id, value, unit, received_at FROM sensor_data ORDER BY id DESC LIMIT 10"
        )
        recent = cur.fetchall()
        cur.execute(
            "SELECT TIMESTAMPDIFF(SECOND, MAX(received_at), NOW()) FROM sensor_data"
        )
        seconds_since = cur.fetchone()[0]
        cur.close()
        conn.close()
        # 进程存活 且 最近 120 秒内有写入，链路才算正常
        data_fresh = seconds_since is not None and seconds_since < 120
        pipeline_ok = producer_ok and consumer_ok and data_fresh
        return total, recent, True, pipeline_ok, seconds_since, producer_ok, consumer_ok, kafka_ok
    except MySQLError:
        return 0, [], False, False, None, producer_ok, consumer_ok, kafka_ok


def get_history():
    """返回近 15 分钟每分钟写入条数，用于折线图"""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT DATE_FORMAT(received_at, %s) AS minute, COUNT(*) AS cnt "
            "FROM sensor_data WHERE received_at >= NOW() - INTERVAL 15 MINUTE "
            "GROUP BY minute ORDER BY minute",
            ('%H:%i',)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        labels = [r[0] for r in rows]
        counts = [r[1] for r in rows]
        return labels, counts
    except MySQLError:
        return [], []


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/stats')
def api_stats():
    total, recent, db_ok, pipeline_ok, seconds_since, producer_ok, consumer_ok, kafka_ok = get_stats()
    recent_list = [[r[0], float(r[1]), r[2], str(r[3])] for r in recent]
    return jsonify({
        'total': total, 'db_ok': db_ok, 'pipeline_ok': pipeline_ok,
        'seconds_since': seconds_since, 'producer_ok': producer_ok,
        'consumer_ok': consumer_ok, 'kafka_ok': kafka_ok, 'recent': recent_list,
    })


@app.route('/api/history')
def api_history():
    labels, counts = get_history()
    return jsonify({'labels': labels, 'counts': counts})


@app.route('/api/chat', methods=['POST'])
def api_chat():
    data = request.get_json(silent=True) or {}
    question = data.get('question', '').strip()
    if not question:
        return jsonify({'error': '问题不能为空'}), 400
    try:
        r = _req.post('http://localhost:8080/internal/chat',
                      json={'question': question}, timeout=120)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({'error': f'AI 服务暂时不可用：{e}'}), 503


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
