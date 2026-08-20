#!/usr/bin/env python3
"""yt2bili Telegram Bot — 私聊发 YouTube 链接自动下载翻译上传"""
import os, sys, json, time, threading, re, urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE, 'output', 'tasks.json')
CONFIG = os.path.join(BASE, 'config.yaml')

# 用户白名单: None = 允许所有人
ALLOWED_USERS = None

_start_task_callback = None

def set_start_task_callback(cb):
    global _start_task_callback
    _start_task_callback = cb

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

# === 同步直接 HTTP 发送通知 (不依赖 asyncio 运行环境，最稳定) ===
def send_notification(message):
    token = _read_token()
    chat_id = _read_chat_id()
    if not token or not chat_id:
        print(f"[Telegram] 未配置 token ({bool(token)}) 或 chat_id ({bool(chat_id)})，跳过通知")
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
        print(f"[Telegram] HTTP 发送通知异常: {e}")
        return False

# === 启动 Bot 守护进程 ===
def start_bot_sync():
    import subprocess
    token = _read_token()
    if not token:
        print("[Telegram] 未配置 token，跳过 bot 启动")
        return

    bot_script = os.path.join(BASE, 'telegram_bot_runner.py')
    with open(bot_script, 'w', encoding='utf-8') as f:
        f.write(f'''#!/usr/bin/env python3
import asyncio, sys, re, json, urllib.request
sys.path.insert(0, "{BASE}")
from telegram_bot import _read_token, _read_chat_id, _save_chat_id, DATA_FILE
token = _read_token()
if not token:
    print("No token")
    sys.exit(1)

from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram import Update

async def cmd_start(update: Update, context):
    chat_id = update.effective_chat.id
    _save_chat_id(chat_id)
    await update.message.reply_text(
        "🤖 *yt2bili Bot 已就绪*\\n\\n"
        "已将当前会话绑定为通知接收目标！\\n\\n"
        "直接发送 YouTube 链接，自动执行：\\n"
        "⬇️ 下载 → 🌐 双语翻译 → 🎬 压制 → 🚀 上传 B 站\\n\\n"
        "命令：\\n"
        "/status — 查看任务队列与统计",
        parse_mode='Markdown'
    )

async def cmd_status(update: Update, context):
    chat_id = update.effective_chat.id
    _save_chat_id(chat_id)
    try:
        with open("{DATA_FILE}", encoding='utf-8') as f:
            tasks = json.load(f)
    except Exception:
        tasks = {{}}
    running = sum(1 for t in tasks.values() if t.get('status') == 'running')
    queued = sum(1 for t in tasks.values() if t.get('status') == 'queued')
    done = sum(1 for t in tasks.values() if t.get('status') == 'done')
    err = sum(1 for t in tasks.values() if t.get('status') == 'error')
    await update.message.reply_text(
        f"📊 *任务队列状态*\\n"
        f"⏳ 排队: {{queued}} | 🏃 运行: {{running}}\\n"
        f"✅ 成功: {{done}} | ❌ 失败: {{err}}",
        parse_mode='Markdown'
    )

async def cmd_msg(update: Update, context):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    _save_chat_id(chat_id)
    yt = r'(https?:\\/\\/(?:www\\.)?(?:youtube\\.com\\/watch\\?v=|youtu\\.be\\/)[\\w-]+)'
    m = re.search(yt, text)
    if not m:
        await update.message.reply_text("❌ 请发送有效的 YouTube 视频链接")
        return
    url = m.group(1)
    req = urllib.request.Request(
        "http://127.0.0.1:5000/start",
        data=json.dumps({{"url": url}}).encode('utf-8'),
        headers={{"Content-Type": "application/json"}},
        method='POST'
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        tid = data.get('task_id', '')
        await update.message.reply_text(
            f"✅ *已成功创建任务*\\n"
            f"🆔 `{tid}`\\n\\n"
            f"🚀 已进入自动化工作流，全部完成时会在此通知你！",
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(f"❌ 任务创建失败: {{e}}")

app = Application.builder().token(token).build()
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("status", cmd_status))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_msg))
print("[Telegram] Bot 启动成功，正在轮询...")
app.run_polling(allowed_updates=Update.ALL_TYPES)
''')

    proc = subprocess.Popen(
        [sys.executable, bot_script],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True
    )
    print(f"[Telegram] Bot 进程已启动 (PID {proc.pid})")

def run_bot():
    t = threading.Thread(target=start_bot_sync, daemon=True)
    t.start()
    return t
