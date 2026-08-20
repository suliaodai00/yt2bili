'use strict';

const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

let allTasks = {};
let filter = 'all';
let hist = { cpu: [], mem: [] };
let openLogs = {};

/* ================= 工具 ================= */
function fmtDur(sec) {
  if (sec == null) return '--';
  sec = Math.round(sec);
  if (sec < 60) return sec + 's';
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return m + '分' + s + 's';
  const h = Math.floor(m / 60);
  return h + '时' + (m % 60) + '分';
}
function fmtBytes(b) {
  if (!b) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return b.toFixed(b >= 100 ? 0 : 1) + ' ' + u[i];
}
function fmtUptime(sec) {
  if (sec == null) return '--';
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  if (d > 0) return d + '天' + h + '时';
  if (h > 0) return h + '时' + m + '分';
  return m + '分钟';
}

function toast(msg, type) {
  const el = $('toast');
  el.textContent = msg;
  el.className = 'toast show' + (type ? ' ' + type : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.className = 'toast', 2600);
}

function statusLabel(s) {
  return { queued: '排队中', running: '运行中', done: '成功', error: '失败' }[s] || s;
}

/* ================= 图表 ================= */
function drawSpark(el, data, color) {
  if (!el || !data || data.length < 2) return;
  const W = 200, H = 40, P = 3;
  const max = Math.max(...data, 1), min = Math.min(...data, 0);
  const range = (max - min) || 1;
  const pts = data.map((v, i) => {
    const x = P + (i / (data.length - 1)) * (W - 2 * P);
    const y = H - P - ((v - min) / range) * (H - 2 * P);
    return x + ',' + y;
  }).join(' ');
  el.setAttribute('viewBox', `0 0 ${W} ${H}`);
  el.innerHTML = `<polyline points="${pts}" stroke="${color}" stroke-width="1.6"/>`;
}

/* ================= 系统监控 ================= */
function renderSystem(d) {
  if (!d) return;
  const cpu = d.cpu;
  if (cpu != null) {
    $('cpuNum').textContent = cpu.toFixed(1) + '%';
    hist.cpu.push(cpu);
    if (hist.cpu.length > 60) hist.cpu.shift();
    drawSpark($('cpuChart'), hist.cpu, '#00b4d8');
  }
  const mem = d.memory || {};
  if (mem.total) {
    const pct = mem.percent ?? 0;
    $('memNum').textContent = pct.toFixed(1) + '%';
    $('memBar').style.width = pct + '%';
    $('memDetail').textContent = fmtBytes(mem.used) + ' / ' + fmtBytes(mem.total);
    hist.mem.push(pct);
    if (hist.mem.length > 60) hist.mem.shift();
  }
  const disk = d.disk || {};
  if (disk.total) {
    $('diskNum').textContent = (disk.percent ?? 0).toFixed(0) + '%';
    $('diskBar').style.width = (disk.percent ?? 0) + '%';
    $('diskDetail').textContent = fmtBytes(disk.used) + ' / ' + fmtBytes(disk.total);
  }
  if (d.loadavg) {
    $('loadNum').textContent = d.loadavg.map(x => x.toFixed(2)).join(' / ');
  }
  $('uptimeTxt').textContent = '已运行 ' + fmtUptime(d.uptime) + ' · 并发 ' + (d.active ?? 0);

  // YouTube 下载体系状态渲染
  const yt = d.youtube;
  if (yt) {
    const potBadge = $('potBadge');
    if (potBadge) {
      if (yt.pot_provider === 'online') {
        potBadge.className = 'badge badge-on';
        potBadge.textContent = 'PO Token 🟢 正常';
      } else {
        potBadge.className = 'badge badge-off';
        potBadge.textContent = 'PO Token 🔴 离线';
      }
    }
    const potArchStatus = $('potArchStatus');
    if (potArchStatus) {
      potArchStatus.textContent = yt.pot_provider === 'online' ? '🟢 在线运行' : '🔴 离线';
      $('potArchBadge').className = 'yt-arch-badge ' + (yt.pot_provider === 'online' ? 'badge-green' : 'badge-red');
      $('potArchBadge').textContent = yt.pot_provider === 'online' ? '127.0.0.1:4416' : '容器离线';
    }
    const ffArchStatus = $('ffArchStatus');
    if (ffArchStatus) {
      const hasFf = yt.firefox_profile === 'exists' && yt.firefox_cookie_count > 0;
      ffArchStatus.textContent = hasFf ? '🟢 Profile 就绪' : '⚪ 未配置';
      $('ffArchBadge').className = 'yt-arch-badge ' + (hasFf ? 'badge-green' : 'badge-yellow');
      $('ffArchBadge').textContent = `${yt.firefox_cookie_count || 0} 条 Cookie`;
    }
    const ytcArchStatus = $('ytcArchStatus');
    if (ytcArchStatus) {
      ytcArchStatus.textContent = yt.cookies_txt ? '🟢 文件存在' : '⚪ 未放置';
      $('ytcArchBadge').className = 'yt-arch-badge ' + (yt.cookies_txt ? 'badge-green' : 'badge-yellow');
      $('ytcArchBadge').textContent = yt.cookies_txt ? 'youtube_cookies.txt' : '无文件';
    }
  }
}

function renderOllama(o) {
  const el = $('ollamaBadge');
  if (o && o.online) {
    el.className = 'badge badge-on';
    el.textContent = 'Ollama 在线' + (o.model ? ' · ' + o.model : '');
  } else {
    el.className = 'badge badge-off';
    el.textContent = 'Ollama 离线';
  }
}

/* ================= Cookie 配置 ================= */
function renderCookies(d) {
  if (!d) return;
  const map = { 'cookie-youtube': d.youtube, 'cookie-bilibili': d.bilibili };
  for (const elId in map) {
    const c = map[elId];
    const el = $(elId);
    if (!el || !c) continue;
    el.querySelector('.cookie-path').textContent = c.path;
    el.querySelector('.cookie-path').title = c.path;
    const st = el.querySelector('.cookie-status');
    if (c.exists) {
      st.className = 'cookie-status ok';
      st.textContent = '已配置 · ' + fmtBytes(c.size) + ' · ' + new Date(c.mtime * 1000).toLocaleString('zh-CN', { hour12: false });
    } else {
      st.className = 'cookie-status warn';
      st.textContent = '未配置（文件不存在）';
    }
  }
  renderProxy(d.proxy);
}

/* ================= 下载代理 ================= */
function renderProxy(proxy) {
  const input = $('proxyInput');
  const toggle = $('proxyToggle');
  const label = $('proxyToggleLabel');
  const status = $('proxyStatus');
  const enabled = !!(proxy && proxy.trim());
  input.value = proxy || '';
  toggle.checked = enabled;
  input.disabled = !enabled;
  label.textContent = enabled ? '代理：开启' : '代理：关闭';
  if (enabled) {
    status.className = 'proxy-status ok';
    status.textContent = '已启用 · ' + proxy;
  } else {
    status.className = 'proxy-status warn';
    status.textContent = '未启用（直连下载）';
  }
}

function saveProxy() {
  const input = $('proxyInput');
  const toggle = $('proxyToggle');
  const proxy = toggle.checked ? input.value.trim() : '';
  if (toggle.checked && !proxy) {
    toast('请填写代理地址，或关闭开关', 'err');
    return;
  }
  fetch('/api/proxy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ proxy })
  }).then(r => r.json()).then(d => {
    if (d.error) { toast(d.error, 'err'); return; }
    toast('代理配置已保存', 'ok');
    renderProxy(d.proxy);
  }).catch(() => toast('保存失败', 'err'));
}

/* ================= Cookie 在线登录 / 上传 ================= */
let biliKey = null;
let biliTimer = null;

function startBiliLogin() {
  $('biliModal').style.display = 'flex';
  $('biliQr').style.display = 'none';
  $('biliStatus').textContent = '生成二维码中...';
  fetch('/api/bili-login/start', { method: 'POST' }).then(r => r.json()).then(d => {
    if (d.error) { $('biliStatus').textContent = d.error; return; }
    if (!d.qr) { $('biliStatus').textContent = '服务器缺少二维码库'; return; }
    biliKey = d.key;
    $('biliQr').src = d.qr;
    $('biliQr').style.display = 'block';
    $('biliStatus').textContent = '请使用 Bilibili App 扫码';
    pollBiliStatus();
  }).catch(() => { $('biliStatus').textContent = '获取二维码失败'; });
}

function pollBiliStatus() {
  clearTimeout(biliTimer);
  if (!biliKey) return;
  fetch('/api/bili-login/status?key=' + encodeURIComponent(biliKey)).then(r => r.json()).then(d => {
    if (d.status === 'ok') {
      biliKey = null;
      $('biliStatus').textContent = '登录成功！';
      toast('Bilibili 登录成功', 'ok');
      setTimeout(closeBiliLogin, 1200);
      refreshAll();
      return;
    }
    if (d.status === 'expired') { $('biliStatus').textContent = '二维码已过期，请点击刷新'; return; }
    if (d.status === 'scanned') { $('biliStatus').textContent = '已扫码，请在手机上确认'; }
    else if (d.status === 'error') { $('biliStatus').textContent = d.message || '登录出错'; return; }
    biliTimer = setTimeout(pollBiliStatus, 2000);
  }).catch(() => { biliTimer = setTimeout(pollBiliStatus, 3000); });
}

function refreshBiliQr() { clearTimeout(biliTimer); biliKey = null; startBiliLogin(); }
function closeBiliLogin() { clearTimeout(biliTimer); biliKey = null; $('biliModal').style.display = 'none'; }

function uploadYtCookie() {
  const inp = $('ytCookieFile');
  inp.onchange = () => {
    const file = inp.files[0];
    inp.value = '';
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    toast('上传中...');
    fetch('/api/yt-cookie', { method: 'POST', body: fd }).then(r => r.json()).then(d => {
      if (d.error) return toast(d.error, 'err');
      toast('YouTube cookie 已保存', 'ok');
      refreshAll();
    }).catch(() => toast('上传失败', 'err'));
  };
  inp.click();
}

/* ================= 统计渲染 ================= */
function renderStats(s) {
  if (!s) return;
  $('stTotal').textContent = s.total ?? 0;
  $('stRunning').textContent = s.running ?? 0;
  $('stDone').textContent = s.done ?? 0;
  $('stError').textContent = s.error ?? 0;
  $('stRate').textContent = s.rate ?? '0%';
  $('stSubs').textContent = s.subs ?? 0;
}

/* ================= 任务渲染 ================= */
function renderTasks() {
  const list = Object.values(allTasks).sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  const filtered = list.filter(t => {
    if (filter === 'all') return true;
    return t.status === filter;
  });

  const empty = $('emptyState');
  const container = $('taskList');

  if (list.length === 0) {
    empty.style.display = 'block';
    container.innerHTML = '';
    renderAssistant(null);
    return;
  }
  empty.style.display = filtered.length === 0 ? 'block' : 'none';

  const running = list.find(t => t.status === 'running') || list.find(t => t.status === 'queued') || list[0];
  renderAssistant(running);

  container.innerHTML = filtered.map(t => {
    const isLogOpen = openLogs[t.id];
    return `
      <div class="task-card ${t.status}" id="task-${t.id}">
        <div class="task-head">
          <div class="task-title-row">
            <span class="task-status-tag ${t.status}">${statusLabel(t.status)}</span>
            <span class="task-title">${esc(t.title || '正在获取信息...')}</span>
          </div>
          <div class="task-meta">
            <span class="task-elapsed" data-tid="${t.id}">${fmtDur(t.duration)}</span>
            ${t.bvid ? `<a href="https://www.bilibili.com/video/${t.bvid}" target="_blank" class="bvid-link">📺 ${t.bvid}</a>` : ''}
          </div>
        </div>
        <div class="task-progress-box">
          <div class="task-step-info">
            <span>${esc(t.step || '')}</span>
            <strong>${t.progress || 0}%</strong>
          </div>
          <div class="task-bar-track">
            <div class="task-bar-val ${t.status}" style="width:${t.progress || 0}%"></div>
          </div>
        </div>
        <div class="task-foot">
          <button class="mini-btn" onclick="toggleLog('${t.id}')">${isLogOpen ? '收起日志' : '查看日志'}</button>
          ${t.status === 'error' ? `<button class="mini-btn" onclick="retryTask('${t.id}')">🔄 重试</button>` : ''}
          ${t.status === 'done' || t.status === 'error' ? `<button class="mini-btn danger" onclick="deleteFiles('${t.id}')">🗑️ 清理文件</button>` : ''}
        </div>
        <div class="task-log" id="log-${t.id}" style="display:${isLogOpen ? 'block' : 'none'}">
          <pre>${esc((t.logs || []).join('\n'))}</pre>
        </div>
      </div>
    `;
  }).join('');
}

function renderAssistant(t) {
  const line = $('assistantLine');
  const pct = $('assistantPercent');
  const bar = $('assistantProgressFill');
  if (!t) {
    line.textContent = '等待新的 YouTube 链接';
    pct.textContent = '0%';
    bar.style.width = '0%';
    return;
  }
  line.textContent = `[${statusLabel(t.status)}] ${t.title || '处理中'} - ${t.step || ''}`;
  pct.textContent = `${t.progress || 0}%`;
  bar.style.width = `${t.progress || 0}%`;
}

function toggleLog(tid) {
  openLogs[tid] = !openLogs[tid];
  const el = $('log-' + tid);
  if (el) el.style.display = openLogs[tid] ? 'block' : 'none';
  const btn = el?.previousElementSibling?.querySelector('button');
  if (btn) btn.textContent = openLogs[tid] ? '收起日志' : '查看日志';
}

/* ================= 操作 ================= */
function submitTask() {
  const input = $('urlInput');
  const url = input.value.trim();
  if (!url) return toast('请输入链接', 'err');
  const btn = $('startBtn');
  btn.disabled = true; btn.textContent = '提交中...';
  fetch('/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url })
  }).then(r => r.json()).then(d => {
    btn.disabled = false; btn.textContent = '开始';
    if (d.error) { toast(d.error, 'err'); return; }
    input.value = '';
    toast('任务已提交', 'ok');
    refreshAll();
  }).catch(() => {
    btn.disabled = false; btn.textContent = '开始';
    toast('提交失败', 'err');
  });
}

function retryTask(tid) {
  const t = allTasks[tid];
  if (!t || !t.url) return toast('无法重试', 'err');
  fetch('/api/retry', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_id: tid })
  }).then(r => r.json()).then(d => {
    if (d.error) return toast(d.error, 'err');
    toast('已重新排队', 'ok');
    refreshAll();
  }).catch(() => toast('重试失败', 'err'));
}

function clearDone() {
  if (!confirm('确认清空所有已完成/失败的历史任务？')) return;
  fetch('/api/clear', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ only_finished: true })
  }).then(r => r.json()).then(d => {
    if (d.ok) { toast('已清空', 'ok'); refreshAll(); }
  }).catch(() => toast('清空失败', 'err'));
}

function deleteFiles(tid) {
  const t = allTasks[tid];
  if (!t) return;
  if (!confirm('确认删除该任务的全部已下载文件（视频/字幕/烧录成品）？此操作不可恢复。')) return;
  fetch('/api/delete-files', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_id: tid })
  }).then(r => r.json()).then(d => {
    if (d.error) return toast(d.error, 'err');
    toast('已删除 ' + d.deleted + ' 个文件', 'ok');
    refreshAll();
  }).catch(() => toast('删除失败', 'err'));
}

function clearAllFiles() {
  const btn = $('clearAllBtn');
  const res = $('clearResult');
  if (btn) { btn.disabled = true; btn.textContent = '清除中...'; }
  if (res) res.textContent = '';
  if (!confirm('确认清除全部已生成的视频、字幕、烧录成品文件？\n同时会清空所有任务记录。\n\ncookie 与代理配置将保留。此操作不可恢复！')) {
    if (btn) { btn.disabled = false; btn.textContent = '清除全部生成文件'; }
    return;
  }
  fetch('/api/clear-all', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
  }).then(r => r.json()).then(d => {
    if (btn) { btn.disabled = false; btn.textContent = '清除全部生成文件'; }
    if (d.ok) {
      const msg = '已清除 ' + d.removed + ' 个文件，清空 ' + d.tasks_cleared + ' 条任务，释放 ' + d.freed_mb + ' MB';
      if (res) res.textContent = msg;
      if (res) res.style.color = 'var(--ok)';
      toast(msg, 'ok');
      refreshAll();
    } else {
      toast(d.error || '清除失败', 'err');
    }
  }).catch(() => {
    if (btn) { btn.disabled = false; btn.textContent = '清除全部生成文件'; }
    toast('清除失败', 'err');
  });
}

/* ================= Telegram 机器人 ================= */
function saveTgToken() {
  const input = $('tgTokenInput');
  const btn = $('tgSaveBtn');
  const status = $('tgStatus');
  const token = input.value.trim();
  if (!token) { status.textContent = '请输入 Token'; status.style.color = 'var(--err)'; return; }
  btn.disabled = true; btn.textContent = '保存中...';
  fetch('/api/tg-token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token })
  }).then(r => r.json()).then(d => {
    btn.disabled = false; btn.textContent = '保存 Token';
    if (d.ok) {
      status.textContent = '✅ 已保存，机器人已重启';
      status.style.color = 'var(--ok)';
      setTimeout(() => { status.textContent = ''; }, 3000);
      renderTgStatus(d);
    } else {
      status.textContent = d.error || '保存失败';
      status.style.color = 'var(--err)';
    }
  }).catch(() => {
    btn.disabled = false; btn.textContent = '保存 Token';
    status.textContent = '请求失败'; status.style.color = 'var(--err)';
  });
}

function renderTgStatus(d) {
  const el = $('tgBotStatus');
  if (!el) return;
  const input = $('tgTokenInput');
  if (d && d.token_set) {
    if (input && !input.value) input.value = d.token_preview || '';
  }
  if (d && d.running) {
    el.className = 'proxy-status ok';
    el.textContent = '🤖 ' + (d.bot_name || 'Bot') + ' 在线 · 已记录 ' + (d.users || 0) + ' 个用户';
  } else if (d && d.token_set) {
    el.className = 'proxy-status';
    el.textContent = 'Token 已配置，启动中...';
  } else {
    el.className = 'proxy-status warn';
    el.textContent = '未配置 Bot Token';
  }
}

/* ================= 轮询 ================= */
function refreshAll() {
  Promise.all([
    fetch('/api/stats').then(r => r.json()).catch(() => null),
    fetch('/api/system').then(r => r.json()).catch(() => null),
    fetch('/api/cookies').then(r => r.json()).catch(() => null),
    fetch('/api/tg-token').then(r => r.json()).catch(() => null),
    fetch('/tasks').then(r => r.json()).catch(() => null)
  ]).then(([stats, sys, cookies, tg, tasks]) => {
    if (stats) renderStats(stats);
    if (sys) { renderSystem(sys); renderOllama(sys.ollama); }
    if (cookies) renderCookies(cookies);
    if (tg) renderTgStatus(tg);
    if (tasks) { allTasks = tasks; renderTasks(); }
  }).catch(() => {});
}

function tick() {
  $('clock').textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  updateElapsed();
}

function updateElapsed() {
  const now = Date.now() / 1000;
  document.querySelectorAll('.task-elapsed').forEach(el => {
    const t = allTasks[el.dataset.tid];
    if (!t) { el.textContent = ''; return; }
    if (t.status === 'queued') { el.textContent = '排队中'; return; }
    if (t.status !== 'running' || !t.started_at) { el.textContent = ''; return; }
    el.textContent = '已运行 ' + fmtDur(now - t.started_at);
  });
}

/* ================= 事件 ================= */
$('urlInput').addEventListener('keydown', e => { if (e.key === 'Enter') submitTask(); });

$('pageNav').addEventListener('click', e => {
  const tab = e.target.closest('.page-tab');
  if (!tab) return;
  document.querySelectorAll('.page-tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  const page = tab.dataset.page;
  document.querySelectorAll('.page').forEach(p => { p.style.display = p.id === 'page-' + page ? '' : 'none'; });
});

$('proxyToggle').addEventListener('change', e => {
  const enabled = e.target.checked;
  $('proxyInput').disabled = !enabled;
  $('proxyToggleLabel').textContent = enabled ? '代理：开启' : '代理：关闭';
  if (enabled && !$('proxyInput').value.trim()) $('proxyInput').focus();
});

$('tabBar').addEventListener('click', e => {
  const tab = e.target.closest('.tab');
  if (!tab) return;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  filter = tab.dataset.f;
  renderTasks();
});

/* ================= 启动 ================= */
tick();
refreshAll();
setInterval(refreshAll, 4000);
setInterval(tick, 1000);
