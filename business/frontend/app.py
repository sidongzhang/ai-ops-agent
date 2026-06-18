#!/usr/bin/env python3
"""
Frontend: Flask 展示数据统计，Chart.js 折线图，JavaScript 轮询刷新
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
  <title>AI Ops Demo</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, Arial, sans-serif; background: #f0f2f5; color: #333; }
    .header { background: #001529; color: white; padding: 16px 32px; display: flex; align-items: center; justify-content: space-between; }
    .header h1 { font-size: 20px; }
    .header-right { font-size: 12px; color: #888; text-align: right; }
    .content { max-width: 960px; margin: 24px auto; padding: 0 16px; }
    .card { background: white; border-radius: 8px; padding: 24px; margin-bottom: 16px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
    .stats-row { display: flex; gap: 16px; }
    .stat-box { flex: 1; background: white; border-radius: 8px; padding: 24px;
                box-shadow: 0 1px 4px rgba(0,0,0,0.1); }
    .stat-num { font-size: 52px; font-weight: bold; color: #1890ff; line-height: 1; }
    .stat-num.red { color: #ff4d4f; }
    .stat-label { color: #888; margin-top: 8px; font-size: 13px; }
    .badge { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px; }
    .ok   { background: #f6ffed; color: #52c41a; border: 1px solid #b7eb8f; }
    .err  { background: #fff2f0; color: #ff4d4f; border: 1px solid #ffccc7; }
    .warn { background: #fffbe6; color: #faad14; border: 1px solid #ffe58f; }
    table { width: 100%; border-collapse: collapse; }
    th { text-align: left; padding: 10px 12px; background: #fafafa;
         border-bottom: 1px solid #f0f0f0; color: #666; font-size: 13px; }
    td { padding: 10px 12px; border-bottom: 1px solid #f5f5f5; font-size: 14px; }
    tr:last-child td { border-bottom: none; }
    .chart-wrap { position: relative; height: 200px; }
    .footer { text-align: center; color: #bbb; font-size: 12px; padding: 16px; }
    .pulse { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
             background: #52c41a; margin-right: 6px; animation: pulse 2s infinite; }
    .pulse.dead { background: #ff4d4f; animation: none; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  </style>
</head>
<body>
  <div class="header">
    <h1>🤖 AI 智能运维 Demo &nbsp;—&nbsp; 数据监控面板</h1>
    <div class="header-right">
      数据库: <span id="db-badge" class="badge ok">正常</span>
      &nbsp;|&nbsp; 最后更新: <span id="last-update">--</span>
    </div>
  </div>

  <div class="content">
    <div class="stats-row" style="margin-bottom:16px;">
      <div class="stat-box">
        <div class="stat-num" id="total">--</div>
        <div class="stat-label">传感器数据总条数</div>
      </div>
      <div class="stat-box">
        <div class="stat-num" id="rate" style="font-size:36px;">--</div>
        <div class="stat-label">写入速率（条/分钟）</div>
      </div>
      <div class="stat-box">
        <div class="stat-num" id="status-dot" style="font-size:36px;">--</div>
        <div class="stat-label">数据链路状态</div>
      </div>
    </div>

    <div class="card">
      <h3 style="margin-bottom:16px;">📈 数据写入趋势（近 15 分钟）</h3>
      <div class="chart-wrap">
        <canvas id="trendChart"></canvas>
      </div>
    </div>

    <div class="card">
      <h3 style="margin-bottom:16px;">最近 10 条数据</h3>
      <table>
        <thead><tr><th>传感器</th><th>数值</th><th>单位</th><th>接收时间</th></tr></thead>
        <tbody id="recent-rows">
          <tr><td colspan="4" style="text-align:center;color:#bbb;padding:32px;">加载中...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="footer">Producer → Kafka (9092) → Consumer → MySQL (3306) → Frontend (:5001)</div>

<script>
const ctx = document.getElementById('trendChart').getContext('2d');
const chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [{
      label: '每分钟写入条数',
      data: [],
      borderColor: '#1890ff',
      backgroundColor: 'rgba(24,144,255,0.08)',
      borderWidth: 2,
      pointRadius: 3,
      pointBackgroundColor: '#1890ff',
      tension: 0.4,
      fill: true,
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { color: '#f0f0f0' }, ticks: { font: { size: 11 } } },
      y: { beginAtZero: true, grid: { color: '#f0f0f0' }, ticks: { font: { size: 11 }, stepSize: 1 } }
    },
    animation: { duration: 400 }
  }
});

let prevTotal = null;

async function refresh() {
  try {
    const [statsRes, histRes] = await Promise.all([
      fetch('/api/stats'), fetch('/api/history')
    ]);
    const stats = await statsRes.json();
    const hist  = await histRes.json();

    // 总条数
    document.getElementById('total').textContent = stats.total.toLocaleString();

    // 数据库状态
    const dbBadge = document.getElementById('db-badge');
    dbBadge.textContent = stats.db_ok ? '正常' : '异常';
    dbBadge.className = 'badge ' + (stats.db_ok ? 'ok' : 'err');

    // 写入速率（最近一分钟）
    const rateEl = document.getElementById('rate');
    const lastRate = hist.counts.length ? hist.counts[hist.counts.length - 1] : 0;
    rateEl.textContent = lastRate;
    rateEl.className = 'stat-num' + (lastRate === 0 ? ' red' : '');

    // 链路状态（以最后写入时间为准，>30秒无新数据则报异常）
    const statusEl = document.getElementById('status-dot');
    const pipelineOk = stats.pipeline_ok && stats.db_ok;
    statusEl.textContent = pipelineOk ? '✅ 正常' : '❌ 中断';
    statusEl.style.fontSize = '28px';
    statusEl.style.color = pipelineOk ? '' : '#ff4d4f';

    // 折线图
    chart.data.labels   = hist.labels;
    chart.data.datasets[0].data = hist.counts;
    chart.data.datasets[0].borderColor = lastRate === 0 ? '#ff4d4f' : '#1890ff';
    chart.data.datasets[0].backgroundColor = lastRate === 0
      ? 'rgba(255,77,79,0.08)' : 'rgba(24,144,255,0.08)';
    chart.data.datasets[0].pointBackgroundColor = lastRate === 0 ? '#ff4d4f' : '#1890ff';
    chart.update();

    // 最近数据行
    const tbody = document.getElementById('recent-rows');
    if (stats.recent && stats.recent.length) {
      tbody.innerHTML = stats.recent.map(r =>
        `<tr><td>${r[0]}</td><td>${parseFloat(r[1]).toFixed(2)}</td><td>${r[2]}</td><td>${r[3]}</td></tr>`
      ).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:#bbb;padding:32px;">暂无数据，等待 Producer 发送...</td></tr>';
    }

    document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
  } catch(e) {
    document.getElementById('db-badge').textContent = '连接失败';
    document.getElementById('db-badge').className = 'badge err';
  }
}

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>"""


def _connect():
    return mysql.connector.connect(**MYSQL_CONFIG)


def get_stats():
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
        # 超过 120 秒没有新数据，链路视为中断（producer 间隔 100 秒）
        pipeline_ok = seconds_since is not None and seconds_since < 120
        return total, recent, True, pipeline_ok, seconds_since
    except MySQLError:
        return 0, [], False, False, None


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
    return render_template_string(HTML)


@app.route('/api/stats')
def api_stats():
    total, recent, db_ok, pipeline_ok, seconds_since = get_stats()
    recent_list = [
        [r[0], float(r[1]), r[2], str(r[3])] for r in recent
    ]
    return jsonify({
        'total': total,
        'db_ok': db_ok,
        'pipeline_ok': pipeline_ok,
        'seconds_since': seconds_since,
        'recent': recent_list,
    })


@app.route('/api/history')
def api_history():
    labels, counts = get_history()
    return jsonify({'labels': labels, 'counts': counts})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
