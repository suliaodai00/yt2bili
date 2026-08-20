#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yt2bili Web 监控面板 — 粘贴 YouTube 链接, 一键下载→翻译→上传B站
含任务持久化、统计、服务器资源监控、失败重试。
"""
import os, sys, json, time, subprocess, threading, re, shutil, urllib.request, urllib.parse, io, base64, http.cookiejar, hashlib
import telegram_bot
from pathlib import Path
from flask import Flask, render_template, request, jsonify, make_response
from youtube_downloader import YouTubeDownloader, YouTubeError

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'

BASE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(BASE, '.venv')
PYTHON = os.path.join(VENV, 'bin/python3')
CONFIG = os.path.join(BASE, 'config.yaml')
COOKIES = os.path.join(BASE, 'cookies.json')
YOUTUBE_COOKIES = os.path.join(BASE, 'youtube_cookies.txt')
DOWNLOAD_DIR = os.path.join(BASE, 'output', 'downloads')
SUBTITLE_DIR = os.path.join(BASE, 'output', 'subtitles')
FINAL_DIR = os.path.join(BASE, 'output', 'final')
TRANSLATE_PY = os.path.join(BASE, 'translate.py')
TRANSCRIBE_PY = os.path.join(BASE, 'transcribe.py')
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
downloader = YouTubeDownloader(config_path=CONFIG)


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


def _stop_ollama_if_idle():
    """当无活跃任务时停止 Ollama 节省资源"""
    try:
        active = [t for t in tasks.values() if t.get('status') in ('running', 'queued')]
        if active:
            return
        out = subprocess.run(['pgrep', '-f', 'llama-server'], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            subprocess.run(['systemctl', 'stop', 'ollama'], timeout=30)
            print(f"[Ollama] 无活跃任务，已停止 Ollama 节省资源")
    except Exception as e:
        print(f"[Ollama] 停止失败: {e}")


def _start_ollama_if_needed():
    """有任务时启动 Ollama"""
    try:
        out = subprocess.run(['systemctl', 'is-active', 'ollama'], capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            subprocess.run(['systemctl', 'start', 'ollama'], timeout=30)
            print(f"[Ollama] 新任务，已启动 Ollama")
            time.sleep(3)
    except Exception as e:
        print(f"[Ollama] 启动失败: {e}")


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
        try:
            import yaml
            raw = yaml.safe_load(open(CONFIG, encoding='utf-8'))
            if isinstance(raw, dict):
                cfg['proxy'] = raw.get('proxy', '')
                cfg['ollama_url'] = raw.get('ollama_url', cfg['ollama_url'])
                cfg['ollama_model'] = raw.get('ollama_model', cfg['ollama_model'])
        except Exception:
            pass
    if os.path.exists(YOUTUBE_COOKIES):
        cfg['youtube_cookies'] = f'--cookies {YOUTUBE_COOKIES}'
    return cfg


def run_cmd_stream(cmd_list, timeout=300, log_func=None, progress_cb=None):
    """流式执行命令并逐行回调。"""
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


def ffprobe_duration(path):
    """返回视频时长（秒），失败返回 None"""
    try:
        out = subprocess.check_output([
            'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
            '-of', 'csv=p=0', path
        ], text=True, timeout=10).strip()
        return round(float(out))
    except Exception:
        return None


def run_task(task_id, url):
    global _active
    task = tasks[task_id]

    def log(msg):
        ts = time.strftime('%H:%M:%S')
        line = f"[{ts}] {msg}"
        with lock:
            task['logs'].append(line)
            print(f"[{task_id}] {line}")
            save_tasks()

    cfg = read_config()
    global downloader
    downloader = YouTubeDownloader(config_path=CONFIG)

    try:
        # ===== 1. 获取视频信息（复用或从 downloader 提取）=====
        task['phase'] = '获取信息'
        vid = task.get('video_id', '')
        title = task.get('title', '')
        if vid and title and title != '处理中...':
            log(f"♻️ 复用已有视频信息: {title}")
        else:
            log("📋 正在获取视频元数据 (通过 youtube_downloader)...")
            try:
                meta = downloader.get_metadata(url, log_func=log)
                vid = meta['id']
                title = meta['title']
                task['video_id'] = vid
                task['title'] = title
                log(f"✅ 视频标题: {title} (ID: {vid}, 认证模式: {meta.get('auth_mode_used', 'default')})")
                
                # 标题翻译
                translated = ollama_translate_title(title, cfg['ollama_url'], cfg['ollama_model'])
                if translated != title:
                    task['title'] = translated
                    log(f"🌐 标题翻译: {title} → {translated}")
                else:
                    task['title'] = title
            except YouTubeError as ye:
                raise Exception(f"获取视频信息失败: [{ye.code}] {ye.message}")

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
            log("⬇️ 正在下载最高画质视频和英文字幕...")
            t_dl = time.time()

            def dl_prog_cb(pdict):
                pct_str = pdict.get('percent', '').replace('%', '').strip()
                try:
                    pct = float(pct_str)
                    task['progress'] = round(10 + pct / 100 * 20, 1)
                    task['step'] = f"下载视频与字幕 {pct:.1f}%"
                except Exception:
                    pass

            try:
                dl_res = downloader.download(
                    url,
                    download_dir=DOWNLOAD_DIR,
                    subtitle_dir=SUBTITLE_DIR,
                    progress_cb=dl_prog_cb,
                    log_func=log
                )
                video_file = dl_res.get('video_file')
                if not video_file or not os.path.exists(video_file):
                    v_p = os.path.join(DOWNLOAD_DIR, f"{vid}.mp4")
                    if os.path.exists(v_p): video_file = v_p
            except YouTubeError as ye:
                raise Exception(f"YouTube 下载失败: [{ye.code}] {ye.message}")

            if not video_file or not os.path.exists(video_file):
                raise Exception("未找到下载后的视频文件")
                
            task['video_file'] = video_file
            task['duration_download'] = round(time.time() - t_dl, 1)
            log("✅ 视频及字幕下载完成")
            task['progress'] = 30

        # 列出英文字幕文件
        vtts = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(vid) and f.endswith('.vtt')]
        if vtts:
            log(f"📄 字幕文件: {', '.join(vtts)}")
        else:
            log("⚠️ 未找到官方/自动英文字幕（将尝试 Whisper 语音识别）")

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

            # Whisper 语音识别后备: 无字幕时自动生成
            if not en_sub:
                log("⚠️ 未找到字幕, 使用 Whisper 语音识别生成...")
                whisper_srt = os.path.join(SUBTITLE_DIR, f'{vid}_whisper.srt')
                try:
                    if os.path.exists(whisper_srt) and os.path.getsize(whisper_srt) > 0:
                        en_sub = whisper_srt
                        log("♻️ 复用已有 Whisper 字幕")
                    else:
                        run_cmd_stream(
                            [PYTHON, TRANSCRIBE_PY, video_file, SUBTITLE_DIR, VENV],
                            timeout=3600, log_func=log)
                        if os.path.exists(whisper_srt) and os.path.getsize(whisper_srt) > 0:
                            en_sub = whisper_srt
                            log("✅ Whisper 语音识别完成")
                        else:
                            log("⚠️ Whisper 识别失败, 未生成字幕文件")
                except Exception as e:
                    log(f"⚠️ Whisper 识别出错: {e}")

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

        # ===== 4. 压制字幕（已有成品视频则跳过）=====
        task['phase'] = '压制'
        task['step'] = '压制字幕中'
        final_video = os.path.join(FINAL_DIR, f'{vid}_final_bilingual.mp4')
        if os.path.exists(final_video) and os.path.getsize(final_video) > 0:
            log(f"♻️ 已存在压制好的视频，跳过压制: {os.path.basename(final_video)}")
            upload_target = final_video
            task['progress'] = 85
        elif output_srt and os.path.exists(output_srt) and os.path.getsize(output_srt) > 0:
            log("🎬 压制双语字幕到视频...")
            t_burn = time.time()
            burn_cmd = ['ffmpeg', '-y', '-nostats', '-i', video_file,
                        '-vf', f"subtitles={output_srt}:force_style='FontName=DejaVu Sans,FontSize=18,MarginV=40'",
                        '-c:a', 'copy',
                        '-progress', 'pipe:1',
                        final_video]

            # 获取视频总时长用于计算进度
            total_dur = ffprobe_duration(video_file)

            def burn_cb(line):
                if not total_dur:
                    return False
                m = re.search(r'out_time_ms=(\d+)', line)
                if not m:
                    return False
                cur_s = int(m.group(1)) / 1_000_000
                pct = min(cur_s / total_dur * 100, 99.9)
                task['progress'] = round(70 + pct / 100 * 15, 1)
                task['step'] = f"压制字幕 {pct:.1f}%"
                return True

            run_cmd_stream(burn_cmd, timeout=3600, log_func=log, progress_cb=burn_cb)
            task['duration_burn'] = round(time.time() - t_burn, 1)
            upload_target = final_video
            log(f"✅ 压制完成: {os.path.basename(final_video)}")
            task['progress'] = 85
        else:
            log("⚠️ 无双语字幕，直接使用原始视频上传")
            upload_target = video_file
            task['progress'] = 85

        # ===== 5. 上传B站 =====
        task['phase'] = '上传'
        task['step'] = '上传B站中'
        log("🚀 上传到 Bilibili...")
        t_up = time.time()
        bili_cfg = {'tid': 171}
        if os.path.exists(CONFIG):
            with open(CONFIG, encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith('tid:'):
                        try: bili_cfg['tid'] = int(line.split(':', 1)[1].strip())
                        except Exception: pass

        def up_cb(line):
            m = re.search(r'(\d+)%', line)
            if not m:
                return False
            pct = int(m.group(1))
            task['progress'] = round(85 + pct / 100 * 15, 1)
            task['step'] = f"上传中 {pct}%"
            return True

        # 从原始简介提取或留空
        desc_text = f"原视频链接: {url}\n由 yt2bili 自动下载、AI双语翻译并上传。"

        up_out = run_cmd_stream([
            PYTHON, UPLOAD_PY,
            '--video', upload_target,
            '--title', task['title'],
            '--desc', desc_text,
            '--tid', str(bili_cfg['tid']),
            '--cookies', COOKIES
        ], timeout=1800, log_func=log, progress_cb=up_cb)
        task['duration_upload'] = round(time.time() - t_up, 1)

        bvid = None
        for line in up_out.split('\n'):
            if 'BV' in line:
                m = re.search(r'(BV[a-zA-Z0-9]+)', line)
                if m: bvid = m.group(1)

        task['bvid'] = bvid
        task['status'] = 'done'
        task['phase'] = '完成'
        task['step'] = '全部完成'
        task['progress'] = 100
        dur_msg = f" (耗时: 下载 {task.get('duration_download','-')}s / 翻译 {task.get('duration_translate','-')}s / 压制 {task.get('duration_burn','-')}s / 上传 {task.get('duration_upload','-')}s)"
        log(f"🎉 全部完成! B站: {bvid or '已提交'}{dur_msg}")

    except Exception as e:
        task['status'] = 'error'
        task['error'] = str(e)
        task['step'] = f"失败: {str(e)[:50]}"
        log(f"❌ 错误: {e}")

    finally:
        with lock:
            _active = max(0, _active - 1)
            task['finished_at'] = time.time()
            task['duration'] = round(task['finished_at'] - task['created_at'], 1)
            save_tasks()
        _stop_ollama_if_idle()


def ollama_translate_title(text, ollama_url, model):
    """用本地 Ollama 模型将标题翻译为自然中文"""
    try:
        prompt = f"将以下英文视频标题翻译成中文，保持专业术语和专有名词不翻译，只返回翻译结果不要多余内容：\n\n{text}"
        req = urllib.request.Request(
            f'{ollama_url}/api/generate',
            data=json.dumps({"model": model, "prompt": prompt, "stream": False,
                             "options": {"temperature": 0.1, "num_predict": 100}}).encode(),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
        translated = resp.get('response', '').strip().strip('"').strip("'")
        return translated if translated else text
    except Exception:
        return text


@app.route('/api/system-status')
def api_system_status():
    """获取系统运行状态"""
    cpu = None
    try:
        out = subprocess.check_output(['top', '-bn1'], text=True, timeout=2)
        for line in out.split('\n'):
            if '%Cpu' in line or 'CPU:' in line:
                cpu = line.strip()
                break
    except Exception:
        pass

    loadavg = None
    try:
        with open('/proc/loadavg') as f:
            loadavg = f.read().strip()
    except Exception:
        pass

    mem = {}
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    mem['total'] = int(line.split()[1])
                elif line.startswith('MemAvailable:'):
                    mem['available'] = int(line.split()[1])
    except Exception:
        pass

    disk = {}
    try:
        st = os.statvfs(BASE)
        disk['total'] = st.f_blocks * st.f_frsize
        disk['free'] = st.f_bavail * st.f_frsize
    except Exception:
        pass

    # Ollama 探测
    ollama = {'online': False, 'model': '', 'error': ''}
    url = 'http://127.0.0.1:11434'
    if os.path.exists(CONFIG):
        try:
            import yaml
            raw = yaml.safe_load(open(CONFIG))
            if isinstance(raw, dict):
                url = raw.get('ollama_url', url)
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

    # YouTube 下载器状态
    pot_online = downloader.check_pot_provider()
    ff_exists, ff_count = downloader.check_firefox_profile()
    yt_status = {
        'pot_provider': 'online' if pot_online else 'offline',
        'firefox_profile': 'exists' if ff_exists else 'not_found',
        'firefox_cookie_count': ff_count,
        'cookies_txt': os.path.exists(YOUTUBE_COOKIES),
        'strategies': [s[0] for s in downloader.get_auth_strategy()]
    }

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
        'youtube': yt_status,
        'uptime': uptime,
        'ts': time.time(),
        'active': _active,
    }


@app.route('/')
def index():
    return render_template('index.html')


def start_new_task(url):
    if 'youtube.com/watch' not in url and 'youtu.be/' not in url:
        return None
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
    _start_ollama_if_needed()
    t = threading.Thread(target=run_task, args=(task_id, url), daemon=True)
    t.start()
    return task_id


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
    _start_ollama_if_needed()
    t = threading.Thread(target=run_task, args=(task_id, url), daemon=True)
    t.start()
    return jsonify({'task_id': task_id})


@app.route('/status')
def get_status():
    t = tasks.get(request.args.get('task_id', ''))
    return jsonify(t or {'error': 'not found'}), (404 if not t else 200)


@app.route('/api/tasks')
def api_tasks():
    with lock:
        lst = list(tasks.values())
    lst.sort(key=lambda x: x.get('created_at', 0), reverse=True)
    return jsonify(lst[:MAX_TASKS])


@app.route('/api/test-youtube', methods=['POST'])
def api_test_youtube():
    """测试 YouTube 下载连通性 (只读取 metadata, 不下载视频)"""
    data = request.get_json() or {}
    test_url = data.get('url', 'https://www.youtube.com/watch?v=_onfQRKB1JY')
    try:
        meta = downloader.get_metadata(test_url)
        return jsonify({
            'ok': True,
            'title': meta.get('title'),
            'id': meta.get('id'),
            'auth_mode': meta.get('auth_mode_used'),
            'message': 'YouTube 连接与元数据读取正常'
        })
    except YouTubeError as ye:
        return jsonify({
            'ok': False,
            'code': ye.code,
            'message': ye.message,
            'details': ye.raw_error[-200:]
        }), 500


@app.route('/api/cookies-status')
def api_cookies_status():
    """返回账号与 cookie 状态"""
    def _f(name, path):
        if not os.path.exists(path):
            return {'name': name, 'exists': False, 'valid': False, 'msg': '未找到 cookie 文件'}
        try:
            sz = os.path.getsize(path)
            if sz == 0:
                return {'name': name, 'exists': True, 'valid': False, 'msg': '文件为空'}
            return {'name': name, 'exists': True, 'valid': True, 'msg': f'文件有效 ({round(sz/1024, 1)} KB)'}
        except Exception as e:
            return {'name': name, 'exists': True, 'valid': False, 'msg': str(e)}

    pot_online = downloader.check_pot_provider()
    ff_exists, ff_count = downloader.check_firefox_profile()

    return jsonify({
        'youtube': _f('YouTube (Fallback)', YOUTUBE_COOKIES),
        'bilibili': _f('Bilibili', COOKIES),
        'pot_provider': {'online': pot_online, 'url': downloader.pot_provider_url},
        'firefox_profile': {'exists': ff_exists, 'cookie_count': ff_count, 'path': downloader.browser_profile},
        'proxy': downloader.proxy,
    })


@app.route('/api/upload-yt-cookie', methods=['POST'])
def api_upload_yt_cookie():
    if 'file' not in request.files:
        return jsonify({'error': '未选择文件'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': '文件名为空'}), 400
    file.save(YOUTUBE_COOKIES)
    return jsonify({'ok': True, 'message': 'YouTube cookie 文件已更新'})


@app.route('/api/proxy', methods=['GET', 'POST'])
def api_proxy():
    if request.method == 'POST':
        data = request.get_json() or {}
        p = data.get('proxy', '').strip()
        try:
            import yaml
            cfg_obj = {}
            if os.path.exists(CONFIG):
                cfg_obj = yaml.safe_load(open(CONFIG, encoding='utf-8')) or {}
            cfg_obj['proxy'] = p
            with open(CONFIG, 'w', encoding='utf-8') as f:
                yaml.safe_dump(cfg_obj, f, allow_unicode=True)
            downloader.proxy = p
            return jsonify({'ok': True, 'proxy': p})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        return jsonify({'proxy': downloader.proxy})


@app.route('/api/clear-tasks', methods=['POST'])
def api_clear_tasks():
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
    r = os.path.realpath(root)
    p = os.path.realpath(os.path.join(r, filename))
    if os.path.commonpath([p, r]) == r:
        return p
    return None


def delete_task_files(task_id):
    t = tasks.get(task_id)
    if not t:
        return [], '任务不存在'
    vid = t.get('video_id', '')
    if not vid:
        return [], '任务无视频 ID，无法定位文件'
    if t.get('files_deleted'):
        return [], '该任务文件已删除'

    deleted = []
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


@app.route('/api/clear-all', methods=['POST'])
def api_clear_all():
    removed = 0
    for d in (DOWNLOAD_DIR, SUBTITLE_DIR, FINAL_DIR):
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            p = _safe_path(d, fn)
            if p and os.path.isfile(p):
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass

    task_count = len(tasks)
    with lock:
        tasks.clear()
        save_tasks()

    return jsonify({
        'ok': True,
        'removed': removed,
        'tasks_cleared': task_count,
        'kept': ['config.yaml', 'cookies.json', 'youtube_cookies.txt'],
    })


@app.route('/api/tg-token', methods=['GET', 'POST'])
def api_tg_token():
    if request.method == 'POST':
        data = request.get_json() or {}
        token = data.get('token', '').strip()
        if not token:
            return jsonify({'error': 'Token 不能为空'}), 400
        try:
            with open(CONFIG, encoding='utf-8') as f:
                content_cfg = f.read()
            if 'telegram_bot_token:' in content_cfg:
                lines = content_cfg.split('\n')
                for i, line in enumerate(lines):
                    if line.startswith('telegram_bot_token:'):
                        lines[i] = f'telegram_bot_token: {token}'
                        break
                content_cfg = '\n'.join(lines)
            else:
                content_cfg += f'\ntelegram_bot_token: {token}\n'
            with open(CONFIG, 'w', encoding='utf-8') as f:
                f.write(content_cfg)
        except Exception as e:
            return jsonify({'error': f'写入失败: {e}'}), 500

        try:
            telegram_bot.run_bot()
        except Exception:
            pass
        return jsonify({'ok': True, 'token_set': True, 'running': True, 'message': 'Token 已保存，Bot 已重启'})
    else:
        token = telegram_bot._read_token()
        chat_id = telegram_bot._read_chat_id()
        running = False
        bot_name = None
        try:
            out = subprocess.check_output(['pgrep', '-f', 'telegram_bot_runner']).decode().strip()
            running = bool(out)
        except Exception:
            running = False
        return jsonify({
            'token_set': bool(token),
            'running': running,
            'bot_name': bot_name,
            'users': 1 if chat_id else 0,
            'token_preview': token[:10] + '...' if token else None,
        })


if __name__ == '__main__':
    load_tasks()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    print(f"yt2bili Web 监控面板: http://127.0.0.1:{port}")
    telegram_bot.set_start_task_callback(start_new_task)
    telegram_bot.run_bot()
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
