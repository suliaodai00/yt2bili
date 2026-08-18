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
    if (!d.qr) { $('biliStatus').textContent = '服务器缺少二维码库，请执行: pip install qrcode pillow'; return; }
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

/* ================= 统计 ================= */
function renderStats(s) {
  $('stTotal').textContent = s.total ?? 0;
  $('stRunning').textContent = s.running ?? 0;
  $('stDone').textContent = s.done ?? 0;
  $('stError').textContent = s.error ?? 0;
  $('stRate').textContent = (s.success_rate ?? 0) + '%';
  $('stSubs').textContent = s.subtitle_count ?? 0;
}

function renderAssistant() {
  const stage = $('assistantStage');
  if (!stage) return;
  const tasks = Object.values(allTasks);
  const running = tasks.filter(t => ['queued', 'running'].includes(t.status));
  const errors = tasks.filter(t => t.status === 'error');
  const done = tasks.filter(t => t.status === 'done');
  let pct = 0;
  let line = '等待新的 YouTube 链接';
  let state = 'empty';

  if (tasks.length) {
    const totalProgress = tasks.reduce((sum, t) => sum + Number(t.progress || 0), 0);
    pct = Math.round(totalProgress / tasks.length);
    if (running.length) {
      const active = running.sort((a, b) => (b.created_at || 0) - (a.created_at || 0))[0];
      const activePct = Number(active.progress || 0);
      pct = activePct;
      state = 'running';
      line = (active.step || '任务处理中') + ' · ' + activePct.toFixed(1) + '%';
    } else if (errors.length) {
      state = 'error';
      line = '有任务失败，点开日志查看原因';
    } else if (done.length === tasks.length) {
      pct = 100;
      state = 'done';
      line = '全部任务已完成，可以继续投喂链接';
    } else {
      state = 'empty';
      line = '任务队列已同步';
    }
  }

  stage.classList.remove('state-empty', 'state-running', 'state-done', 'state-error');
  stage.classList.add('state-' + state);
  $('assistantLine').textContent = line;
  $('assistantPercent').textContent = Number(pct).toFixed(1) + '%';
  $('assistantProgressFill').style.width = Number(pct).toFixed(1) + '%';
}

/* ================= 任务列表 ================= */
function taskCardsHtml() {
  let list = Object.values(allTasks).sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
  if (filter === 'running') list = list.filter(t => ['queued', 'running'].includes(t.status));
  else if (filter !== 'all') list = list.filter(t => t.status === filter);

  const visible = Object.keys(openLogs).filter(id => openLogs[id]);
  const html = list.map(t => {
    const tid = t.id;
    const st = t.status || 'error';
    const dur = t.duration ? '<span class="task-meta">' + fmtDur(t.duration) + '</span>' : '';
    const subs = t.subtitle_count ? '<span class="task-meta">字幕 ' + t.subtitle_count + ' 条</span>' : '';
    const open = visible.includes(tid);
    const logHtml = open && t.logs && t.logs.length
      ? '<div class="task-log open" id="log' + tid + '">' + t.logs.map(l => '<div>' + esc(l) + '</div>').join('') + '</div>'
      : '';
    const canRetry = (st === 'error' || st === 'done') && t.url;
    const hasFile = t.video_id || t.video_file;
    const deletedMark = t.files_deleted ? '<span class="task-meta" style="color:var(--err)">文件已删除</span>' : '';
    return `<div class="task-card" id="c${tid}">
      <div class="task-head">
        <div class="task-head-left">
          <span class="tag tag-${st}">${statusLabel(st)}</span>
          <div class="task-title" title="${esc(t.title)}">${esc(t.title)}</div>
          ${dur}${subs}${deletedMark}
        </div>
        <div class="task-actions">
          <button class="mini-btn" onclick="toggleLog('${tid}')">${open ? '收起' : '日志'}</button>
          <button class="mini-btn" onclick="exportLog('${tid}')" title="导出任务日志为 txt">导出</button>
          <button class="mini-btn" onclick="copyUrl('${tid}')" title="复制原链接">复制</button>
          ${canRetry ? `<button class="mini-btn retry" onclick="retryTask('${tid}')">重试</button>` : ''}
          ${hasFile && !t.files_deleted ? `<button class="mini-btn danger" onclick="deleteFiles('${tid}')">删文件</button>` : ''}
        </div>
      </div>
      <div class="task-progress"><div class="task-fill${st === 'error' ? ' err' : ''}" style="width:${Number(t.progress || 0).toFixed(1)}%"></div></div>
      <div class="task-foot">
        <span class="task-step">${esc(t.step || '')}${(t.status === 'running' || t.status === 'queued') ? ' · ' + Number(t.progress || 0).toFixed(1) + '%' : ''}</span>
        ${t.video_id ? '<span class="task-meta">' + esc(t.video_id) + '</span>' : ''}
      </div>
      ${logHtml}
    </div>`;
  }).join('');
  $('emptyState').style.display = list.length ? 'none' : 'block';
  return html || '<div class="empty">当前筛选下暂无任务</div>';
}

function renderTasks() {
  const scrolls = {};
  Object.keys(openLogs).forEach(id => {
    if (openLogs[id]) {
      const el = document.getElementById('log' + id);
      if (el) {
        const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        scrolls[id] = { top: el.scrollTop, atBottom };
      }
    }
  });
  $('taskList').innerHTML = taskCardsHtml();
  Object.keys(scrolls).forEach(id => {
    const el = document.getElementById('log' + id);
    if (el) {
      const s = scrolls[id];
      if (s.atBottom) el.scrollTop = el.scrollHeight;
      else el.scrollTop = s.top;
    }
  });
  renderAssistant();
}

function toggleLog(tid) {
  openLogs[tid] = !openLogs[tid];
  renderTasks();
  const log = $('log' + tid);
  if (log) log.scrollTop = log.scrollHeight;
}

function copyUrl(tid) {
  const t = allTasks[tid];
  if (!t || !t.url) return toast('无链接', 'err');
  (navigator.clipboard ? navigator.clipboard.writeText(t.url) : Promise.reject())
    .then(() => toast('已复制链接', 'ok'))
    .catch(() => toast('复制失败', 'err'));
}

function exportLog(tid) {
  toast('正在导出...', 'ok');
  fetch('/api/export-log?task_id=' + encodeURIComponent(tid))
    .then(r => {
      if (!r.ok) return r.json().then(d => { throw new Error(d.error || ('HTTP ' + r.status)); });
      const cd = r.headers.get('Content-Disposition') || '';
      const m = cd.match(/filename="?([^";]+)"?/);
      const name = m ? m[1] : 'y2b_' + tid + '.txt';
      return r.blob().then(blob => ({ blob, name }));
    })
    .then(({ blob, name }) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      toast('日志已导出', 'ok');
    })
    .catch(e => toast('导出失败: ' + e.message, 'err'));
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

/* ================= 轮询 ================= */
function refreshAll() {
  Promise.all([
    fetch('/api/stats').then(r => r.json()),
    fetch('/api/system').then(r => r.json()),
    fetch('/api/cookies').then(r => r.json()),
    fetch('/tasks').then(r => r.json())
  ]).then(([stats, sys, cookies, tasks]) => {
    renderStats(stats);
    renderSystem(sys);
    renderOllama(sys.ollama);
    renderCookies(cookies);
    allTasks = tasks;
    renderTasks();
  }).catch(() => {});
}

function tick() {
  $('clock').textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

/* ================= 事件 ================= */
$('urlInput').addEventListener('keydown', e => { if (e.key === 'Enter') submitTask(); });

/* 页面切换 */
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
