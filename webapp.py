#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yt2bili Web 监控面板 — 粘贴 YouTube 链接, 一键下载→翻译→上传B站
含任务持久化、统计、服务器资源监控、失败重试。
"""
import os, sys, json, time, subprocess, threading, re, shutil, urllib.request, io, base64, http.cookiejar
from pathlib import Path
from flask import Flask, render_template, request, jsonify

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'

BASE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(BASE, '.venv')
PYTHON = os.path.join(VENV, 'bin/python3')
YTBIN = os.path.join(VENV, 'bin/yt-dlp')
CONFIG = os.path.join(BASE, 'config.yaml')
COOKIES = os.path.join(BASE, 'cookies.json')
YOUTUBE_COOKIES = os.path.join(BASE, 'youtube_cookies.txt')
DOWNLOAD_DIR = os.path.join(BASE, 'output', 'downloads')
SUBTITLE_DIR = os.path.join(BASE, 'output', 'subtitles')
FINAL_DIR = os.path.join(BASE, 'output', 'final')
TRANSLATE_PY = os.path.join(BASE, 'translate.py')
UPLOAD_PY = os.path.join(BASE, 'upload.py')
DATA_FILE = os.path.join(BASE, 'output', 'tasks.json')

MAX_TASKS = 100          # 保留的历史任务上限
MAX_CONCURRENT = 1       # 同时运行任务数上限

for d in [DOWNLOAD_DIR, SUBTITLE_DIR, FINAL_DIR]:
    os.makedirs(d, exist_ok=True)

app = Flask(__name__)

lock = threading.Lock()
tasks = {}
_active = 0


def load_tasks():
    """启动时从磁盘恢复历史任务"""
    global tasks
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding='utf-8') as f:
                tasks = json.load(f)
        except Exception:
            tasks = {}
    for t in tasks.values():
        if t.get('status') in ('queued', 'running'):
            t['status'] = 'error'
            t['step'] = '服务重启中断'
            t['logs'] = t.get('logs', []) + ["[--:--:--] ⚠️ 服务重启，任务中断"]


def save_tasks():
    """持久化任务到磁盘"""
    try:
        tmp = DATA_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, ensure_ascii=False)
        os.replace(tmp, DATA_FILE)
    except Exception:
        pass


def read_config():
    cfg = {'proxy': '', 'youtube_cookies': ''}
    if os.path.exists(CONFIG):
        with open(CONFIG) as f:
            for line in f:
                if line.startswith('proxy:'):
                    cfg['proxy'] = line.split(':', 1)[1].strip().strip('"')
    if os.path.exists(YOUTUBE_COOKIES):
        cfg['youtube_cookies'] = f'--cookies {YOUTUBE_COOKIES}'
    return cfg


def run_cmd(cmd_list, timeout=300, log_func=None):
    proc = subprocess.run(cmd_list, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        err = proc.stderr[-500:] if proc.stderr else 'unknown error'
        if log_func: log_func(f"⚠️ {err}")
        raise Exception(f"命令失败: {err}")
    return proc.stdout


def _touch_task(task_id, status=None, progress=None, step=None):
    """更新任务字段并持久化"""
    with lock:
        t = tasks.get(task_id)
        if not t: return
        if status is not None: t['status'] = status
        if progress is not None: t['progress'] = progress
        if step is not None: t['step'] = step
        save_tasks()


def run_task(task_id, url):
    """核心流水线: 下载 -> 翻译 -> 烧录 -> 上传"""
    global _active
    with lock:
        _active += 1
    task = tasks.get(task_id)
    if not task:
        with lock:
            _active -= 1
        return

    started = time.time()
    task['status'] = 'running'
    task['started_at'] = started
    task['step'] = '准备中...'
    log = lambda msg: task['logs'].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    cfg = read_config()
    save_tasks()

    # 添加 deno 路径（解决 EJS 挑战）
    deno_dir = '/root/.deno/bin'
    if os.path.exists(deno_dir):
        os.environ['PATH'] = f"{deno_dir}:{os.environ.get('PATH', '')}"
    ejs_opt = ['--remote-components', 'ejs:github']

    try:
        # ===== 1. 获取视频信息 =====
        task['phase'] = '获取信息'
        log("📋 获取视频信息...")
        proxy_opt = ['--proxy', cfg['proxy']] if cfg['proxy'] else []
        ck_opt = cfg['youtube_cookies'].split() if cfg['youtube_cookies'] else []
        meta_cmd = [YTBIN, '--print-json', '--skip-download'] + ejs_opt + proxy_opt + ck_opt + [url]
        meta_out = run_cmd(meta_cmd, timeout=30, log_func=log)
        info = json.loads(meta_out)
        vid = info['id']
        title = info['title']
        task['title'] = title
        task['video_id'] = vid
        task['phase'] = '获取信息'
        log(f"✅ 视频: {title}")

        # ===== 2. 下载 =====
        task['phase'] = '下载'
        task['step'] = '下载视频与字幕'
        task['progress'] = 10
        log("⬇️ 下载视频和英文字幕...")
        dl_cmd = [YTBIN] + ejs_opt + proxy_opt + ck_opt + [
            '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            '--write-subs', '--write-auto-subs', '--sub-langs', 'en',
            '--embed-subs', '--embed-thumbnail',
            '-o', f'{DOWNLOAD_DIR}/%(id)s.%(ext)s', url]
        t_dl = time.time()
        run_cmd(dl_cmd, timeout=300, log_func=log)
        task['duration_download'] = round(time.time() - t_dl, 1)
        log("✅ 下载完成")
        task['progress'] = 30

        video_file = None
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(vid) and f.endswith('.mp4'):
                video_file = os.path.join(DOWNLOAD_DIR, f); break
        if not video_file:
            mp4s = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.mp4')]
            if mp4s: video_file = os.path.join(DOWNLOAD_DIR, mp4s[0])
        if not video_file:
            raise Exception("未找到视频文件")
        task['video_file'] = video_file
        log(f"📁 视频: {os.path.basename(video_file)}")

        # ===== 3. 翻译 =====
        task['phase'] = '翻译'
        task['step'] = '翻译 (本地 qwen2.5, 免费无限)'
        task['progress'] = 40
        log("🌐 AI 双语翻译中...")

        en_sub = None
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(vid) and f.endswith('.en.vtt'):
                en_sub = os.path.join(DOWNLOAD_DIR, f); break
        if not en_sub:
            vtts = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.vtt')]
            if vtts: en_sub = vtts[0]

        output_srt = os.path.join(SUBTITLE_DIR, f'{vid}_bilingual_full.srt')
        task['subtitle_count'] = 0
        if en_sub:
            t_tr = time.time()
            out = run_cmd([PYTHON, TRANSLATE_PY, en_sub, output_srt, CONFIG], timeout=3600, log_func=log)
            task['duration_translate'] = round(time.time() - t_tr, 1)
            m = re.search(r'完成:\s*(\d+)/(\d+)', out or '')
            if m:
                task['subtitle_count'] = int(m.group(1))
            log("✅ 翻译完成")
        else:
            log("⚠️ 无英文字幕, 跳过")
            output_srt = None
        task['progress'] = 70

        # ===== 4. 烧录字幕 =====
        task['phase'] = '烧录字幕'
        task['step'] = '烧录字幕'
        task['progress'] = 75
        final_video = os.path.join(FINAL_DIR, f'{vid}_final_bilingual.mp4')
        video_to_upload = video_file
        if output_srt and os.path.exists(output_srt):
            log("🎬 烧录字幕...")
            burn_cmd = ['ffmpeg', '-y', '-i', video_file,
                       '-vf', f"subtitles={output_srt}:force_style='FontName=DejaVu Sans,FontSize=18,MarginV=40'",
                       '-c:a', 'copy', final_video]
            try:
                run_cmd(burn_cmd, timeout=600, log_func=log)
                if os.path.exists(final_video) and os.path.getsize(final_video) > 0:
                    log("✅ 烧录完成")
                    video_to_upload = final_video
            except Exception:
                pass
        task['progress'] = 85

        # ===== 5. 上传 =====
        task['phase'] = '上传 B 站'
        task['step'] = '上传 B 站'
        task['progress'] = 90
        log("⬆️ 上传到 Bilibili...")
        bili_title = f"{title[:40]} - 双语字幕"
        desc = f"原视频: {url}\n\nAI 双语字幕由本地模型 qwen2.5:7b 生成 (免费无限🚀)"
        t_up = time.time()
        run_cmd([PYTHON, UPLOAD_PY,
            '--video', video_to_upload,
            '--cookie', COOKIES,
            '--title', bili_title[:80],
            '--desc', desc[:1000],
            '--tid', '171',
            '--tags', 'AI,科技',
            '--source', url], timeout=1800, log_func=log)
        task['duration_upload'] = round(time.time() - t_up, 1)
        log("✅ 上传 B 站完成!")
        task['progress'] = 100
        task['status'] = 'done'
        task['step'] = '✅ 全部完成'

    except subprocess.TimeoutExpired:
        task['status'] = 'error'
        task['step'] = '超时'
        log("❌ 任务超时")
    except Exception as e:
        task['status'] = 'error'
        task['step'] = '失败'
        log(f"❌ 错误: {str(e)[:200]}")

    task['finished_at'] = time.time()
    task['duration'] = round(time.time() - started, 1)
    with lock:
        _active -= 1
        trim = list(tasks.keys())[-MAX_TASKS:]
        for k in list(tasks.keys()):
            if k not in trim and tasks[k].get('status') in ('done', 'error'):
                del tasks[k]
        save_tasks()


# ============================================================
# 系统资源监控
# ============================================================
def _read_proc_stat():
    try:
        with open('/proc/stat') as f:
            for line in f:
                if line.startswith('cpu '):
                    parts = [int(x) for x in line.split()[1:]]
                    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
                    total = sum(parts)
                    return idle, total
    except Exception:
        return None
    return None


def _cpu_percent():
    a = _read_proc_stat()
    if not a: return None
    time.sleep(0.5)
    b = _read_proc_stat()
    if not b: return None
    d_total = b[1] - a[1]
    if d_total <= 0: return 0.0
    d_idle = b[0] - a[0]
    return round((1 - d_idle / d_total) * 100, 1)


def _mem_info():
    try:
        with open('/proc/meminfo') as f:
            d = {}
            for line in f:
                k, v = line.split(':', 1)
                d[k] = int(v.strip().split()[0])  # kB
        total = d.get('MemTotal', 0) * 1024
        avail = d.get('MemAvailable', d.get('MemFree', 0)) * 1024
        return {'total': total, 'used': total - avail, 'avail': avail,
                'percent': round((total - avail) / total * 100, 1) if total else 0}
    except Exception:
        return {}


def system_stats():
    """采集服务器资源状态"""
    loadavg = None
    try:
        with open('/proc/loadavg') as f:
            la = f.read().split()
        loadavg = [float(x) for x in la[:3]]
    except Exception:
        pass

    cpu = _cpu_percent()
    mem = _mem_info()

    disk = {}
    try:
        du = shutil.disk_usage(DOWNLOAD_DIR)
        disk = {'total': du.total, 'used': du.used, 'free': du.free,
                'percent': round(du.used / du.total * 100, 1) if du.total else 0}
    except Exception:
        pass

    # Ollama 状态探测
    ollama = {'online': False, 'model': '', 'error': ''}
    url = 'http://127.0.0.1:11434'
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG) as f:
                for line in f:
                    if line.startswith('ollama_url:'):
                        url = line.split(':', 1)[1].strip().strip('"'); break
        except Exception:
            pass
    try:
        req = urllib.request.Request(url + '/api/tags')
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read())
        models = [m.get('name', '') for m in data.get('models', [])]
        ollama['online'] = True
        ollama['model'] = models[0] if models else ''
        ollama['models'] = models
    except Exception as e:
        ollama['error'] = str(e)[:100]

    uptime = None
    try:
        with open('/proc/uptime') as f:
            uptime = float(f.read().split()[0])
    except Exception:
        pass

    return {
        'cpu': cpu,
        'loadavg': loadavg,
        'memory': mem,
        'disk': disk,
        'ollama': ollama,
        'uptime': uptime,
        'ts': time.time(),
        'active': _active,
    }


# ============================================================
# 路由
# ============================================================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/start', methods=['POST'])
def start_task():
    data = request.get_json() or {}
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': '请输入链接'}), 400
    if 'youtube.com/watch' not in url and 'youtu.be/' not in url:
        return jsonify({'error': '请输入有效的 YouTube 链接'}), 400

    task_id = f"t{int(time.time() * 1000)}"
    with lock:
        tasks[task_id] = {
            'id': task_id, 'status': 'queued', 'progress': 0,
            'step': '排队中...', 'title': '处理中...',
            'video_id': '', 'video_file': '', 'logs': [], 'url': url,
            'created_at': time.time(),
            'duration': None, 'subtitle_count': 0,
        }
        save_tasks()
    t = threading.Thread(target=run_task, args=(task_id, url), daemon=True)
    t.start()
    return jsonify({'task_id': task_id})


@app.route('/status')
def get_status():
    t = tasks.get(request.args.get('task_id', ''))
    return jsonify(t or {'error': 'not found'}), (404 if not t else 200)


@app.route('/tasks')
def list_tasks():
    return jsonify(tasks)


@app.route('/api/stats')
def api_stats():
    total = len(tasks)
    by = {'queued': 0, 'running': 0, 'done': 0, 'error': 0}
    sub_count = 0
    durations = []
    for t in tasks.values():
        s = t.get('status', '')
        if s in by: by[s] += 1
        else: by['error'] += 1
        sub_count += t.get('subtitle_count', 0) or 0
        if t.get('duration'): durations.append(t['duration'])
    done_count = by['done']
    success_rate = round(done_count / total * 100, 1) if total else 0
    avg_duration = round(sum(durations) / len(durations), 1) if durations else None
    return jsonify({
        'total': total, 'queued': by['queued'], 'running': by['running'],
        'done': done_count, 'error': by['error'], 'success_rate': success_rate,
        'subtitle_count': sub_count, 'avg_duration': avg_duration,
    })


@app.route('/api/system')
def api_system():
    return jsonify(system_stats())


@app.route('/api/cookies')
def api_cookies():
    """返回 YouTube / Bilibili cookie 文件的放置路径与配置状态"""
    def _f(label, path):
        info = {'label': label, 'path': path, 'exists': False, 'size': 0, 'mtime': None}
        try:
            if os.path.exists(path):
                st = os.stat(path)
                info.update({'exists': True, 'size': st.st_size, 'mtime': st.st_mtime})
        except OSError:
            pass
        return info
    return jsonify({
        'youtube': _f('YouTube', YOUTUBE_COOKIES),
        'bilibili': _f('Bilibili', COOKIES),
    })


# ============================================================
# Cookie 在线登录 / 上传
# ============================================================
BILI_LOGIN_STATE = {}   # qrcode_key -> {jar, created_at}


def _atomic_write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


@app.route('/api/bili-login/start', methods=['POST'])
def bili_login_start():
    """调用 B 站扫码登录接口生成二维码，返回 PNG base64 与 qrcode_key"""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        req = urllib.request.Request(
            'https://passport.bilibili.com/x/passport-login/web/qrcode/generate',
            headers={'User-Agent': UA, 'Referer': 'https://www.bilibili.com/'})
        raw = opener.open(req, timeout=10).read()
        data = json.loads(raw)
    except Exception as e:
        return jsonify({'error': f'获取二维码失败: {str(e)[:100]}'}), 502
    if data.get('code') != 0:
        return jsonify({'error': f"B站返回: {data.get('message', data.get('code'))}"}), 502

    qkey = data['data']['qrcode_key']
    qurl = data['data']['url']
    qr_b64 = ''
    try:
        import qrcode
        img = qrcode.make(qurl)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        qr_b64 = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        pass

    now = time.time()
    for k in list(BILI_LOGIN_STATE):          # 清理 10 分钟前的旧会话
        if now - BILI_LOGIN_STATE[k]['created_at'] > 600:
            BILI_LOGIN_STATE.pop(k, None)
    BILI_LOGIN_STATE[qkey] = {'jar': jar, 'created_at': now}
    return jsonify({'key': qkey, 'qr': qr_b64, 'url': qurl})


@app.route('/api/bili-login/status')
def bili_login_status():
    qkey = request.args.get('key', '')
    st = BILI_LOGIN_STATE.get(qkey)
    if not st:
        return jsonify({'status': 'expired', 'message': '登录会话已失效'}), 404
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(st['jar']))
        req = urllib.request.Request(
            f'https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={qkey}',
            headers={'User-Agent': UA, 'Referer': 'https://www.bilibili.com/'})
        raw = opener.open(req, timeout=10).read()
        data = json.loads(raw)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)[:100]})
    if data.get('code') != 0:
        return jsonify({'status': 'error', 'message': f"B站返回: {data.get('message', data.get('code'))}"})
    inner = data.get('data') or {}
    state = inner.get('code', -1)
    if state == 0:
        cookies = {}
        for c in inner.get('cookie_info', {}).get('cookies', []):
            cookies[c['name']] = c['value']
        out = {'cookies': cookies}
        if inner.get('refresh_token'):
            out['refresh_token'] = inner['refresh_token']
        _atomic_write_json(COOKIES, out)
        BILI_LOGIN_STATE.pop(qkey, None)
        return jsonify({'status': 'ok', 'message': '登录成功', 'user': cookies.get('DedeUserID', '')})
    if state == 86090:
        return jsonify({'status': 'scanned', 'message': '已扫码，请在手机确认'})
    if state == 86101:
        return jsonify({'status': 'pending', 'message': '等待扫码'})
    if state == 86038:
        BILI_LOGIN_STATE.pop(qkey, None)
        return jsonify({'status': 'expired', 'message': '二维码已过期'})
    return jsonify({'status': 'error', 'message': f"code={state} {inner.get('message', '')}"})


@app.route('/api/yt-cookie', methods=['POST'])
def api_yt_cookie():
    """上传 YouTube Netscape 格式 cookie 文件，保存为 youtube_cookies.txt"""
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': '请选择 cookie 文件'}), 400
    content = f.read()
    if len(content) > 10 * 1024 * 1024:
        return jsonify({'error': '文件过大（限 10MB）'}), 400
    try:
        text = content.decode('utf-8', 'replace')
    except Exception:
        text = ''
    if 'youtube.com' not in text and not re.search(r'\.youtube\.com|\.google\.com', text):
        return jsonify({'error': '文件内容不像 YouTube cookie（未找到 youtube.com 域名）'}), 400
    with open(YOUTUBE_COOKIES, 'w', encoding='utf-8') as fh:
        fh.write(text)
    return jsonify({'ok': True, 'size': len(content)})


@app.route('/api/retry', methods=['POST'])
def api_retry():
    data = request.get_json() or {}
    task_id = data.get('task_id', '')
    t = tasks.get(task_id)
    if not t:
        return jsonify({'error': '任务不存在'}), 404
    url = t.get('url', '')
    if not url:
        return jsonify({'error': '任务无原始链接'}), 400
    new_id = f"t{int(time.time() * 1000)}"
    with lock:
        tasks[new_id] = {
            'id': new_id, 'status': 'queued', 'progress': 0,
            'step': '重试排队中...', 'title': t.get('title', '处理中...'),
            'video_id': '', 'video_file': '', 'logs': [], 'url': url,
            'created_at': time.time(), 'retry_of': task_id,
            'duration': None, 'subtitle_count': 0,
        }
        save_tasks()
    th = threading.Thread(target=run_task, args=(new_id, url), daemon=True)
    th.start()
    return jsonify({'task_id': new_id})


@app.route('/api/clear', methods=['POST'])
def api_clear():
    data = request.get_json() or {}
    only_finished = data.get('only_finished', True)
    with lock:
        for k in list(tasks.keys()):
            if only_finished and tasks[k].get('status') not in ('done', 'error'):
                continue
            del tasks[k]
        save_tasks()
    return jsonify({'ok': True, 'remaining': len(tasks)})


def _safe_path(root, filename):
    """校验文件路径落在 root 目录内，防止路径穿越"""
    r = os.path.realpath(root)
    p = os.path.realpath(os.path.join(r, filename))
    if os.path.commonpath([p, r]) == r:
        return p
    return None


def delete_task_files(task_id):
    """删除任务关联的全部下载产物（视频/字幕/烧录成品），保留任务记录"""
    t = tasks.get(task_id)
    if not t:
        return [], '任务不存在'
    vid = t.get('video_id', '')
    if not vid:
        return [], '任务无视频 ID，无法定位文件'
    if t.get('files_deleted'):
        return [], '该任务文件已删除'

    deleted = []
    # downloads: 按 {vid}.* 前缀匹配（mp4/vtt/srt/jpg 等）
    for d, prefix in [(DOWNLOAD_DIR, vid), (SUBTITLE_DIR, vid + '_'), (FINAL_DIR, vid + '_')]:
        if not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for fn in names:
            if not fn.startswith(prefix):
                continue
            p = _safe_path(d, fn)
            if p and os.path.isfile(p):
                try:
                    os.remove(p)
                    deleted.append(p)
                except OSError:
                    pass

    with lock:
        t = tasks.get(task_id)
        if t:
            t['files_deleted'] = True
            t['logs'] = t.get('logs', []) + [f"[{time.strftime('%H:%M:%S')}] 🗑️ 已删除 {len(deleted)} 个任务文件"]
            save_tasks()
    return deleted, None


@app.route('/api/delete-files', methods=['POST'])
def api_delete_files():
    data = request.get_json() or {}
    task_id = data.get('task_id', '')
    deleted, err = delete_task_files(task_id)
    if err:
        return jsonify({'error': err}), 400
    return jsonify({
        'ok': True,
        'deleted': len(deleted),
        'files': [os.path.basename(x) for x in deleted],
    })


if __name__ == '__main__':
    load_tasks()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    print(f"yt2bili Web 监控面板: http://127.0.0.1:{port}")
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
