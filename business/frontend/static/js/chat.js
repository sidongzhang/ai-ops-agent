(function () {
  const btn = document.getElementById('chat-btn'),
        panel = document.getElementById('chat-panel'),
        close = document.getElementById('chat-close'),
        msgs = document.getElementById('chat-messages'),
        input = document.getElementById('chat-input'),
        send = document.getElementById('chat-send'),
        hdr = document.getElementById('chat-hdr'),
        inpRow = document.getElementById('chat-input-row');

  const KEY = 'ai_chat_v2', TTL = 864e5;
  let opened = false;

  function syncH() {
    if (!panel.classList.contains('open')) return;
    const hh = hdr.offsetHeight, ih = inpRow.offsetHeight, ph = panel.offsetHeight;
    msgs.style.top = hh + 'px';
    msgs.style.height = (ph - hh - ih) + 'px';
  }
  window.addEventListener('resize', syncH);

  const loadH = () => { try { const r = localStorage.getItem(KEY); return r ? JSON.parse(r).filter(m => m.ts > Date.now() - TTL) : []; } catch { return []; } };
  const saveH = l => { try { localStorage.setItem(KEY, JSON.stringify(l)); } catch {} };
  let hist = loadH();

  const md = (typeof marked !== 'undefined') ? marked.parse.bind(marked) : t => t.replace(/</g, '&lt;');

  function wrapTables(el) {
    el.querySelectorAll('table').forEach(t => {
      if (t.parentNode.classList.contains('tbl-scroll')) return;
      const w = document.createElement('div');
      w.className = 'tbl-scroll';
      t.parentNode.insertBefore(w, t);
      w.appendChild(t);
    });
  }

  const bubble = (cls, text) => {
    const d = document.createElement('div');
    d.className = 'msg ' + cls;
    if (cls === 'msg-agent') { d.innerHTML = md(text); wrapTables(d); } else { d.textContent = text; }
    msgs.appendChild(d);
    return d;
  };

  const append = (cls, text, save) => {
    const d = document.createElement('div');
    d.className = 'msg ' + cls;
    if (cls === 'msg-loading') {
      d.innerHTML = '<div class="dots"><span></span><span></span><span></span></div>';
    } else if (cls === 'msg-agent') {
      d.innerHTML = md(text); wrapTables(d);
    } else {
      d.textContent = text;
    }
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
    if (save) { hist.push({ cls, text, ts: Date.now() }); saveH(hist); }
    return d;
  };

  if (hist.length) {
    msgs.innerHTML = '';
    hist.forEach(m => bubble(m.cls, m.text));
    msgs.scrollTop = msgs.scrollHeight;
  }

  btn.addEventListener('click', () => {
    panel.classList.toggle('open');
    if (panel.classList.contains('open')) {
      setTimeout(() => { syncH(); msgs.scrollTop = msgs.scrollHeight; }, 0);
      if (!opened) { opened = true; input.focus(); }
    }
  });
  close.addEventListener('click', () => panel.classList.remove('open'));

  msgs.addEventListener('wheel', e => {
    const atTop = msgs.scrollTop === 0 && e.deltaY < 0;
    const atBot = msgs.scrollTop + msgs.clientHeight >= msgs.scrollHeight && e.deltaY > 0;
    if (!atTop && !atBot) e.stopPropagation();
  }, { passive: true });

  async function doSend() {
    const q = input.value.trim();
    if (!q) return;
    input.value = ''; input.style.height = '';
    send.disabled = input.disabled = true;
    append('msg-user', q, true);
    const loader = append('msg-loading', '', false);
    try {
      const r = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q }),
      });
      const data = await r.json();
      const text = data.answer || data.error || '服务暂时不可用。';
      loader.className = 'msg msg-agent';
      loader.innerHTML = md(text);
      wrapTables(loader);
      hist.push({ cls: 'msg-agent', text, ts: Date.now() }); saveH(hist);
    } catch {
      const text = '网络错误，请检查服务状态。';
      loader.className = 'msg msg-agent'; loader.innerHTML = md(text);
      hist.push({ cls: 'msg-agent', text, ts: Date.now() }); saveH(hist);
    }
    msgs.scrollTop = msgs.scrollHeight;
    send.disabled = input.disabled = false;
    input.focus();
  }

  send.addEventListener('click', doSend);
  input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSend(); } });
  input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 100) + 'px'; });
})();
