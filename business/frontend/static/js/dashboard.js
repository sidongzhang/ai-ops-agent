const BL = '#3B82F6', BLA = 'rgba(59,130,246,0.1)', RL = '#EF4444', RLA = 'rgba(239,68,68,0.1)';

const chart = new Chart(document.getElementById('trendChart').getContext('2d'), {
  type: 'line',
  data: {
    labels: [],
    datasets: [{
      label: '条/分钟', data: [],
      borderColor: BL, backgroundColor: BLA,
      borderWidth: 2, pointRadius: 3,
      pointBackgroundColor: BL, pointBorderColor: '#fff', pointBorderWidth: 2,
      tension: 0.4, fill: true,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { backgroundColor: '#1E293B', titleColor: '#94A3B8', bodyColor: '#F1F5F9', padding: 10, cornerRadius: 10 },
    },
    scales: {
      x: { grid: { color: '#F1F5F9' }, ticks: { font: { size: 11 }, color: '#9CA3AF' } },
      y: { beginAtZero: true, grid: { color: '#F1F5F9' }, ticks: { font: { size: 11 }, color: '#9CA3AF', stepSize: 1 } },
    },
    animation: { duration: 400 },
  }
});

// 倒计时
let cdSec = 5;
const cdEl = document.getElementById('countdown');
setInterval(() => { cdSec--; if (cdSec < 0) cdSec = 4; cdEl.textContent = cdSec + 's 后刷新'; }, 1000);

// 一键巡检
function triggerInspect() {
  const panel = document.getElementById('chat-panel');
  const btn = document.getElementById('inspect-btn');
  if (!panel.classList.contains('open')) {
    document.getElementById('chat-btn').click();
  }
  setTimeout(() => {
    const inp = document.getElementById('chat-input');
    inp.value = '请全面检查当前系统状态，给出巡检报告';
    inp.dispatchEvent(new Event('input'));
    document.getElementById('chat-send').click();
  }, 300);
  btn.classList.add('loading'); btn.textContent = '⏳ 巡检中…';
  setTimeout(() => { btn.classList.remove('loading'); btn.innerHTML = '🔍 立即巡检'; }, 30000);
}

function setPip(id, ok) {
  const el = document.getElementById(id);
  if (el) el.className = 'pip' + (ok ? '' : ' dead');
}

async function refresh() {
  try {
    const [sR, hR] = await Promise.all([fetch('/api/stats'), fetch('/api/history')]);
    const stats = await sR.json(), hist = await hR.json();

    // Header service pips
    setPip('pip-producer', stats.producer_ok);
    setPip('pip-consumer', stats.consumer_ok);
    setPip('pip-kafka', stats.kafka_ok);
    setPip('pip-mysql', stats.db_ok);

    // DB badge — 只在异常时显示
    const bdg = document.getElementById('db-badge'), lbl = document.getElementById('db-label');
    bdg.style.display = stats.db_ok ? 'none' : 'flex';
    lbl.textContent = 'MySQL 异常';

    // Alert banner — 只在有服务真正停止时显示
    const alertBar = document.getElementById('alert-bar'), alertTxt = document.getElementById('alert-text');
    const dead = [];
    if (!stats.producer_ok) dead.push('Producer');
    if (!stats.consumer_ok) dead.push('Consumer');
    if (!stats.kafka_ok) dead.push('Kafka');
    if (!stats.db_ok) dead.push('MySQL');
    alertBar.className = dead.length ? 'show' : '';
    if (dead.length) alertTxt.textContent = '服务停止：' + dead.join(' / ') + '，数据链路中断，请立即处理';

    // Stats
    document.getElementById('total').textContent = stats.total.toLocaleString();
    const lr = hist.counts.length ? hist.counts[hist.counts.length - 1] : 0;
    const rEl = document.getElementById('rate'); rEl.textContent = lr; rEl.style.color = lr === 0 ? RL : BL;
    const sd = document.getElementById('status-dot'), ok = stats.pipeline_ok && stats.db_ok;
    if (ok) {
      sd.innerHTML = '<span class="stat-val ok-tag">⬤ 正常运行</span>';
    } else {
      const d = [];
      if (!stats.producer_ok) d.push('Producer');
      if (!stats.consumer_ok) d.push('Consumer');
      sd.innerHTML = `<span class="stat-val err-tag">⬤ ${d.length ? d.join(' / ') + ' 停止' : '数据中断'}</span>`;
    }

    // Chart
    const bad = lr === 0;
    chart.data.labels = hist.labels; chart.data.datasets[0].data = hist.counts;
    chart.data.datasets[0].borderColor = bad ? RL : BL;
    chart.data.datasets[0].backgroundColor = bad ? RLA : BLA;
    chart.data.datasets[0].pointBackgroundColor = bad ? RL : BL;
    chart.update();

    // Table
    const tb = document.getElementById('recent-rows');
    if (stats.recent && stats.recent.length) {
      tb.innerHTML = stats.recent.map(r => `<tr>
        <td><span class="tag">${r[0]}</span></td>
        <td class="num">${parseFloat(r[1]).toFixed(2)}</td>
        <td style="color:var(--t3)">${r[2]}</td>
        <td style="color:var(--t3);font-size:12px">${r[3]}</td>
      </tr>`).join('');
    } else {
      tb.innerHTML = '<tr><td colspan="4" style="text-align:center;padding:40px;color:var(--t3)">等待数据写入...</td></tr>';
    }

    document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
    cdSec = 5;
  } catch {
    document.getElementById('db-badge').className = 'status-badge err';
    document.getElementById('db-label').textContent = '连接失败';
  }
}

refresh();
setInterval(refresh, 5000);
