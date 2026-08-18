#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yt2bili Web 监控面板 — 粘贴 YouTube 链接, 一键下载→翻译→上传B站
含任务持久化、统计、服务器资源监控、失败重试。
"""
import os, sys, json, time, subprocess, threading, re, shutil, urllib.request, urllib.parse, io, base64, http.cookiejar, hashlib
from pathlib import Path
from flask import Flask, render_template, request, jsonify, make_response

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
    cfg = {'proxy': '', 'youtube_cookies': '', 'ollama_url': 'http://127.0.0.1:11434', 'ollama_model': 'qwen2.5:7b'}
    if os.path.exists(CONFIG):
        with open(CONFIG) as f:
            for line in f:
                if line.startswith('proxy:'):
                    cfg['proxy'] = line.split(':', 1)[1].strip().strip('"')
    if os.path.exists(YOUTUBE_COOKIES):
        cfg['youtube_cookies'] = f'--cookies {YOUTUBE_COOKIES}'
    try:
        import yaml
        raw = yaml.safe_load(open(CONFIG))
        if isinstance(raw, dict):
            cfg['ollama_url'] = raw.get('ollama_url', cfg['ollama_url'])
            cfg['ollama_model'] = raw.get('ollama_model', cfg['ollama_model'])
    except Exception:
        pass
    return cfg


def run_cmd(cmd_list, timeout=300, log_func=None):
    proc = subprocess.run(cmd_list, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or 'unknown error')[-500:]
        if log_func: log_func(f"⚠️ {err}")
        raise Exception(f"命令失败: {err}")
    return proc.stdout


def run_cmd_with_cookies_fallback(cmd_list, ck_opt, timeout=300, log_func=None, fallback_client_opt=None):
    """带 cookies 执行；若失败（cookie 失效触发反爬等）自动去掉 cookies 重试一次。
    fallback_client_opt: 回退直连时追加的参数（如 android 客户端规避风控）"""
    try:
        return run_cmd(cmd_list + ck_opt, timeout=timeout, log_func=log_func)
    except Exception:
        if not ck_opt:
            raise
        if log_func:
            log_func("⚠️ YouTube cookies 可能已失效，自动改用直连重试...")
        return run_cmd(cmd_list + (fallback_client_opt or []), timeout=timeout, log_func=log_func)


def run_cmd_stream(cmd_list, timeout=300, log_func=None, progress_cb=None):
    """流式执行命令并逐行回调。stdout+stderr 合并，按 \\n 或 \\r 切行实时输出。

    progress_cb(line) 返回 True 表示该行是进度行（不写入日志）。
    """
    env = dict(os.environ)
    env['PYTHONUNBUFFERED'] = '1'
    proc = subprocess.Popen(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    out = bytearray()
    buf = b''
    deadline = time.time() + timeout
    try:
        while True:
            if time.time() > deadline:
                proc.kill()
                raise subprocess.TimeoutExpired(cmd_list, timeout)
            chunk = proc.stdout.read1(65536)
            if not chunk:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            buf += chunk
            while buf:
                i = buf.find(b'\n')
                j = buf.find(b'\r')
                if i == -1:
                    i = j
                elif j != -1:
                    i = min(i, j)
                if i == -1:
                    break
                raw = buf[:i]
                buf = buf[i + 1:]
                line = raw.decode('utf-8', 'replace')
                out += raw + b'\n'
                if not line.strip():
                    continue
                is_prog = False
                if progress_cb:
                    try:
                        is_prog = progress_cb(line)
                    except Exception:
                        is_prog = False
                if log_func and not is_prog:
                    log_func(line)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    proc.wait()
    if proc.returncode != 0:
        err = bytes(out)[-500:].decode('utf-8', 'replace')
        if log_func:
            log_func(f"⚠️ {err}")
        raise Exception(f"命令失败: {err}")
    return bytes(out).decode('utf-8', 'replace')


def run_cmd_with_cookies_fallback_stream(cmd_list, ck_opt, timeout=300, log_func=None, progress_cb=None, fallback_client_opt=None):
    """带 cookies 的流式版本；失败时自动去掉 cookies 重试一次
    fallback_client_opt: 回退直连时追加的参数（如 android 客户端规避风控）"""
    try:
        return run_cmd_stream(cmd_list + ck_opt, timeout=timeout, log_func=log_func, progress_cb=progress_cb)
    except Exception:
        if not ck_opt:
            raise
        if log_func:
            log_func("⚠️ YouTube cookies 可能已失效，自动改用直连重试...")
        return run_cmd_stream(cmd_list + (fallback_client_opt or []), timeout=timeout, log_func=log_func, progress_cb=progress_cb)


def ffprobe_duration(path):
    """返回视频时长（秒），失败返回 None"""
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', path],
            capture_output=True, text=True, timeout=30)
        d = json.loads(r.stdout or '{}')
        return float(d['format']['duration'])
    except Exception:
        return None


def _safe_remove(path):
    """尽力删除文件，失败静默"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


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
    android_opt = ['--extractor-args', 'youtube:player_client=android']
    # 有 cookies 时用默认客户端（android 不支持 cookies 会被跳过），cookies 失效回退时改用 android 规避风控
    if cfg.get('youtube_cookies'):
        client_opt = []
        fallback_client = android_opt
    else:
        client_opt = android_opt
        fallback_client = None

    try:
        # ===== 1. 获取视频信息（重试时复用已有信息，跳过联网）=====
        task['phase'] = '获取信息'
        vid = task.get('video_id', '')
        title = task.get('title', '')
        proxy_opt = ['--proxy', cfg['proxy']] if cfg['proxy'] else []
        ck_opt = cfg['youtube_cookies'].split() if cfg['youtube_cookies'] else []
        if vid and title and title != '处理中...':
            log(f"♻️ 复用已有视频信息: {title}")
        else:
            log("📋 获取视频信息...")
            meta_out = run_cmd_with_cookies_fallback(
                [YTBIN, '--print-json', '--skip-download'] + ejs_opt + client_opt + proxy_opt + [url],
                ck_opt, timeout=30, log_func=log, fallback_client_opt=fallback_client)
            info = json.loads(meta_out)
            vid = info['id']
            title = info['title']
            task['video_id'] = vid
            log(f"✅ 视频: {title}")
            translated = ollama_translate_title(title, cfg['ollama_url'], cfg['ollama_model'])
            if translated != title:
                task['title'] = translated
                log(f"🌐 标题翻译: {title} → {translated}")
            else:
                task['title'] = title

        # ===== 2. 下载（已有视频文件则跳过）=====
        task['phase'] = '下载'
        video_file = task.get('video_file', '')
        if video_file and os.path.exists(video_file) and os.path.getsize(video_file) > 0:
            log(f"♻️ 已存在视频文件，跳过下载: {os.path.basename(video_file)}")
            task['step'] = '已跳过下载'
            task['progress'] = 30
        else:
            task['step'] = '下载视频与字幕'
            task['progress'] = 10
            log("⬇️ 下载视频和英文字幕...")
            t_dl = time.time()
            dl_state = {'last': 0.0}

            def dl_cb(line):
                m = re.search(r'\[download\]\s+([\d.]+)%', line)
                if not m:
                    return False
                pct = float(m.group(1))
                if pct < dl_state['last'] - 1:      # 换文件了，进度重新开始
                    dl_state['last'] = 0.0
                dl_state['last'] = max(dl_state['last'], pct)
                task['progress'] = round(10 + dl_state['last'] / 100 * 20, 1)
                task['step'] = f"下载视频与字幕 {dl_state['last']:.1f}%"
                return True

            run_cmd_with_cookies_fallback_stream(
                [YTBIN] + ejs_opt + client_opt + proxy_opt + [
                '-f', 'bv*[height<=1080]+ba/b[height<=1080]/b',
                '--write-subs', '--write-auto-subs', '--sub-langs', 'en',
                '--embed-subs', '--embed-thumbnail',
                '-o', f'{DOWNLOAD_DIR}/%(id)s.%(ext)s', url],
                ck_opt, timeout=300, log_func=log, progress_cb=dl_cb, fallback_client_opt=fallback_client)
            task['duration_download'] = round(time.time() - t_dl, 1)
            log("✅ 下载完成")
            task['progress'] = 30

        # 列出字幕文件方便诊断
        vtts = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(vid) and f.endswith('.vtt')]
        if vtts:
            log(f"📄 字幕文件: {', '.join(vtts)}")
        else:
            log("⚠️ 未找到字幕文件（视频可能无英文字幕）")

        video_file = None
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(vid) and (f.endswith('.mp4') or f.endswith('.webm')):
                video_file = os.path.join(DOWNLOAD_DIR, f); break
        if not video_file:
            mp4s = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.mp4')]
            if mp4s: video_file = os.path.join(DOWNLOAD_DIR, mp4s[0])
        if not video_file:
            raise Exception("未找到视频文件")
        task['video_file'] = video_file
        log(f"📁 视频: {os.path.basename(video_file)}")

        # ===== 3. 翻译（已有双语字幕则跳过）=====
        task['phase'] = '翻译'
        output_srt = os.path.join(SUBTITLE_DIR, f'{vid}_bilingual_full.srt')
        if os.path.exists(output_srt) and os.path.getsize(output_srt) > 0:
            log(f"♻️ 已存在双语字幕，跳过翻译: {os.path.basename(output_srt)}")
            if not task.get('subtitle_count'):
                with open(output_srt, encoding='utf-8') as f:
                    task['subtitle_count'] = len(re.findall(r'\d+\n\d{2}:\d{2}:\d{2}', f.read()))
            en_sub = None
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(vid) and f.endswith('.en.vtt'):
                    en_sub = os.path.join(DOWNLOAD_DIR, f); break
        else:
            task['step'] = '翻译 (本地 qwen2.5, 免费无限)'
            task['progress'] = 40
            log("🌐 AI 双语翻译中...")
            task['subtitle_count'] = 0

            en_sub = None
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(vid) and f.endswith('.en.vtt'):
                    en_sub = os.path.join(DOWNLOAD_DIR, f); break
            if not en_sub:
                vtts = [os.path.join(DOWNLOAD_DIR, f) for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.vtt')]
                if vtts: en_sub = vtts[0]

            if en_sub:
                t_tr = time.time()

                def tr_cb(line):
                    m = re.search(r'\[(\d+)/(\d+)\]', line)
                    if not m:
                        return False
                    try:
                        cur, tot = int(m.group(1)), int(m.group(2))
                        pct = cur / tot * 100 if tot else 0
                    except (ValueError, ZeroDivisionError):
                        return False
                    task['progress'] = round(40 + pct / 100 * 30, 1)
                    task['step'] = f"翻译中 {pct:.1f}%"
                    return True

                out = run_cmd_stream([PYTHON, TRANSLATE_PY, en_sub, output_srt, CONFIG],
                                     timeout=3600, log_func=log, progress_cb=tr_cb)
                task['duration_translate'] = round(time.time() - t_tr, 1)
                m = re.search(r'完成:\s*(\d+)/(\d+)', out or '')
                if m:
                    task['subtitle_count'] = int(m.group(1))
                log("✅ 翻译完成")
            else:
                log("⚠️ 无英文字幕, 跳过")
                output_srt = None
        task['progress'] = 70

        # ===== 4. 烧录字幕（已有烧录成品则跳过）=====
        task['phase'] = '烧录字幕'
        task['step'] = '烧录字幕'
        task['progress'] = 75
        final_video = os.path.join(FINAL_DIR, f'{vid}_final_bilingual.mp4')
        video_to_upload = video_file
        if os.path.exists(final_video) and os.path.getsize(final_video) > 0:
            log(f"♻️ 已存在烧录成品，跳过烧录: {os.path.basename(final_video)}")
            video_to_upload = final_video
        elif output_srt and os.path.exists(output_srt):
            log("🎬 烧录字幕...")
            total_us = None
            dur = ffprobe_duration(video_file)
            if dur:
                total_us = dur * 1e6
            burn_cmd = ['ffmpeg', '-y', '-nostats', '-i', video_file,
                       '-vf', f"subtitles={output_srt}:force_style='FontName=DejaVu Sans,FontSize=18,MarginV=40'",
                       '-c:a', 'copy', '-progress', 'pipe:1', final_video]

            def burn_cb(line):
                m = re.search(r'out_time_us=(\d+)', line)
                if not m:
                    return False
                if not total_us:
                    return True
                pct = min(100.0, int(m.group(1)) / total_us * 100)
                task['progress'] = round(75 + pct / 100 * 10, 1)
                task['step'] = f"烧录字幕 {pct:.1f}%"
                return True

            try:
                run_cmd_stream(burn_cmd, timeout=900, log_func=log, progress_cb=burn_cb)
                if os.path.exists(final_video) and os.path.getsize(final_video) > 0:
                    log("✅ 烧录完成")
                    video_to_upload = final_video
                else:
                    log("⚠️ 烧录未生成有效文件，将上传原视频（无字幕）")
            except subprocess.TimeoutExpired:
                log(f"❌ 烧录超时({900}s)，清理不完整产物，将上传原视频（无字幕）")
                _safe_remove(final_video)
            except Exception as e:
                log(f"⚠️ 烧录失败: {str(e)[:120]}，将上传原视频（无字幕）")
                _safe_remove(final_video)
        task['progress'] = 85

        # ===== 5. 上传 =====
        task['phase'] = '上传 B 站'
        task['step'] = '上传 B 站'
        task['progress'] = 90
        log("⬆️ 上传到 Bilibili...")
        bili_title = f"{title[:40]} - 双语字幕"
        desc = f"原视频: {url}\n\nAI 双语字幕由本地模型 qwen2.5:7b 生成 (免费无限🚀)"
        t_up = time.time()
        up_state = {'last': 0.0}

        def up_cb(line):
            m = re.search(r'=>\s*([\d.]+)%', line)
            if not m:
                return False
            pct = float(m.group(1))
            up_state['last'] = max(up_state['last'], pct)
            task['progress'] = round(90 + up_state['last'] / 100 * 10, 1)
            task['step'] = f"上传 B 站 {up_state['last']:.1f}%"
            return True

        run_cmd_stream([PYTHON, UPLOAD_PY,
            '--video', video_to_upload,
            '--cookie', COOKIES,
            '--title', bili_title[:80],
            '--desc', desc[:1000],
            '--tid', '171',
            '--tags', 'AI,科技',
            '--source', url], timeout=1800, log_func=log, progress_cb=up_cb)
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
        msg = str(e)[:200]
        if 'confirm' in msg.lower() and 'bot' in msg.lower():
            log("🔑 当前服务器 IP 被 YouTube 风控，需要有效的 YouTube cookies")
            log("💡 请用浏览器扩展（如 Get cookies.txt）导出已登录 YouTube 的 cookies 并上传")
        log(f"❌ 错误: {msg}")

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


@app.route('/api/export-log')
def api_export_log():
    """导出单个任务完整日志为 .txt 附件"""
    task_id = request.args.get('task_id', '')
    t = tasks.get(task_id)
    if not t:
        return jsonify({'error': '任务不存在'}), 404
    lines = []
    lines.append('===== Y2B 任务日志导出 =====')
    lines.append(f'任务ID: {task_id}')
    lines.append(f'标题: {t.get("title", "")}')
    lines.append(f'状态: {t.get("status", "")}')
    lines.append(f'链接: {t.get("url", "")}')
    if t.get('video_id'):
        lines.append(f'视频ID: {t["video_id"]}')
    if t.get('video_file'):
        lines.append(f'视频文件: {t["video_file"]}')
    if t.get('subtitle_count'):
        lines.append(f'字幕数: {t["subtitle_count"]}')
    if t.get('duration'):
        lines.append(f'用时: {t["duration"]}s')
    if t.get('created_at'):
        lines.append(f'创建时间: {time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t["created_at"]))}')
    lines.append('')
    lines.append('----- 日志 -----')
    lines.extend(t.get('logs', []))
    text = '\n'.join(lines) + '\n'
    fname = f'y2b_{task_id}.txt'
    resp = make_response(text)
    resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
    resp.headers['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp


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
        'proxy': read_config()['proxy'],
    })


@app.route('/api/proxy', methods=['GET', 'POST'])
def api_proxy():
    """读取 / 保存下载代理配置（仅写 config.yaml 的 proxy 行，不碰系统设置）"""
    if request.method == 'GET':
        return jsonify({'proxy': read_config()['proxy']})
    data = request.get_json(silent=True) or {}
    proxy = (data.get('proxy') or '').strip()
    if proxy and not re.match(r'^(https?|socks4|socks5)://', proxy):
        return jsonify({'error': '代理地址需以 http://、https://、socks4:// 或 socks5:// 开头'}), 400
    try:
        lines = []
        if os.path.exists(CONFIG):
            with open(CONFIG, encoding='utf-8') as f:
                lines = f.readlines()
        found = False
        for i, ln in enumerate(lines):
            if re.match(r'\s*proxy\s*:', ln):
                lines[i] = f'proxy: "{proxy}"\n'
                found = True
                break
        if not found:
            lines.append(f'proxy: "{proxy}"\n')
        tmp = CONFIG + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        os.replace(tmp, CONFIG)
        return jsonify({'ok': True, 'proxy': proxy})
    except Exception as e:
        return jsonify({'error': f'保存失败: {e}'}), 500


# ============================================================
# Cookie 在线登录 / 上传 (B站 TV 登录接口，带 appkey+sign)
# ============================================================
BILI_LOGIN_STATE = {}   # auth_code -> {jar, created_at}

BILI_APPKEY = '4409e2ce8ffd12b8'
BILI_APPSEC = '59b43e04ad6965f34319062b478f83dd'


def _atomic_write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def ollama_translate_title(text, ollama_url, model):
    try:
        prompt = f"将以下英文视频标题翻译成中文，保持专业术语和专有名词不翻译，只返回翻译结果不要多余内容：\n\n{text}"
        req = urllib.request.Request(
            f'{ollama_url}/api/generate',
            data=json.dumps({'model': model, 'prompt': prompt, 'stream': False, 'options': {'temperature': 0.1}}).encode(),
            headers={'Content-Type': 'application/json'})
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        translated = resp.get('response', '').strip().strip('"').strip("'")
        return translated if translated else text
    except Exception:
        return text


def _bili_sign(params):
    return hashlib.md5(f"{urllib.parse.urlencode(params)}{BILI_APPSEC}".encode()).hexdigest()


def _bili_tv_post(path, data, jar):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    data['ts'] = int(time.time())
    data['sign'] = _bili_sign(data)
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        f'https://passport.bilibili.com/x/passport-tv-login/{path}',
        data=body,
        headers={'User-Agent': UA, 'Content-Type': 'application/x-www-form-urlencoded'})
    raw = opener.open(req, timeout=10).read()
    return json.loads(raw), jar


@app.route('/api/bili-login/start', methods=['POST'])
def bili_login_start():
    """调用 B 站 TV 登录接口生成二维码"""
    jar = http.cookiejar.CookieJar()
    try:
        data, jar = _bili_tv_post('qrcode/auth_code', {
            'appkey': BILI_APPKEY,
            'local_id': '0',
        }, jar)
    except Exception as e:
        return jsonify({'error': f'获取二维码失败: {str(e)[:100]}'}), 502
    if data.get('code') != 0:
        return jsonify({'error': f"B站返回: {data.get('message', data.get('code'))}"}), 502

    auth_code = data['data']['auth_code']
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
    for k in list(BILI_LOGIN_STATE):
        if now - BILI_LOGIN_STATE[k]['created_at'] > 600:
            BILI_LOGIN_STATE.pop(k, None)
    BILI_LOGIN_STATE[auth_code] = {'jar': jar, 'created_at': now}
    return jsonify({'key': auth_code, 'qr': qr_b64, 'url': qurl})


@app.route('/api/bili-login/status')
def bili_login_status():
    auth_code = request.args.get('key', '')
    st = BILI_LOGIN_STATE.get(auth_code)
    if not st:
        return jsonify({'status': 'expired', 'message': '登录会话已失效'}), 404
    try:
        data, _ = _bili_tv_post('qrcode/poll', {
            'appkey': BILI_APPKEY,
            'auth_code': auth_code,
            'local_id': '0',
        }, st['jar'])
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)[:100]})
    if data.get('code') != 0:
        return jsonify({'status': 'pending', 'message': '等待扫码'})
    inner = data.get('data') or {}

    # 从响应体提取 cookie（B站 TV 登录的 cookie 在 JSON body 的 cookie_info 中，
    # 不在 Set-Cookie 头，所以 CookieJar 提取始终为空）
    # 参考 biliup 源码 login_by_password 方法确认此行为
    cookie_dict = {}
    for c in inner.get('cookie_info', {}).get('cookies', []):
        if c.get('name') and c.get('value'):
            cookie_dict[c['name']] = c['value']

    # 补充来自 CookieJar 的 cookie（部分旧版接口可能也通过 Set-Cookie 设置）
    for c in st['jar']:
        if c.name and c.value and c.name not in cookie_dict:
            cookie_dict[c.name] = c.value

    if not cookie_dict:
        return jsonify({'status': 'error', 'message': '登录成功但未获取到 Cookie（会话异常），请重新扫码'})

    token_info = {
        'mid': inner.get('mid', ''),
        'access_token': inner.get('access_token', ''),
        'refresh_token': inner.get('refresh_token', ''),
        'expires_in': inner.get('expires_in', 0),
    }
    out = {
        'cookie_info': {
            'cookies': [{'name': k, 'value': v} for k, v in cookie_dict.items()],
            'domains': ['.bilibili.com', '.biliapi.net'],
        },
        'token_info': token_info,
    }
    if token_info.get('refresh_token'):
        out['refresh_token'] = token_info['refresh_token']
    _atomic_write_json(COOKIES, out)
    BILI_LOGIN_STATE.pop(auth_code, None)
    return jsonify({'status': 'ok', 'message': '登录成功', 'user': cookie_dict.get('DedeUserID', '')})


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
            'video_id': t.get('video_id', ''), 'video_file': t.get('video_file', ''),
            'logs': [], 'url': url, 'created_at': time.time(), 'retry_of': task_id,
            'duration': None, 'subtitle_count': t.get('subtitle_count', 0),
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
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
