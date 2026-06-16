#!/usr/bin/env python3
"""
Frontend: Flask 展示数据统计，每5秒自动刷新
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

from flask import Flask, jsonify, render_template_string
import mysql.connector
from mysql.connector import Error as MySQLError

app = Flask(__name__)

MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'user': os.getenv('MYSQL_USER', 'opsuser'),
    'password': os.getenv('MYSQL_PASSWORD', 'opspass'),
    'database': os.getenv('MYSQL_DB', 'opsdb'),
}

HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="5">
  <title>AI Ops Demo</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, Arial, sans-serif; background: #f0f2f5; color: #333; }
    .header { background: #001529; color: white; padding: 16px 32px; }
    .header h1 { font-size: 20px; }
    .header p { font-size: 12px; color: #888; margin-top: 4px; }
    .content { max-width: 900px; margin: 24px auto; padding: 0 16px; }
    .card { background: white; border-radius: 8px; padding: 24px; margin-bottom: 16px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
    .stat-num { font-size: 56px; font-weight: bold; color: #1890ff; line-height: 1; }
    .stat-label { color: #888; margin-top: 8px; font-size: 14px; }
    .badge { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px; }
    .ok { background: #f6ffed; color: #52c41a; border: 1px solid #b7eb8f; }
    .err { background: #fff2f0; color: #ff4d4f; border: 1px solid #ffccc7; }
    table { width: 100%; border-collapse: collapse; }
    th { text-align: left; padding: 10px 12px; background: #fafafa;
         border-bottom: 1px solid #f0f0f0; color: #666; font-size: 13px; }
    td { padding: 10px 12px; border-bottom: 1px solid #f5f5f5; font-size: 14px; }
    tr:last-child td { border-bottom: none; }
    .footer { text-align: center; color: #bbb; font-size: 12px; padding: 16px; }
  </style>
</head>
<body>
  <div class="header">
    <h1>🤖 AI 智能运维 Demo &nbsp;—&nbsp; 数据监控面板</h1>
    <p>页面每5秒自动刷新 &nbsp;|&nbsp; 数据库状态: <span class="{{ 'ok' if db_ok else 'err' }} badge">{{ '正常' if db_ok else '异常' }}</span></p>
  </div>
  <div class="content">
    <div class="card">
      <div class="stat-num">{{ total }}</div>
      <div class="stat-label">传感器数据总条数（由 Producer → Kafka → Consumer → MySQL 写入）</div>
    </div>
    <div class="card">
      <h3 style="margin-bottom:16px;">最近 10 条数据</h3>
      {% if recent %}
      <table>
        <tr><th>传感器</th><th>数值</th><th>单位</th><th>接收时间</th></tr>
        {% for row in recent %}
        <tr>
          <td>{{ row[0] }}</td>
          <td>{{ "%.2f"|format(row[1]) }}</td>
          <td>{{ row[2] }}</td>
          <td>{{ row[3] }}</td>
        </tr>
        {% endfor %}
      </table>
      {% else %}
      <p style="color:#bbb;text-align:center;padding:32px;">暂无数据，等待 Producer 发送...</p>
      {% endif %}
    </div>
  </div>
  <div class="footer">Producer → Kafka (9092) → Consumer → MySQL (3306) → Frontend (:5001)</div>
</body>
</html>"""


def get_stats():
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sensor_data")
        total = cursor.fetchone()[0]
        cursor.execute(
            "SELECT sensor_id, value, unit, received_at FROM sensor_data ORDER BY id DESC LIMIT 10"
        )
        recent = cursor.fetchall()
        cursor.close()
        conn.close()
        return total, recent, True
    except MySQLError:
        return 0, [], False


@app.route('/')
def index():
    total, recent, db_ok = get_stats()
    return render_template_string(HTML, total=total, recent=recent, db_ok=db_ok)


@app.route('/api/stats')
def api_stats():
    total, _, db_ok = get_stats()
    return jsonify({'total': total, 'db_ok': db_ok})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
