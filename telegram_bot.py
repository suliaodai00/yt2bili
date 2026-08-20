#!/usr/bin/env python3
"""yt2bili Telegram Bot 辅助模块"""
import os, sys, json, time, threading, urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE, 'output', 'tasks.json')
CONFIG = os.path.join(BASE, 'config.yaml')

def _read_config():
    cfg = {}
    if os.path.exists(CONFIG):
        try:
            import yaml
            raw = yaml.safe_load(open(CONFIG, encoding='utf-8'))
            if isinstance(raw, dict): return raw
        except Exception:
            pass
        with open(CONFIG, encoding='utf-8') as f:
            for line in f:
                if ':' in line:
                    k, v = line.split(':', 1)
                    cfg[k.strip()] = v.strip().strip('"')
    return cfg

def _read_token():
    cfg = _read_config()
    token = cfg.get('telegram_bot_token', '')
    if not token:
        token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    return token

def _read_chat_id():
    cfg = _read_config()
    cid = cfg.get('telegram_chat_id', '')
    if cid:
        try:
            return int(cid)
        except ValueError:
            pass
    return None

def _save_chat_id(chat_id):
    try:
        content = ""
        if os.path.exists(CONFIG):
            with open(CONFIG, encoding='utf-8') as f:
                content = f.read()
        if 'telegram_chat_id:' in content:
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if line.startswith('telegram_chat_id:'):
                    lines[i] = f'telegram_chat_id: {chat_id}'
                    break
            content = '\n'.join(lines)
        else:
            content += f'\n# Telegram 通知目标 chat_id（bot 自动记录）\ntelegram_chat_id: {chat_id}\n'
        with open(CONFIG, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"[Telegram] 已记录并更新 telegram_chat_id: {chat_id}")
    except Exception as e:
        print(f"[Telegram] 保存 chat_id 异常: {e}")

def send_notification(message):
    """直接使用 HTTP 发送通知，不依赖 asyncio 事件循环"""
    token = _read_token()
    chat_id = _read_chat_id()
    if not token or not chat_id:
        print(f"[Telegram] 未配置 token 或 chat_id，跳过发送")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read())
            return res.get("ok", False)
    except Exception as e:
        print(f"[Telegram] 发送通知异常: {e}")
        return False

def run_bot():
    """启动独立 Telegram Bot 进程"""
    import subprocess
    token = _read_token()
    if not token:
        return
    try:
        subprocess.run(['pkill', '-f', 'telegram_bot_runner.py'], capture_output=True)
    except Exception:
        pass
    
    bot_script = os.path.join(BASE, 'telegram_bot_runner.py')
    py = os.path.join(BASE, '.venv', 'bin', 'python3')
    if not os.path.exists(py):
        py = sys.executable

    log_path = os.path.join(BASE, 'output', 'telegram_bot.log')
    with open(log_path, 'a') as log_file:
        proc = subprocess.Popen(
            [py, bot_script],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True
        )
    print(f"[Telegram] Bot 服务已在后台拉起 (PID {proc.pid})")
